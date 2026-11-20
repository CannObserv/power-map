"""Egress-IP drift guard (issue #410) — is our source address still allowlisted?

Wired to ``infra/power-map-egress-ip.timer`` (every 5 minutes). Exists because
on 2026-08-09 the VM's NAT egress IP rotated ``67.213.124.9`` ->
``69.67.149.183`` with no VM-side change: no network events, no config change,
no terraform run. The DigitalOcean cluster gates on source IP
(``digitalocean_database_firewall`` <- ``var.allowed_external_ips``), so every
DB-backed route died at once and stayed dead for ~35 minutes.

This is the **cause** half of that outage. ``scripts/check_ready`` (#347) is
the effect half: it notices the database went away, within two minutes. This
one names *why*, and hands over the exact address to paste into Trusted
Sources — which is the whole of the fix.

The allowlist comes from the **DigitalOcean API** — the live Trusted Sources of
the cluster — whenever ``DO_API_TOKEN`` is present (#409 restored it). That is
authoritative, needs nothing kept in sync by hand, and catches a failure the
old hand-maintained copy could not see: our address being *removed* from the
list while our egress IP never changed.

``EGRESS_EXPECTED_IPS`` remains the fallback, for a host with no token or an
API that is briefly unreachable. A transient API failure (timeout, connection
error, 5xx, 429) is retried first, with a short back-off (#839).

Three judgements worth keeping:

- **Undeterminable is not drift.** If every echo service fails, the run logs a
  WARNING and exits 0. A third-party outage must not open an alert about our
  network — and the real failure would surface through ``/ready`` anyway.
- **A non-IP body is not an IP.** Echo services have been known to answer 200
  with an HTML error page; parsing that as an address would "drift" every run.
- **A fallback disagreement is not drift (#839).** With a token present, the
  fallback is consulted only because the API failed, and it is a hand-kept copy
  that goes stale silently. On 2026-11-19 a DO API outage plus a stale copy
  raised a drift alert for 85 minutes about an address already allowlisted. So a
  mismatch there is a WARNING at exit 0; ``/ready`` catches a real cut-off. On a
  host with *no* token the env var is the only list, and a mismatch still alerts.

Exit codes: 0 = matches (or nothing trustworthy to compare against); 3 = drift (systemd
marks the unit failed, so it shows in ``systemctl --failed``, and the unit's
co-status check-in alerts as ``drift``; #566). The new address is in the
journal WARNING, which the alert points at; it never leaves the host.
5 = unreadable (#833): the DO API refused for good (401, 403, 404, no such
cluster, a bad body). Judged by the cause, not by a count of runs: a refusal
answers the same way every time, so the guard says it is blind in the run that
finds out, even when the fallback happens to agree. A transient failure stays
quiet, as above.

Test hatch:
    EGRESS_CHECK_FORCE_FAIL=1 skip lookup and exercise the failure path.

Usage:
    uv run python -m scripts.check_egress_ip
    uv run python -m scripts.check_egress_ip --expected 69.67.149.183
"""

import argparse
import ipaddress
import os
import sys
from time import sleep
from urllib.error import HTTPError
from urllib.request import urlopen

from scripts._do_api import DEFAULT_CLUSTER, fetch_allowed_ips
from scripts._do_api import DEFAULT_TIMEOUT as DO_API_TIMEOUT
from src.core.logging import configure_logging, get_logger

logger = get_logger(__name__)

# Two independent providers: one outage must not blind the guard.
DEFAULT_SERVICES = ("https://api.ipify.org", "https://ifconfig.me/ip")
DEFAULT_TIMEOUT = 10.0


def lookup(services, *, timeout: float, opener=None) -> str | None:
    """Return this host's public egress IP, or None if nothing answered.

    Tries each service in turn. A response that is not a valid IP address is
    treated as a failure, not an answer — echo services have been known to
    return an HTML error page with a 200.
    """
    opener = opener or urlopen
    for service in services:
        try:
            with opener(service, timeout=timeout) as response:
                candidate = response.read().decode("utf-8", "replace").strip()
        except Exception:
            logger.info("egress lookup via %s failed", service)
            continue
        try:
            return str(ipaddress.ip_address(candidate))
        except ValueError:
            logger.info("egress lookup via %s returned a non-address body", service)
    return None


