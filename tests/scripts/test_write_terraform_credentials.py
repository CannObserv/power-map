"""Tests for scripts.write_terraform_credentials — rebuild terraform's secrets (#409).

Both of terraform's credential files are gitignored, and on 2026-08-09 both
turned out to be absent from the VM with no record of where their contents
lived. Remote state was fine the whole time; only the keys to reach it were
gone. That made allowlist edits console-only (so they drifted) and blocked
`write-db-secrets.sh`, which opens with `terraform output -json`.

This script turns that recovery from an excavation into one command, sourcing
everything from `/etc/power-map/.env` — the root-owned file that already holds
the database credentials.

The allowlist is read from the DigitalOcean API rather than typed in, because
the live Trusted Sources list is authoritative: a reconstruction that guesses
it would produce a plan proposing to *remove* rules.
"""

import stat

import pytest

from scripts.write_terraform_credentials import (
    REQUIRED_KEYS,
    parse_env,
    render_backend,
    render_tfvars,
    write_credentials,
)

ENV_TEXT = """
# comment line
DATABASE_URL=postgresql://u:p@host:25060/db?sslmode=require
DO_API_TOKEN="dop_v1_secrettoken"
DO_SPACES_KEY='SPACESKEY123'

DO_SPACES_VALUE=spaces+secret/value=with=equals
"""

SECRETS = {
    "DO_API_TOKEN": "dop_v1_secrettoken",
    "DO_SPACES_KEY": "SPACESKEY123",
    "DO_SPACES_VALUE": "spaces+secret/value=with=equals",
}


# --- parse_env -------------------------------------------------------------


def test_parse_env_reads_keys():
    parsed = parse_env(ENV_TEXT)
    assert parsed["DO_API_TOKEN"] == "dop_v1_secrettoken"
    assert parsed["DO_SPACES_KEY"] == "SPACESKEY123"


def test_parse_env_strips_both_quote_styles():
    parsed = parse_env(ENV_TEXT)
    assert not parsed["DO_API_TOKEN"].startswith('"')
    assert not parsed["DO_SPACES_KEY"].startswith("'")


def test_parse_env_keeps_equals_inside_values():
    """Spaces secrets and DSNs both contain '=' — split on the first only."""
    assert parse_env(ENV_TEXT)["DO_SPACES_VALUE"] == "spaces+secret/value=with=equals"


def test_parse_env_ignores_comments_and_blanks():
    parsed = parse_env(ENV_TEXT)
    assert not any(k.startswith("#") for k in parsed)


def test_required_keys_are_the_three_do_secrets():
    assert set(REQUIRED_KEYS) == {"DO_API_TOKEN", "DO_SPACES_KEY", "DO_SPACES_VALUE"}


# --- rendering -------------------------------------------------------------


def test_render_tfvars_carries_token_and_ip_list():
    out = render_tfvars("dop_v1_secrettoken", ["69.67.149.183", "67.213.124.9"])
    assert 'do_token = "dop_v1_secrettoken"' in out
    assert '"69.67.149.183"' in out
    assert '"67.213.124.9"' in out


def test_render_tfvars_emits_valid_hcl_list():
    out = render_tfvars("t", ["1.2.3.4"])
    assert "allowed_external_ips = [" in out


def test_render_tfvars_refuses_an_empty_allowlist():
    """variables.tf validates length > 0 — fail here with a better message."""
    with pytest.raises(ValueError):
        render_tfvars("t", [])


def test_render_backend_carries_both_spaces_keys():
    out = render_backend("SPACESKEY123", "spaces+secret")
    assert 'access_key = "SPACESKEY123"' in out
    assert 'secret_key = "spaces+secret"' in out


# --- write_credentials -----------------------------------------------------


def test_write_credentials_creates_both_files(tmp_path):
    written = write_credentials(tmp_path, SECRETS, ["69.67.149.183"])
    names = {p.name for p in written}
    assert names == {"terraform.tfvars", "backend.hcl"}
    assert (tmp_path / "terraform.tfvars").exists()
    assert (tmp_path / "backend.hcl").exists()


def test_write_credentials_files_are_owner_only(tmp_path):
    """These are live cloud credentials — 0600, never group- or world-readable."""
    for path in write_credentials(tmp_path, SECRETS, ["69.67.149.183"]):
        mode = stat.S_IMODE(path.stat().st_mode)
        assert mode == 0o600, f"{path.name} is {oct(mode)}"


def test_write_credentials_is_idempotent(tmp_path):
    first = (tmp_path / "terraform.tfvars", tmp_path / "backend.hcl")
    write_credentials(tmp_path, SECRETS, ["69.67.149.183"])
    before = [p.read_text() for p in first]
    write_credentials(tmp_path, SECRETS, ["69.67.149.183"])
    assert [p.read_text() for p in first] == before


def test_write_credentials_rejects_missing_secret(tmp_path):
    partial = dict(SECRETS)
    del partial["DO_SPACES_VALUE"]
    with pytest.raises(KeyError) as excinfo:
        write_credentials(tmp_path, partial, ["1.2.3.4"])
    assert "DO_SPACES_VALUE" in str(excinfo.value)


# --- secrets never reach the log -------------------------------------------


def test_written_paths_are_reported_without_their_contents(tmp_path, capsys):
    """The whole point is these values stay out of anything quotable."""
    write_credentials(tmp_path, SECRETS, ["69.67.149.183"])
    captured = capsys.readouterr()
    assert "dop_v1_secrettoken" not in captured.out + captured.err
    assert "spaces+secret" not in captured.out + captured.err
