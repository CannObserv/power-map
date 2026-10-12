"""Create or update the unit-failure template in Notifier (issue #566).

The wording lives in this repo, ``scripts/notifier/unit-failure.json``; Notifier
stores a copy under power-map's tenant (CannObserv/notifier#114), and
``power-map-notify-failure@.service`` names it by ``NOTIFIER_TEMPLATE_ID``.

Dry run by default: renders the sample through Notifier's ``preview`` and writes
nothing. ``--execute`` creates the template, printing the id to set as
``NOTIFIER_TEMPLATE_ID`` in the handler unit, or with ``--template-id`` updates
that one after an edit to the JSON. Refuses a Notifier whose ``/health`` is not
``production``.

The tenant key arrives on **stdin**, never argv or the environment, read from
the root-only file the handler's ``LoadCredential=`` reads:

Usage:
    sudo cat /etc/power-map/notifier-api.key | uv run python -m scripts.publish_notifier_template
    sudo cat /etc/power-map/notifier-api.key | \\
      uv run python -m scripts.publish_notifier_template --execute [--template-id <ULID>]

Exit codes: 0 published or previewed; 1 no key on stdin; the template not
rendering, or a non-production Notifier, exits with its reason.
"""

import argparse
import asyncio
import json
import sys
from collections.abc import Callable
from typing import Any

from notifier_client import NotifierClient

from scripts.notify_unit_failure import EXPECTED_ENVIRONMENT, TEMPLATE_PATH

DEFAULT_URL = "http://notifier:9000"
WRITTEN_FIELDS = (
    "name",
    "title_template",
    "body_template",
    "variables_schema",
    "sample_variables",
    "tags",
)


async def publish(
    url: str,
    api_key: str,
    template: dict[str, Any],
    *,
    template_id: str,
    execute: bool,
    client_factory: Callable[..., Any] = NotifierClient,
) -> str | None:
    """Preview the template; with ``execute``, create or update it. Returns its id."""
    async with client_factory(base_url=url, api_key=api_key) as client:
        environment = (await client.health()).get("environment")
        if environment != EXPECTED_ENVIRONMENT:
            raise SystemExit(f"notifier reports environment={environment}, expected production")
        preview = await client.preview(
            title_template=template["title_template"],
            body_template=template["body_template"],
            variables=template["sample_variables"],
            variables_schema=template["variables_schema"],
        )
        if preview.error:
            raise SystemExit(f"template does not render: {preview.error}")
        print(f"--- preview\n{preview.title}\n\n{preview.body}\n---")
        if not execute:
            print("dry run: nothing written (pass --execute)")
            return None
        fields = {k: template[k] for k in WRITTEN_FIELDS}
        if template_id:
            out = await client.templates.update(template_id, **fields)
            print(f"updated template {out.id}")
        else:
            out = await client.templates.create(**fields)
            print(f"created template {out.id}; set NOTIFIER_TEMPLATE_ID={out.id}")
        return out.id


def main(
    argv: list[str] | None = None, *, client_factory: Callable[..., Any] = NotifierClient
) -> int:
    """CLI entry point; the key is read from stdin."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--url", default=DEFAULT_URL, help=f"Notifier (default {DEFAULT_URL})")
    parser.add_argument("--template-id", default="", help="update this template instead")
    parser.add_argument("--execute", action="store_true", help="write; default is a dry run")
    args = parser.parse_args(argv)
    api_key = sys.stdin.readline().strip()
    if not api_key:
        print("no Notifier key on stdin", file=sys.stderr)
        return 1
    asyncio.run(
        publish(
            args.url,
            api_key,
            json.loads(TEMPLATE_PATH.read_text()),
            template_id=args.template_id,
            execute=args.execute,
            client_factory=client_factory,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
