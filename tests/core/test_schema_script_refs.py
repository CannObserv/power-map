"""Every ``scripts/*.py`` that ``schema.sql`` names must exist (#634).

``schema.sql`` tells the operator which script to run when a migration meets
dirty data — in comments and in the ``RAISE WARNING`` text ``apply_schema``
prints at deploy time. A retired script left behind in that text sends the
operator to a module that is gone. Static: reads the file, no database.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_SQL = REPO_ROOT / "src" / "core" / "schema.sql"
SCRIPT_REF = re.compile(r"\bscripts/([A-Za-z0-9_]+\.py)\b")


def test_schema_sql_names_only_existing_scripts():
    """A script named in schema.sql is present under scripts/."""
    named = set(SCRIPT_REF.findall(SCHEMA_SQL.read_text()))
    assert named, "pattern found no script references — the guard is blind"
    missing = sorted(n for n in named if not (REPO_ROOT / "scripts" / n).is_file())
    assert missing == [], f"schema.sql names retired scripts: {missing}"
