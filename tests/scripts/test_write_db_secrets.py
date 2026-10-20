"""Guards on scripts/write-db-secrets.sh (#667).

The script is the provisioning path for ``/etc/power-map/.env``: whatever DSNs it
writes are what every consumer connects with after a re-run. Trusted Sources
admits an exe.dev egress IP other VMs may share, so the boundary is credentials
plus TLS — every DSN it writes, and every connection it makes itself, must
verify the server against the cluster CA (``sslmode=verify-full``).

Each test runs a copy of the script in ``tmp_path`` with ``terraform``, ``psql``
and ``sudo`` shimmed on ``PATH``: the ``sudo`` shim maps ``/etc/power-map`` onto
a scratch directory, so nothing touches the host and no real DSN is read.
"""

import ast
import json
import os
import re
import shutil
import stat
import subprocess
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

REPO = Path(__file__).parents[2]
SCRIPT = REPO / "scripts" / "write-db-secrets.sh"
CA_PATH = "/etc/power-map/do-ca.crt"

DOADMIN_URI = "postgresql://doadmin:adm1n@db.example.invalid:25060/defaultdb?sslmode=require"

SUDO_SHIM = """#!/usr/bin/env bash
# Map /etc/power-map onto $FAKE_ETC; log every call; never really chown.
echo "sudo $*" >> "$SHIM_LOG"
args=()
for a in "$@"; do args+=("${a//\\/etc\\/power-map/$FAKE_ETC}"); done
[ "${args[0]}" = chown ] && exit 0
exec "${args[@]}"
"""

PSQL_SHIM = """#!/usr/bin/env bash
echo "psql $1" >> "$SHIM_LOG"
cat > /dev/null
exit 0
"""

TERRAFORM_SHIM = """#!/usr/bin/env bash
cat "$TF_OUTPUT_JSON"
"""


def _self_signed_pem(tmp_path: Path) -> str:
    """A throwaway CA certificate, so the script's PEM check sees a real one."""
    key, crt = tmp_path / "ca.key", tmp_path / "ca.crt"
    subprocess.run(
        [
            "openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256",
            "-nodes", "-subj", "/CN=test CA", "-days", "1",
            "-keyout", str(key), "-out", str(crt),
        ],
        check=True,
        capture_output=True,
    )  # fmt: skip
    return crt.read_text()


def _outputs(ca_pem: str) -> dict:
    """Terraform ``output -json`` shape for every key the script reads."""
    values = {
        "doadmin_uri": DOADMIN_URI,
        "db_host": "db.example.invalid",
        "db_port": 25060,
        "production_user_name": "app_user",
        "production_user_password": "p@ss/word",
        "production_migrations_name": "migrations_user",
        "production_migrations_password": "m1grate",
        "test_user_name": "test_user",
        "test_user_password": "t3st",
        "db_ca_certificate": ca_pem,
    }
    return {k: {"value": v, "sensitive": False, "type": "string"} for k, v in values.items()}


