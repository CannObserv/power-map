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
from urllib.parse import parse_qs, unquote, urlparse

import pytest

REPO = Path(__file__).parents[2]
SCRIPT = REPO / "scripts" / "write-db-secrets.sh"
CA_PATH = "/etc/power-map/do-ca.crt"

OLD_CA = "the CA the host already trusts\n"
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
echo "psql $1 pw=${PGPASSWORD:+set} args=${*:2}" >> "$SHIM_LOG"
cat > /dev/null
# Refuse one connection, as a wrong password or CA would.
[ -n "${PSQL_FAIL_ON:-}" ] && [[ "$1" == *"$PSQL_FAIL_ON"* ]] && exit 2
exit 0
"""

PYTHON3_SHIM = """#!/usr/bin/env bash
echo "python3 $*" >> "$SHIM_LOG"
exec "$REAL_PYTHON3" "$@"
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
        "production_migrations_password": "m1:gr#ate",
        "test_user_name": "test_user",
        "test_user_password": "t3st?&=",
        "db_ca_certificate": ca_pem,
    }
    return {k: {"value": v, "sensitive": False, "type": "string"} for k, v in values.items()}


def _run(
    tmp_path: Path, outputs: dict, psql_fail_on: str = "", fresh_host: bool = False
) -> tuple[subprocess.CompletedProcess, Path, list[str]]:
    """Run a copy of the script against shims; return (result, fake /etc/power-map, log).

    By default the host already has an env file and a CA; *fresh_host* has neither.
    """
    root = tmp_path / "checkout"
    (root / "scripts").mkdir(parents=True)
    shutil.copy2(SCRIPT, root / "scripts" / SCRIPT.name)
    tf = root / "infra" / "terraform"
    (tf / ".terraform").mkdir(parents=True)
    (tf / "terraform.tfvars").write_text("")
    (tf / "backend.hcl").write_text("")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    shims = (
        ("sudo", SUDO_SHIM),
        ("psql", PSQL_SHIM),
        ("python3", PYTHON3_SHIM),
        ("terraform", TERRAFORM_SHIM),
    )
    for name, body in shims:
        shim = bin_dir / name
        shim.write_text(body)
        shim.chmod(0o755)

    fake_etc = tmp_path / "etc-power-map"
    fake_etc.mkdir()
    if not fresh_host:
        (fake_etc / ".env").write_text("DATABASE_URL=old\nADDRESS_VALIDATOR_API_KEY=keep-me\n")
        (fake_etc / "do-ca.crt").write_text(OLD_CA)
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
        "PSQL_FAIL_ON": psql_fail_on,
        "REAL_PYTHON3": shutil.which("python3") or "python3",
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


def _psql_calls(log: list[str]) -> list[tuple[int, str, str]]:
    """(log index, URI, remaining args) for every psql call the shim saw."""
    calls = []
    for i, line in enumerate(log):
        if line.startswith("psql "):
            uri = line.split()[1]
            calls.append((i, uri, line.split(" args=", 1)[1]))
    return calls


def _assert_verify_full(dsn: str, ca: str = CA_PATH) -> None:
    """*dsn* verifies the server against *ca*, and asks nothing weaker."""
    query = parse_qs(urlparse(dsn).query)
    assert query.get("sslmode") == ["verify-full"], dsn
    assert query.get("sslrootcert") == [ca], dsn


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
    raw = {
        "DATABASE_URL": "p@ss/word",
        "MIGRATIONS_DATABASE_URL": "m1:gr#ate",
        "TEST_DATABASE_URL": "t3st?&=",
    }
    # The smoke tests connect from the raw parts, not this string: it must decode back.
    assert {k: unquote(urlparse(v).password or "") for k, v in dsns.items()} == raw
    assert "ADDRESS_VALIDATOR_API_KEY=keep-me" in (fake_etc / ".env").read_text()


