"""Guards the settings a shared SocratiCode store depends on (#568).

`co-index`, the cohort's Qdrant (CannObserv/notifier#57), is gated by one
global API key: every cohort VM holds the same secret, so a leak from any repo
is a rotation on all of them. The key lives in `.claude/settings.local.json`,
and this repo is public. Both checks are needed: `.gitignore` does not apply
to a tracked path, and an unignored one is a `git add -A` from a commit
(notifier `tests/deploy/test_socraticode_config.py`, broker#17).

The committed `env` block must never carry the settings that split or misroute
the shared namespace: `QDRANT_COLLECTION_PREFIX` prefixes the store-wide
`socraticode_metadata` collection for every client, `SOCRATICODE_BRANCH_AWARE`
suffixes a path-hash id, `QDRANT_HOST` builds its URL on port 16333, and the key
itself belongs to one host (vendored init-socraticode, external-store.md).
"""

import json
import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SETTINGS = REPO_ROOT / ".claude" / "settings.json"
LOCAL_SETTINGS_REL = ".claude/settings.local.json"

FORBIDDEN_TRACKED_ENV = (
    "QDRANT_API_KEY",
    "QDRANT_COLLECTION_PREFIX",
    "SOCRATICODE_BRANCH_AWARE",
    "QDRANT_HOST",
    "SOCRATICODE_PROJECT_ID",
)


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    """git against this checkout; pre-commit's `GIT_*` would outrank `-C`."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return subprocess.run(
        ["git", "-C", str(REPO_ROOT), *args], capture_output=True, text=True, check=False, env=env
    )


def test_the_local_settings_file_is_ignored_by_a_tracked_rule() -> None:
    result = _git("check-ignore", "-v", "--no-index", LOCAL_SETTINGS_REL)

    assert result.returncode == 0, f"{LOCAL_SETTINGS_REL} is not ignored — the key is one add away"
    source = result.stdout.split(":", 1)[0]
    assert source == ".gitignore", (
        f"{LOCAL_SETTINGS_REL} is ignored by {source}, not the repo's .gitignore: "
        "a global excludes file protects one machine, not every clone"
    )


def test_the_local_settings_file_is_not_tracked() -> None:
    result = _git("ls-files", "--error-unmatch", LOCAL_SETTINGS_REL)

    assert result.returncode != 0, (
        f"{LOCAL_SETTINGS_REL} is tracked — .gitignore does not apply to it; untrack it "
        "and rotate the co-index key on every cohort VM (notifier#57)"
    )


def test_the_committed_env_block_never_splits_or_misroutes_the_shared_store() -> None:
    env = json.loads(SETTINGS.read_text()).get("env", {})

    present = sorted(set(env) & set(FORBIDDEN_TRACKED_ENV))
    assert not present, f".claude/settings.json env carries {present}"