def parse_expected(raw: str) -> set[str]:
    """Parse a comma- or space-separated allowlist into a set."""
    return {part.strip() for part in raw.replace(",", " ").split() if part.strip()}


# DO API retry shape (#839). Worst case must fit the unit's TimeoutStartSec;
# test_worst_case_run_fits_the_units_start_timeout holds that.
API_RETRY_DELAYS = (2.0, 5.0)
API_ATTEMPTS = len(API_RETRY_DELAYS) + 1

# Sentinels for the second element of resolve_allowlist's return.
UNRESTRICTED = "unrestricted"
UNKNOWN = ""
# The env var, consulted because the API failed: a verdict, not an authority.
API_FALLBACK = "EGRESS_EXPECTED_IPS, DO API unread"
# The API refused for good (a bad token, no such cluster): the guard is blind
# until someone acts, whatever the fallback says (#833).
API_REFUSED = "EGRESS_EXPECTED_IPS, DO API refused"

EXIT_DRIFT = 3
EXIT_UNREADABLE = 5


def _is_transient(exc: BaseException) -> bool:
    """True when asking again could plausibly get a different answer.

    ``HTTPError`` is an ``OSError`` too, so it is judged by status first: a 401,
    403 or 404 answers the same way every time. ``LookupError`` (no such
    cluster) and ``ValueError`` (a bad cursor or body) are permanent as well.
    """
    if isinstance(exc, HTTPError):
        return exc.code >= 500 or exc.code == 429
    return isinstance(exc, OSError)


def fetch_with_retry(token: str, cluster: str, *, timeout: float) -> list[str]:
    """``fetch_allowed_ips``, retried with back-off on a transient failure.

    Raises the last exception once the attempts run out, or the first permanent
    one at once.
    """
    for attempt, delay in enumerate((*API_RETRY_DELAYS, None), start=1):
        try:
            return fetch_allowed_ips(token, cluster, timeout=timeout)
        except Exception as exc:
            if delay is None or not _is_transient(exc):
                raise
            logger.info(
                "DO API attempt %d/%d failed (%s); retrying in %.0fs",
                attempt,
                API_ATTEMPTS,
                exc.__class__.__name__,
                delay,
            )
            sleep(delay)
    raise AssertionError("unreachable")  # pragma: no cover


def resolve_allowlist(
    token: str,
    cluster: str,
    expected_raw: str,
    *,
    explicit: bool = False,
    timeout: float = DO_API_TIMEOUT,
) -> tuple[set[str] | None, str]:
    """Return (allowlist, source label), preferring the live Trusted Sources.

    The DO API is authoritative and needs nothing kept in sync by hand, so it
    wins whenever a token is present. It also catches a failure the env var
    cannot see: our address being *removed* from the list while our egress IP
    never changed.

    ``EGRESS_EXPECTED_IPS`` remains the fallback for a host with no token, or
    for an API that is still unreachable after retries; the latter is labelled
    ``API_FALLBACK`` so the caller can refuse to call a mismatch drift (#839).
    A *permanent* API failure is labelled ``API_REFUSED`` instead, with or
    without a fallback list, so the caller can report the guard blind (#833).

    An **explicitly passed** ``--expected`` overrides all of that: the operator
    is debugging, and a flag that silently loses to the API lies about what it
    compared (CR1 finding 2). The env var is not explicit and still loses.
    """
    if explicit:
        parsed = parse_expected(expected_raw)
        if not parsed:
            # Asking to compare against nothing is not a verdict.
            return None, UNKNOWN
        return parsed, "--expected"
    fallback_label = "EGRESS_EXPECTED_IPS"
    if token:
        try:
            live = fetch_with_retry(token, cluster, timeout=timeout)
        except Exception as exc:
            logger.warning("DO API allowlist lookup failed — falling back", exc_info=True)
            fallback_label = API_FALLBACK if _is_transient(exc) else API_REFUSED
        else:
            if not live:
                # DO treats an empty Trusted Sources list as "no IP restriction".
                # Nothing is being blocked, so nothing can have drifted.
                return None, UNRESTRICTED
            return set(live), "the DO API"

    expected = parse_expected(expected_raw)
    if expected:
        return expected, fallback_label
    return None, API_REFUSED if fallback_label == API_REFUSED else UNKNOWN