def test_installs_the_cluster_ca_world_readable_and_root_owned(tmp_path: Path, ca_pem: str) -> None:
    """The units run as exedev, so the CA (not a secret) is 0644 root:root."""
    result, fake_etc, log = _run(tmp_path, _outputs(ca_pem))
    assert result.returncode == 0, result.stderr

    ca_file = fake_etc / "do-ca.crt"
    assert ca_file.read_text().strip() == ca_pem.strip()
    assert stat.S_IMODE(ca_file.stat().st_mode) == 0o644
    assert f"sudo chown root:root {CA_PATH}.new" in log


def test_replaces_the_ca_atomically(tmp_path: Path, ca_pem: str) -> None:
    """A re-run (rotation step 3) happens under live connects: never truncate the CA in place."""
    result, fake_etc, log = _run(tmp_path, _outputs(ca_pem))
    assert result.returncode == 0, result.stderr

    assert f"sudo tee {CA_PATH}" not in log
    staged = log.index(f"sudo tee {CA_PATH}.new")
    owned = log.index(f"sudo chown root:root {CA_PATH}.new")
    renamed = log.index(f"sudo mv -f {CA_PATH}.new {CA_PATH}")
    assert staged < owned < renamed
    assert not (fake_etc / "do-ca.crt.new").exists()


def test_replaces_the_env_file_atomically_and_never_world_readable(
    tmp_path: Path, ca_pem: str
) -> None:
    """tee into the live file truncates it first, and on a fresh host creates it 0644.

    A full disk mid-write would lose every secret in it; a timer starting
    mid-write would read half of it. Stage it, lock it down while still empty,
    fill it, rename it.
    """
    result, fake_etc, log = _run(tmp_path, _outputs(ca_pem))
    assert result.returncode == 0, result.stderr

    env, staged = "/etc/power-map/.env", "/etc/power-map/.env.new"
    assert f"sudo tee {env}" not in log
    locked = log.index(f"sudo chmod 640 {staged}")
    owned = log.index(f"sudo chown root:exedev {staged}")
    filled = log.index(f"sudo tee {staged}")
    renamed = log.index(f"sudo mv -f {staged} {env}")
    assert locked < filled and owned < filled < renamed
    assert stat.S_IMODE((fake_etc / ".env").stat().st_mode) == 0o640
    assert not (fake_etc / ".env.new").exists()


def test_provisions_a_fresh_host(tmp_path: Path, ca_pem: str) -> None:
    """First-time provisioning: no env file to preserve from, no CA to replace."""
    result, fake_etc, _ = _run(tmp_path, _outputs(ca_pem), fresh_host=True)
    assert result.returncode == 0, result.stderr

    env = fake_etc / ".env"
    assert stat.S_IMODE(env.stat().st_mode) == 0o640
    keys = [line.split("=", 1)[0] for line in env.read_text().splitlines()]
    assert keys == ["DATABASE_URL", "MIGRATIONS_DATABASE_URL", "TEST_DATABASE_URL"]
    assert (fake_etc / "do-ca.crt").read_text().strip() == ca_pem.strip()
    assert sorted(f.name for f in fake_etc.iterdir()) == [".env", "do-ca.crt"]


def test_its_own_connections_prove_the_staged_ca_before_it_goes_live(
    tmp_path: Path, ca_pem: str
) -> None:
    """Grants and smoke tests verify against the *staged* CA; it is renamed in only after.

    The live CA is what the running service's new connections read: a CA nothing
    has connected through yet must not replace it.
    """
    result, _, log = _run(tmp_path, _outputs(ca_pem))
    assert result.returncode == 0, result.stderr

    calls = _psql_calls(log)
    assert len(calls) == 5, log  # 2 grant runs + 3 smoke tests
    staged = log.index(f"sudo tee {CA_PATH}.new")
    for _, uri, _ in calls:
        _assert_verify_full(uri, ca=f"{CA_PATH}.new")
    live = log.index(f"sudo mv -f {CA_PATH}.new {CA_PATH}")
    assert staged < calls[0][0] and calls[-1][0] < live


