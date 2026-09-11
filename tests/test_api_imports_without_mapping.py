"""The API service runs without the `mapping` dependency group (#498).

The service's `uv sync` installs the default groups only, so dbt is absent from
production's environment. The admin's pin path reaches into the ingestion tree
(`src.core.curation_overlay` → `src.core.ingestion.crosswalk`), and a single
careless import of the mapping package — whose `__init__` loads dbt — would
take the whole API down at boot while every test, run with the group installed,
stayed green. So import the app in a fresh interpreter with dbt made
unimportable, and fail if anything reaches for it.
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

_PROBE = """
import sys
sys.modules["dbt"] = None  # any `import dbt...` now raises ImportError
import src.api.main  # noqa: F401
import src.api.admin.overlay_slots  # noqa: F401
print("ok")
"""


def test_the_app_imports_with_dbt_unimportable():
    result = subprocess.run(
        [sys.executable, "-c", _PROBE], cwd=ROOT, capture_output=True, text=True, timeout=120
    )

    assert result.returncode == 0, result.stderr[-2000:]
    assert result.stdout.strip().endswith("ok")


def test_the_probe_can_fail():
    """The guard above must be able to see a dbt import, or it proves nothing."""
    probe = 'import sys\nsys.modules["dbt"] = None\nimport src.core.ingestion.mapping\n'
    result = subprocess.run(
        [sys.executable, "-c", probe], cwd=ROOT, capture_output=True, text=True, timeout=120
    )

    assert result.returncode != 0 and "dbt" in result.stderr