def _run(tmp_path: Path, outputs: dict) -> tuple[subprocess.CompletedProcess, Path, list[str]]:
    """Run a copy of the script against shims; return (result, fake /etc/power-map, log)."""
    root = tmp_path / "checkout"
    (root / "scripts").mkdir(parents=True)
    shutil.copy2(SCRIPT, root / "scripts" / SCRIPT.name)
    tf = root / "infra" / "terraform"
    (tf / ".terraform").mkdir(parents=True)
    (tf / "terraform.tfvars").write_text("")
    (tf / "backend.hcl").write_text("")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("sudo", SUDO_SHIM), ("psql", PSQL_SHIM), ("terraform", TERRAFORM_SHIM)):
        shim = bin_dir / name
        shim.write_text(body)
        shim.chmod(0o755)

    fake_etc = tmp_path / "etc-power-map"
    fake_etc.mkdir()
    (fake_etc / ".env").write_text("DATABASE_URL=old\nADDRESS_VALIDATOR_API_KEY=keep-me\n")
    tf_json = tmp_path / "tf-output.json"
    tf_json.write_text(json.dumps(outputs))
    log = tmp_path / "shim.log"
    log.write_text("")

    env = {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "HOME": str(tmp_path),
        "FAKE_ETC": str(fake_etc),
        "SHIM_LOG": str(log),
        "TF_OUTPUT_JSON": str(tf_json),
    }
    result = subprocess.run(
        ["bash", str(root / "scripts" / SCRIPT.name)],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return result, fake_etc, log.read_text().splitlines()


def _env_dsns(env_file: Path) -> dict[str, str]:
    """The ``*DATABASE_URL`` lines of a written env file."""
    pairs = (line.split("=", 1) for line in env_file.read_text().splitlines() if "=" in line)
    return {k: v for k, v in pairs if k.endswith("DATABASE_URL")}


def _assert_verify_full(dsn: str) -> None:
    """*dsn* verifies the server against the cluster CA, and asks nothing weaker."""
    query = parse_qs(urlparse(dsn).query)
    assert query.get("sslmode") == ["verify-full"], dsn
    assert query.get("sslrootcert") == [CA_PATH], dsn


@pytest.fixture
def ca_pem(tmp_path: Path) -> str:
    return _self_signed_pem(tmp_path)


def test_every_written_dsn_verifies_the_server(tmp_path: Path, ca_pem: str) -> None:
    """All three DSNs carry verify-full and the CA path, and keep their credentials."""
    result, fake_etc, _ = _run(tmp_path, _outputs(ca_pem))
    assert result.returncode == 0, result.stderr

    dsns = _env_dsns(fake_etc / ".env")
    assert set(dsns) == {"DATABASE_URL", "MIGRATIONS_DATABASE_URL", "TEST_DATABASE_URL"}
    for dsn in dsns.values():
        _assert_verify_full(dsn)
    assert urlparse(dsns["DATABASE_URL"]).password == "p%40ss%2Fword"
    assert "ADDRESS_VALIDATOR_API_KEY=keep-me" in (fake_etc / ".env").read_text()


def test_installs_the_cluster_ca_world_readable_and_root_owned(tmp_path: Path, ca_pem: str) -> None:
    """The units run as exedev, so the CA (not a secret) is 0644 root:root."""
    result, fake_etc, log = _run(tmp_path, _outputs(ca_pem))
    assert result.returncode == 0, result.stderr

    ca_file = fake_etc / "do-ca.crt"
    assert ca_file.read_text().strip() == ca_pem.strip()
    assert stat.S_IMODE(ca_file.stat().st_mode) == 0o644
    assert f"sudo chown root:root {CA_PATH}" in log


def test_its_own_connections_verify_the_server_after_the_ca_lands(
    tmp_path: Path, ca_pem: str
) -> None:
    """Grants (doadmin) and the smoke test run verify-full, never before the CA exists."""
    result, _, log = _run(tmp_path, _outputs(ca_pem))
    assert result.returncode == 0, result.stderr

    psql_calls = [i for i, line in enumerate(log) if line.startswith("psql ")]
    assert len(psql_calls) == 5, log  # 2 grant runs + 3 smoke tests
    for i in psql_calls:
        _assert_verify_full(log[i].removeprefix("psql "))
    ca_written = next(i for i, line in enumerate(log) if f"tee {CA_PATH}" in line)
    assert ca_written < psql_calls[0]


def test_refuses_a_ca_that_is_not_a_certificate(tmp_path: Path) -> None:
    """A wrong or empty output would make every verify-full connection fail: stop first."""
    result, fake_etc, log = _run(tmp_path, _outputs("null"))
    assert result.returncode == 2
    assert "db_ca_certificate" in result.stderr
    assert not (fake_etc / "do-ca.crt").exists()
    assert (fake_etc / ".env").read_text().startswith("DATABASE_URL=old")
    assert not [line for line in log if line.startswith("psql ")]


def test_every_output_the_script_reads_is_declared() -> None:
    """The shims above stand in for terraform; this pins them to the real outputs."""
    read = set(re.findall(r"jq -r '\.(\w+)\.value'", SCRIPT.read_text()))
    outputs_tf = (REPO / "infra" / "terraform" / "outputs.tf").read_text()
    declared = set(re.findall(r'^output "(\w+)"', outputs_tf, re.MULTILINE))
    assert "db_ca_certificate" in read
    assert read <= declared, read - declared


SSLMODE = re.compile(r"(?:PG)?SSLMODE\s*[=:]\s*['\"]?([\w-]+)", re.IGNORECASE)
_RATCHET_SKIP = ("tests/", "skills-vendor/", "src/static/vendor/")


def _tracked_files() -> list[Path]:
    """Every tracked file outside tests and vendored trees."""
    out = subprocess.run(
        ["git", "ls-files"], cwd=REPO, check=True, capture_output=True, text=True
    ).stdout
    return [REPO / f for f in out.splitlines() if not f.startswith(_RATCHET_SKIP)]


def test_no_dsn_outside_tests_asks_for_less_than_verify_full() -> None:
    """Ratchet: any sslmode / PGSSLMODE setting weaker than verify-full is a regression.

    Covers the URI form, libpq keyword strings and env lines, in every tracked
    file but tests/ and vendored trees.
    """
    weaker = []
    for path in _tracked_files():
        if not path.is_file():
            continue
        try:
            text = path.read_text()
        except UnicodeDecodeError:
            continue
        for n, line in enumerate(text.splitlines(), 1):
            weaker += [
                f"{path.relative_to(REPO)}:{n}: sslmode={m}"
                for m in SSLMODE.findall(line)
                if m.lower() != "verify-full"
            ]
    assert weaker == []


@pytest.mark.parametrize(
    "line",
    [
        "postgresql://u@h/db?sslmode=require",
        "host=h dbname=db sslmode = 'prefer'",
        "PGSSLMODE=disable",
        'Environment="PGSSLMODE: allow"',
    ],
)
def test_the_ratchet_pattern_sees_every_form(line: str) -> None:
    """The ratchet would be blind, not green, if the pattern missed a form."""
    assert SSLMODE.findall(line) and SSLMODE.findall(line)[0].lower() != "verify-full"


_SSL_OVERRIDING_CALLS = {"connect", "create_pool"}


def _ssl_overrides(path: Path) -> list[str]:
    """``ssl=`` passed to a ``connect`` / ``create_pool`` call in *path*."""
    tree = ast.parse(path.read_text(), filename=str(path))
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
        if name in _SSL_OVERRIDING_CALLS and any(kw.arg == "ssl" for kw in node.keywords):
            shown = path.relative_to(REPO) if path.is_relative_to(REPO) else path
            hits.append(f"{shown}:{node.lineno}")
    return hits


def test_no_code_passes_ssl_to_a_connection() -> None:
    """asyncpg's ``ssl=`` overrides the DSN's sslmode: one ``ssl=True`` drops verify-full.

    TLS stays config-only (the DSN), so no connect or pool call may set it.
    """
    hits = []
    for root in ("src", "scripts", "clients"):
        for path in sorted((REPO / root).rglob("*.py")):
            hits += _ssl_overrides(path)
    assert hits == []


def test_the_ssl_sweep_sees_both_call_shapes(tmp_path: Path) -> None:
    """The sweep would be blind, not green, if it missed a call shape."""
    sample = tmp_path / "sample.py"
    sample.write_text(
        "import asyncpg\n"
        "async def f(d):\n"
        "    await asyncpg.connect(d, ssl=True)\n"
        "    await create_pool(d, ssl='require')\n"
    )
    assert len(_ssl_overrides(sample)) == 2