def test_a_failed_grant_fails_the_script(tmp_path: Path, ca_pem: str) -> None:
    """psql exits 0 on a failed statement unless ON_ERROR_STOP is set."""
    result, _, log = _run(tmp_path, _outputs(ca_pem))
    assert result.returncode == 0, result.stderr

    grants = [args for _, uri, args in _psql_calls(log) if urlparse(uri).username == "doadmin"]
    assert len(grants) == 2
    assert all("-v ON_ERROR_STOP=1" in args for args in grants), grants


def test_no_psqlrc_runs_in_its_sessions(tmp_path: Path, ca_pem: str) -> None:
    """psqlrc runs after -v, so one could unset ON_ERROR_STOP: every call passes -X."""
    result, _, log = _run(tmp_path, _outputs(ca_pem))
    assert result.returncode == 0, result.stderr

    calls = _psql_calls(log)
    assert calls and all("-X" in args.split() for _, _, args in calls), calls


@pytest.mark.parametrize("user", ["app_user", "migrations_user", "test_user"])
def test_a_dsn_that_cannot_connect_fails_before_the_env_file_changes(
    tmp_path: Path, ca_pem: str, user: str
) -> None:
    """A smoke test that fails must fail the script, and leave the old DSNs in place.

    Otherwise a rotation writes a dead DSN, reports Done, and the next restart's
    ExecStartPre takes the service down.
    """
    result, fake_etc, log = _run(tmp_path, _outputs(ca_pem), psql_fail_on=f"//{user}@")
    assert result.returncode != 0
    assert (fake_etc / ".env").read_text().startswith("DATABASE_URL=old")
    assert not [line for line in log if line.startswith("sudo tee /etc/power-map/.env")]
    assert (fake_etc / "do-ca.crt").read_text() == OLD_CA


# Every password _outputs() hands the script, raw and URL-encoded.
SECRETS = ("p@ss/word", "p%40ss%2Fword", "m1:gr#ate", "m1%3Agr%23ate", "t3st?&=", "adm1n")


def test_no_password_reaches_a_command_line(tmp_path: Path, ca_pem: str) -> None:
    """/proc/*/cmdline is world-readable; credentials are the boundary (#667).

    Passwords travel in the environment (PGPASSWORD; /proc/*/environ is
    owner-only) or on stdin, never in argv: not psql's, not python3's.
    """
    result, _, log = _run(tmp_path, _outputs(ca_pem))
    assert result.returncode == 0, result.stderr

    assert [line for line in log if any(secret in line for secret in SECRETS)] == []
    assert all(" pw=set " in log[i] for i, _, _ in _psql_calls(log)), log


@pytest.mark.parametrize("missing", ["doadmin_uri", "test_user_password", "db_ca_certificate"])
def test_a_missing_output_fails_naming_it(tmp_path: Path, ca_pem: str, missing: str) -> None:
    """jq -r prints a missing output as the string "null": stop and name it instead.

    A "null" doadmin_uri became an empty user, and libpq fell back to the OS user.
    """
    outputs = _outputs(ca_pem)
    del outputs[missing]
    result, fake_etc, log = _run(tmp_path, outputs)
    assert result.returncode == 2
    assert f"terraform output {missing} is missing" in result.stderr
    assert (fake_etc / ".env").read_text().startswith("DATABASE_URL=old")
    assert (fake_etc / "do-ca.crt").read_text() == OLD_CA
    assert not [line for line in log if line.startswith(("psql ", "sudo "))]


def test_refuses_a_ca_that_is_not_a_certificate(tmp_path: Path) -> None:
    """A wrong or empty output would make every verify-full connection fail: stop first."""
    result, fake_etc, log = _run(tmp_path, _outputs("null"))
    assert result.returncode == 2
    assert "db_ca_certificate" in result.stderr
    assert (fake_etc / "do-ca.crt").read_text() == OLD_CA
    assert (fake_etc / ".env").read_text().startswith("DATABASE_URL=old")
    assert not [line for line in log if line.startswith("psql ")]


def test_every_output_the_script_reads_is_declared() -> None:
    """The shims above stand in for terraform; this pins them to the real outputs."""
    read = set(re.findall(r"\$\(out (\w+)\)", SCRIPT.read_text()))
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
