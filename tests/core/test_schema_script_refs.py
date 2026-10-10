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
SCRIPT_PATH_REF = re.compile(r"\bscripts/([A-Za-z0-9_]+)\.py\b")
SCRIPT_MODULE_REF = re.compile(r"-m\s+scripts\.([A-Za-z0-9_]+)\b")


def named_scripts(text: str) -> set[str]:
    """Return the ``scripts/`` file names ``text`` names, path or ``-m`` module form."""
    stems = SCRIPT_PATH_REF.findall(text) + SCRIPT_MODULE_REF.findall(text)
    return {f"{stem}.py" for stem in stems}


def test_schema_sql_names_only_existing_scripts():
    """A script named in schema.sql is present under scripts/."""
    named = named_scripts(SCHEMA_SQL.read_text())
    assert named, "pattern found no script references — the guard is blind"
    missing = sorted(n for n in named if not (REPO_ROOT / "scripts" / n).is_file())
    assert missing == [], f"schema.sql names retired scripts: {missing}"


def test_named_scripts_reads_path_and_module_forms():
    """Both ``scripts/x.py`` and ``-m scripts.x`` name the file ``x.py``."""
    text = "-- run scripts/a_b.py, or python -m scripts.c_d --execute; seed_locales_scripts.py"
    assert named_scripts(text) == {"a_b.py", "c_d.py"}
