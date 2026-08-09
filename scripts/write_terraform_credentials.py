"""Rebuild terraform's gitignored credential files from /etc/power-map/.env (#409).

Terraform needs two files that are deliberately never committed:

- ``infra/terraform/terraform.tfvars`` — ``do_token`` + ``allowed_external_ips``
- ``infra/terraform/backend.hcl`` — the DO Spaces ``access_key``/``secret_key``
  that reach the remote state

On 2026-08-09 both turned out to be absent from the VM, with no record anywhere
of where their contents lived. The remote state (``co-pm-spaces-1`` bucket) was
intact the whole time; only the keys to reach it were gone. The cost was real:
allowlist edits became console-only and therefore drifted, and
``scripts/write-db-secrets.sh`` — which opens with ``terraform output -json`` —
could not run at all, blocking credential rotation.

Custody now sits in ``/etc/power-map/.env`` (root-owned, 0640, group ``exedev``),
beside the database credentials, under ``DO_API_TOKEN``, ``DO_SPACES_KEY`` and
``DO_SPACES_VALUE``. This script turns the recovery into one command.

The allowlist is read from the DigitalOcean API rather than typed in, because
the live Trusted Sources list is authoritative — a reconstruction that guessed
it would produce a plan proposing to *remove* rules from a running cluster.
Override with ``--allowed-ips`` when rebuilding deliberately.

Nothing here writes to DigitalOcean or to the database: it makes two local
files and performs two read-only API calls. Secret *values* are never logged —
only the paths written.

Usage:
    uv run python -m scripts.write_terraform_credentials
    uv run python -m scripts.write_terraform_credentials --allowed-ips 1.2.3.4,5.6.7.8
    terraform -chdir=infra/terraform init -backend-config=backend.hcl
"""

import argparse
import json
import os
import sys
from pathlib import Path

from scripts._do_api import DEFAULT_CLUSTER, fetch_allowed_ips
from src.core.logging import configure_logging, get_logger

logger = get_logger(__name__)

DEFAULT_ENV_FILE = Path("/etc/power-map/.env")
REQUIRED_KEYS = ("DO_API_TOKEN", "DO_SPACES_KEY", "DO_SPACES_VALUE")


def parse_env(text: str) -> dict[str, str]:
    """Parse ``KEY=value`` lines, stripping comments, blanks and wrapping quotes.

    Splits on the **first** ``=`` only: Spaces secrets and DSN query strings
    both contain further ones.
    """
    parsed: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        parsed[key.strip()] = value
    return parsed


def render_tfvars(token: str, allowed_ips: list[str]) -> str:
    """Render ``terraform.tfvars``."""
    if not allowed_ips:
        # variables.tf validates this too, but failing here names the cause.
        raise ValueError(
            "allowed_external_ips would be empty — the cluster would become unreachable"
        )
    # json.dumps yields a correctly-escaped HCL string literal. A raw f-string
    # let a quote in the value end the literal early and append arbitrary HCL
    # (CR1 finding 6).
    entries = ", ".join(json.dumps(ip) for ip in allowed_ips)
    return (
        f"do_token = {json.dumps(token)}\n\n"
        "# Mirrors DO -> Databases -> Settings -> Trusted Sources, read from the API\n"
        f"# by scripts/write_terraform_credentials.py (#409).\n"
        f"allowed_external_ips = [{entries}]\n"
    )


def render_backend(access_key: str, secret_key: str) -> str:
    """Render ``backend.hcl`` — the DO Spaces credentials for the S3 backend."""
    return f"access_key = {json.dumps(access_key)}\nsecret_key = {json.dumps(secret_key)}\n"


def _write_private(path: Path, content: str) -> Path:
    """Write ``content`` to ``path`` at 0600, restrictive from creation."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(content)
    os.chmod(path, 0o600)  # existing files keep their old mode through O_CREAT
    return path


def write_credentials(dest_dir, secrets, allowed_ips: list[str]) -> list[Path]:
    """Write both credential files into ``dest_dir``; return the paths written."""
    for key in REQUIRED_KEYS:
        if key not in secrets:
            raise KeyError(key)
    dest = Path(dest_dir)
    return [
        _write_private(
            dest / "terraform.tfvars", render_tfvars(secrets["DO_API_TOKEN"], allowed_ips)
        ),
        _write_private(
            dest / "backend.hcl",
            render_backend(secrets["DO_SPACES_KEY"], secrets["DO_SPACES_VALUE"]),
        ),
    ]


def main() -> None:
    """CLI entry point — exits 2 when a credential is missing."""
    configure_logging()
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--env-file", type=Path, default=DEFAULT_ENV_FILE, help=f"default {DEFAULT_ENV_FILE}"
    )
    parser.add_argument(
        "--dest", type=Path, default=repo_root / "infra" / "terraform", help="terraform directory"
    )
    parser.add_argument("--cluster", default=DEFAULT_CLUSTER, help=f"default {DEFAULT_CLUSTER}")
    parser.add_argument(
        "--allowed-ips",
        default="",
        help="comma-separated override; default reads the live Trusted Sources",
    )
    args = parser.parse_args()

    if not args.env_file.exists():
        logger.error("%s not found — nothing to rebuild from", args.env_file)
        sys.exit(2)
    secrets = parse_env(args.env_file.read_text())
    missing = [k for k in REQUIRED_KEYS if not secrets.get(k)]
    if missing:
        logger.error(
            "%s is missing %s — see docs/COMMANDS.md § Provisioning for where these live",
            args.env_file,
            ", ".join(missing),
        )
        sys.exit(2)

    if args.allowed_ips:
        allowed = [ip.strip() for ip in args.allowed_ips.split(",") if ip.strip()]
        logger.info("using the supplied allowlist (%d entr(ies))", len(allowed))
    else:
        allowed = fetch_allowed_ips(secrets["DO_API_TOKEN"], args.cluster)
        logger.info(
            "read %d Trusted Source(s) from cluster %s: %s",
            len(allowed),
            args.cluster,
            ", ".join(allowed),
        )

    written = write_credentials(args.dest, secrets, allowed)
    for path in written:
        logger.info("wrote %s (0600)", path)
    logger.info(
        "next: terraform -chdir=%s init -backend-config=backend.hcl && terraform -chdir=%s plan",
        args.dest,
        args.dest,
    )


if __name__ == "__main__":
    main()
