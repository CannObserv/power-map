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

The expected set lives in ``EGRESS_EXPECTED_IPS`` (``/etc/power-map/.env``,
beside the DB credentials) and must be kept in step with terraform's
``allowed_external_ips``. That duplication is deliberate for now: #409 tracks
restoring terraform's credentials, after which a ``terraform plan`` drift check
can read the allowlist directly and this env var can go away.

Two judgements worth keeping:

- **Undeterminable is not drift.** If every echo service fails, the run logs a
  WARNING and exits 0. A third-party outage must not open an alert about our
  network — and the real failure would surface through ``/ready`` anyway.
- **A non-IP body is not an IP.** Echo services have been known to answer 200
  with an HTML error page; parsing that as an address would "drift" every run.

Exit codes: 0 = matches (or nothing to compare against); 3 = drift (systemd
marks the unit failed, so it shows in ``systemctl --failed``).

Test hatches:
    EGRESS_CHECK_NO_GH=1      skip GitHub surfacing entirely.
    EGRESS_CHECK_FORCE_FAIL=1 skip lookup and exercise the failure path.

Usage:
    uv run python -m scripts.check_egress_ip
    uv run python -m scripts.check_egress_ip --expected 69.67.149.183 --no-gh
"""

import argparse
import ipaddress
import os
import sys
from urllib.request import urlopen

from scripts._alerting import Alert, surface
from src.core.logging import configure_logging, get_logger

logger = get_logger(__name__)

# Two independent providers: one outage must not blind the guard.
DEFAULT_SERVICES = ("https://api.ipify.org", "https://ifconfig.me/ip")
DEFAULT_TIMEOUT = 10.0

EGRESS_ALERT = Alert(
    label="egress-ip-drift",
    title="Egress IP no longer matches the database allowlist (automated)",
    subject_recovered="the egress IP matches the allowlist again",
    unit="power-map-egress-ip",
    label_description="Automated egress-IP drift detection (power-map-egress-ip.timer, #410)",
)


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


def main() -> None:
    """CLI entry point — exits 3 when the egress IP is not in the expected set."""
    configure_logging()
    parser = argparse.ArgumentParser(description="Detect egress-IP drift (#410)")
    parser.add_argument(
        "--expected",
        default=os.environ.get("EGRESS_EXPECTED_IPS", ""),
        help="comma-separated IPs the database allowlist knows (env EGRESS_EXPECTED_IPS)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help=f"per-service timeout in seconds (default {DEFAULT_TIMEOUT})",
    )
    parser.add_argument(
        "--no-gh", action="store_true", help="skip GitHub surfacing (env EGRESS_CHECK_NO_GH)"
    )
    args = parser.parse_args()
    no_gh = args.no_gh or bool(os.environ.get("EGRESS_CHECK_NO_GH"))

    if os.environ.get("EGRESS_CHECK_FORCE_FAIL"):
        logger.warning("EGRESS_CHECK_FORCE_FAIL set — exercising the failure path (synthetic)")
        if not no_gh:
            surface(
                False,
                "synthetic drift (EGRESS_CHECK_FORCE_FAIL) — surfacing self-test, ignore.",
                alert=EGRESS_ALERT,
            )
        sys.exit(3)

    current = lookup(DEFAULT_SERVICES, timeout=args.timeout, opener=urlopen)
    if current is None:
        # Not drift: every echo service failed. /ready covers the real outage.
        logger.warning("could not determine the egress IP — every lookup service failed")
        return

    expected = parse_expected(args.expected)
    if not expected:
        logger.info(
            "egress IP is %s — no expected set configured; "
            "set EGRESS_EXPECTED_IPS in /etc/power-map/.env to enable drift detection",
            current,
        )
        return

    if current in expected:
        logger.info("egress IP %s is in the expected set", current)
        if not no_gh:
            surface(True, "", alert=EGRESS_ALERT)
        return

    summary = (
        f"Egress IP is now `{current}`, which is not in the expected set "
        f"(`{'`, `'.join(sorted(expected))}`).\n\n"
        "The database cluster gates on source IP, so this takes every DB-backed route "
        "down as soon as connections are re-established. Fix: add `" + current + "` to "
        "DO → Databases → `co-pm-db-1` → Settings → Trusted Sources, then update "
        "`EGRESS_EXPECTED_IPS` and terraform's `allowed_external_ips` to match."
    )
    logger.warning(
        "egress IP DRIFT — now %s, expected one of %s", current, ", ".join(sorted(expected))
    )
    if not no_gh:
        surface(False, summary, alert=EGRESS_ALERT)
    sys.exit(3)


if __name__ == "__main__":
    main()
