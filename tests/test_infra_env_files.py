"""No production unit loads the repo ``.env`` (#724).

The repo ``.env`` holds a developer's credentials (``GH_TOKEN*``, an API key),
and a production process has no use for them. Loaded after
``/etc/power-map/.env``, it would also let a dev-only value silently override
production config. So every unit reads ``/etc/power-map/.env`` only, and a repo
shell script a unit runs doesn't pass the repo ``.env`` back in through
``uv run --env-file``.

These read the tracked units, not the live host: the #560 drift guard holds the
installed copies to them.
"""

import re
from pathlib import Path

from tests.test_infra_alerting import values

REPO = Path(__file__).resolve().parents[1]
INFRA = REPO / "infra"
SERVICES = sorted(INFRA.glob("*.service"))
DEPLOYED = Path("/home/exedev/power-map")
SYSTEM_ENV = "/etc/power-map/.env"
EXEC_KEYS = ("ExecStartPre", "ExecStart", "ExecStartPost", "ExecStopPost")
ENV_FILE_ARG = re.compile(r"--env-file[ =]\"?([^\s\")]+)")


def _env_files(unit: Path) -> list[str]:
    return [v.removeprefix("-") for v in values(unit, "Service", "EnvironmentFile")]


def _scripts(unit: Path) -> list[Path]:
    """The repo shell scripts a unit's ``Exec*=`` lines run, as checkout paths."""
    found = []
    for key in EXEC_KEYS:
        for line in values(unit, "Service", key):
            for token in line.lstrip("-+!@:").split():
                if not token.endswith(".sh"):
                    continue
                path = Path(token)
                if path.is_absolute():
                    if not path.is_relative_to(DEPLOYED):
                        continue  # a system script: not this repo's to read
                    path = path.relative_to(DEPLOYED)
                found.append(REPO / path)
    return found


def test_no_unit_loads_an_env_file_outside_etc():
    for unit in SERVICES:
        assert all(f == SYSTEM_ENV for f in _env_files(unit)), unit.name


def test_the_units_that_load_an_env_file_still_load_the_system_one():
    """The sweep above passes vacuously if the parse finds nothing."""
    loading = {u.name for u in SERVICES if SYSTEM_ENV in _env_files(u)}

    assert {"power-map.service", "power-map-desired-state.service"} <= loading


def test_no_script_a_unit_runs_passes_the_repo_env_to_uv():
    scripts = [s for unit in SERVICES for s in _scripts(unit)]

    assert REPO / "scripts/apply-schema.sh" in scripts
    for script in scripts:
        text = script.read_text()
        assert set(ENV_FILE_ARG.findall(text)) <= {SYSTEM_ENV}, script.name
        assert "load-env.sh" not in text, script.name
