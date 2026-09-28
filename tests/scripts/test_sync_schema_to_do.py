"""Contract between sync-schema-to-do.sh and apply-schema.sh (#398, #581).

The provisioning script delegates its test-database apply rather than carrying
its own copy, and hands the production apply to apply-schema.sh as the next
step. That coupling is invisible until someone runs the provisioning flow by
hand, which is rare — so the contract is pinned here.
"""

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).parents[2]
SCRIPTS = ROOT / "scripts"
SYNC = SCRIPTS / "sync-schema-to-do.sh"
APPLY = SCRIPTS / "apply-schema.sh"
COMMANDS = ROOT / "docs" / "COMMANDS.md"


def test_delegates_the_test_apply_to_apply_schema():
    assert 'apply-schema.sh" --test' in SYNC.read_text()


def test_apply_schema_still_accepts_the_delegated_flag():
    assert "--test)" in APPLY.read_text()


def test_sync_schema_parses():
    result = subprocess.run(["bash", "-n", str(SYNC)], capture_output=True, text=True)

    assert result.returncode == 0, result.stderr


def test_sync_data_script_is_retired():
    # Its source, the local cluster, was purged by #576 (#581).
    assert not (SCRIPTS / "sync-data-to-do.sh").exists()
    assert "sync-data-to-do.sh" not in SYNC.read_text()


def test_sync_schema_names_apply_schema_as_the_production_step():
    assert re.search(r'echo ".*Production schema:.*apply-schema\.sh', SYNC.read_text())


def _provisioning_block() -> str:
    section = COMMANDS.read_text().split("### First-time provisioning", 1)[1]
    return section.split("```bash", 1)[1].split("```", 1)[0]


def test_provisioning_applies_production_schema_before_seeding():
    block = _provisioning_block()

    assert "sync-data-to-do.sh" not in block
    assert "\nbash scripts/apply-schema.sh\n" in block
    assert block.index("bash scripts/apply-schema.sh\n") < block.index("seed_locales_scripts.py")


def test_provisioning_ready_check_waits_for_the_listener():
    # power-map.service is Type=simple: restart returns before uvicorn listens.
    assert "curl -fsS --retry 10 --retry-connrefused" in _provisioning_block()