def main() -> None:
    """CLI entry point — exits 3 when the egress IP is not in the expected set."""
    configure_logging()
    parser = argparse.ArgumentParser(description="Detect egress-IP drift (#410)")
    parser.add_argument(
        "--expected",
        default=None,
        help="comma-separated IPs to compare against; overrides the DO API "
        "(env EGRESS_EXPECTED_IPS supplies a fallback, which does not)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help=f"per-service timeout in seconds (default {DEFAULT_TIMEOUT})",
    )
    parser.add_argument(
        "--cluster", default=DEFAULT_CLUSTER, help=f"DO cluster name (default {DEFAULT_CLUSTER})"
    )
    args = parser.parse_args()

    if os.environ.get("EGRESS_CHECK_FORCE_FAIL"):
        logger.warning("EGRESS_CHECK_FORCE_FAIL set — exercising the failure path (synthetic)")
        sys.exit(EXIT_DRIFT)

    current = lookup(DEFAULT_SERVICES, timeout=args.timeout, opener=urlopen)
    if current is None:
        # Not drift: every echo service failed. /ready covers the real outage.
        logger.warning("could not determine the egress IP — every lookup service failed")
        return

    allowed, source = resolve_allowlist(
        os.environ.get("DO_API_TOKEN", ""),
        args.cluster,
        args.expected if args.expected is not None else os.environ.get("EGRESS_EXPECTED_IPS", ""),
        explicit=args.expected is not None,
        timeout=args.timeout,
    )
    if source == API_REFUSED:
        # #833: a refusal answers the same way every run. Whatever the fallback
        # says, the guard cannot see a removed rule until the token is fixed.
        if allowed is None:
            fallback = "there is no EGRESS_EXPECTED_IPS fallback to compare against"
        elif current in allowed:
            fallback = "the fallback EGRESS_EXPECTED_IPS lists it"
        else:
            fallback = "the fallback EGRESS_EXPECTED_IPS does not list it"
        logger.error(
            "egress guard UNREADABLE — the DO API refused the allowlist lookup (above), and "
            "asking again will not change that. Egress IP is %s; %s. Fix DO_API_TOKEN or "
            "--cluster in /etc/power-map/.env",
            current,
            fallback,
        )
        sys.exit(EXIT_UNREADABLE)
    if source == UNRESTRICTED:
        logger.info(
            "cluster %s has no IP restrictions configured — egress IP %s cannot be blocked",
            args.cluster,
            current,
        )
        return
    if allowed is None:
        # Reached from several states (an empty --expected, no token and no env
        # var, an API failure with no fallback), so name a cause only when it is
        # actually the cause — misattributing it sends triage sideways.
        hint = (
            ""
            if os.environ.get("DO_API_TOKEN")
            else " — set DO_API_TOKEN or EGRESS_EXPECTED_IPS in /etc/power-map/.env"
        )
        logger.warning("could not determine the allowlist; egress IP is %s%s", current, hint)
        return

    if current in allowed:
        logger.info("egress IP %s is in the allowlist (source: %s)", current, source)
        return

    if source == API_FALLBACK:
        # #839: the hand-kept copy disagrees and the authority is unreachable.
        # Either it is stale or the IP rotated; /ready tells which, so no alert.
        logger.warning(
            "cannot verify egress IP %s — the DO API could not be read and the fallback "
            "EGRESS_EXPECTED_IPS (%s) does not list it. Not drift: if /ready is ok the "
            "fallback is stale, so update it in /etc/power-map/.env",
            current,
            ", ".join(sorted(allowed)),
        )
        return

    logger.warning(
        "egress IP DRIFT — %s is not in the allowlist (%s; source: %s)",
        current,
        ", ".join(sorted(allowed)),
        source,
    )
    logger.warning(
        "fix: add %s to DO → Databases → co-pm-db-1 → Settings → Trusted Sources, then "
        "update EGRESS_EXPECTED_IPS and terraform's allowed_external_ips to match",
        current,
    )
    sys.exit(EXIT_DRIFT)


if __name__ == "__main__":
    main()
