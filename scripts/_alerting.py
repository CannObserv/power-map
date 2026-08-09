"""Shared GitHub surfacing for scheduled guards (#347, #410).

Extracted from ``scripts/check_ready`` when the egress-IP guard needed the
same machinery. Every rule here was paid for once already:

- **One issue per label.** ``gh issue list`` failing is not the same as "none
  open" — acting on a failed list opens duplicates (a11y #369 CR2 finding 5).
- **Silence while open.** These guards run every few minutes. A comment per
  failing run would post tens per hour and bury the signal it exists to raise.
  The open issue *is* the state, so no guard needs a state file.
- **Report what happened, not what was attempted.** A ``gh`` call whose return
  code is never inspected can log "opened issue" while no alert exists (#347
  install self-test).
- **Summary and journal pointer only.** CannObserv/power-map is a **public**
  repo; raw output stays on the VM (#369 CR).

Surfacing is best-effort throughout: it must never change a guard's exit code.
"""

import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime

from src.core.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class Alert:
    """Identity of one guard's alert channel.

    ``label`` is the dedup key — one open issue per label, ever. ``unit`` names
    the systemd unit whose journal holds the detail that is deliberately not
    published.
    """

    label: str
    title: str
    subject_recovered: str
    unit: str
    label_description: str


def gh(args: list[str]) -> tuple[int, str]:
    """Run ``gh`` with ``args``; return (returncode, stdout)."""
    completed = subprocess.run(["gh", *args], capture_output=True, text=True, check=False)
    return completed.returncode, completed.stdout


def _ensure_label(alert: Alert, *, runner) -> None:
    """Create the label if absent. Best-effort — never blocks surfacing."""
    rc, out = runner(["label", "list", "--limit", "200", "--json", "name", "-q", ".[].name"])
    if rc == 0 and alert.label in out.split():
        return
    runner(
        [
            "label",
            "create",
            alert.label,
            "--color",
            "B60205",
            "--description",
            alert.label_description,
        ]
    )


def _open_issue_number(alert: Alert, *, runner) -> tuple[bool, str | None]:
    """Return (list_succeeded, issue number or None)."""
    rc, out = runner(
        [
            "issue",
            "list",
            "--label",
            alert.label,
            "--state",
            "open",
            "--json",
            "number",
            "-q",
            ".[0].number",
        ]
    )
    if rc != 0:
        return False, None
    existing = out.strip()
    # `gh -q '.[0].number'` prints `null` for an empty list, not "".
    return True, None if existing in ("", "null") else existing


def surface(ok: bool, summary: str, *, alert: Alert, runner=None) -> None:
    """Open the alert issue on failure, close it on recovery."""
    runner = runner or gh
    try:
        _ensure_label(alert, runner=runner)
        listed, existing = _open_issue_number(alert, runner=runner)
        if not listed:
            logger.warning("gh issue list failed — skipping surfacing this run")
            return

        timestamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        if not ok:
            if existing:
                logger.info("alert already open as #%s — staying quiet", existing)
                return
            body = (
                f"**{alert.title}** — {timestamp}.\n\n{summary}\n\n"
                f"Full detail is on the VM: `journalctl -u {alert.unit}` "
                "(deliberately not published — this is a public repo). "
                f"Automated by `{alert.unit}.timer`."
            )
            rc, _ = runner(
                ["issue", "create", "--title", alert.title, "--label", alert.label, "--body", body]
            )
            if rc == 0:
                logger.warning("opened %s issue", alert.label)
            else:
                # The next run finds nothing open and tries again.
                logger.warning("gh issue create failed (rc=%d) — no alert raised", rc)
        elif existing:
            rc, _ = runner(
                [
                    "issue",
                    "comment",
                    existing,
                    "--body",
                    f"✅ **Recovered** — {alert.subject_recovered} as of {timestamp}. "
                    "Auto-closing.",
                ]
            )
            if rc != 0:
                # Closing is what clears the alert, so press on regardless —
                # but say the note is missing rather than implying it landed.
                logger.warning("gh issue comment failed (rc=%d) — closing anyway", rc)
            rc, _ = runner(["issue", "close", existing])
            if rc == 0:
                logger.info("closed recovered issue #%s", existing)
            else:
                logger.warning("gh issue close failed (rc=%d) — issue #%s left open", rc, existing)
    except Exception:
        # A broken gh must not turn a real failure into a clean exit.
        logger.warning("GitHub surfacing failed — guard result stands", exc_info=True)
