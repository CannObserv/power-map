"""Contract between sync-schema-to-do.sh and apply-schema.sh (#398).

The provisioning script delegates its test-database apply rather than carrying
its own copy. That coupling is invisible until someone runs the provisioning
flow by hand, which is rare — so the flag contract is pinned here.
"""

import subprocess
from pathlib import Path

SCRIPTS = Path(__file__).parents[2] / "scripts"
SYNC = SCRIPTS / "sync-schema-to-do.sh"
APPLY = SCRIPTS / "apply-schema.sh"


def test_delegates_the_test_apply_to_apply_schema():
    assert 'apply-schema.sh" --test' in SYNC.read_text()


def test_apply_schema_still_accepts_the_delegated_flag():
    assert "--test)" in APPLY.read_text()


def test_sync_schema_parses():
    result = subprocess.run(["bash", "-n", str(SYNC)], capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
