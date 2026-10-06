# power-map-client

A Python client for the [power-map](https://github.com/CannObserv/power-map)
public API (`/api/v1/*` and `/health`). It is generated with
[`openapi-python-client`](https://github.com/openapi-generators/openapi-python-client)
from the schema that power-map publishes at `/openapi.json`.

## Install

Pin a release tag and the subdirectory:

```toml
[project]
dependencies = ["power-map-client"]

[tool.uv.sources]
power-map-client = { git = "https://github.com/CannObserv/power-map.git", subdirectory = "clients/python", tag = "v0.59.0" }
```

`CannObserv/power-map` is public, so installing needs no credential. To move to
a later release, change the tag and run `uv sync`.

## Versioning

The client and the server have **one version**. `power-map-client` X.Y.Z is
generated from power-map X.Y.Z, and the tag `vX.Y.Z` pins it. power-map's own
gates enforce this. To check that a deployment matches your pin, compare the
`build` field of `GET /health` with `power_map_client.__version__`.

A release that changes the public schema shows the change in
`clients/python/openapi.json` in that release's diff. Read the diff before you
move your pin.

## Usage

```python
from power_map_client import connect
from power_map_client.generated.api.public_api import get_org, search_orgs
from power_map_client.generated.models import EntityGone, ErrorDetail, OrgDetail

client = connect("https://power-map.exe.xyz", api_key="pm_...")

page = search_orgs.sync(client=client, q="liquor")

r = get_org.sync_detailed(org_id="01H...", client=client)
if r.status_code == 200:
    org: OrgDetail = r.parsed
    etag = r.headers["etag"]
elif r.status_code == 410:
    gone: EntityGone = r.parsed      # merged_into names where the id went
elif r.status_code in (401, 403, 404, 429):
    err: ErrorDetail = r.parsed

# Revalidate: 304 with no body while the ETag still matches.
r = get_org.sync_detailed(org_id="01H...", client=client, if_none_match=etag)
```

Every function comes in four forms: `sync`, `sync_detailed`, `asyncio` and
`asyncio_detailed`. Use the `_detailed` forms when the status code matters. Each
response the API declares has a type: `ErrorDetail` (`{detail}`) for
401/403/404/409/429, `HTTPValidationError` for 422 (its `detail` is a list of
field errors or a single message), and `EntityGone` for 410.

`connect()` exists because the generated `AuthenticatedClient` sends
`Authorization: Bearer <token>` by default, while the API reads the key from
`X-API-Key`. Any other `AuthenticatedClient` option, such as `timeout` or
`httpx_args`, passes through `connect()`.

### Models are attrs, not Pydantic

The generator emits [attrs](https://www.attrs.org) classes. To use Pydantic
models in an adapter, convert each one through a dict:
`MyModel.model_validate(generated.to_dict())`.

### What stays in your adapter

This package has no retries, no rate-limit backoff and no error taxonomy. Each
consumer writes those for its own needs.

## Development

Run everything from the power-map repo root. `src/power_map_client/generated/`
is regenerated output and must never be edited by hand. After a change to the
public schema or a version bump:

```bash
uv run python -m scripts.regenerate_client
```

That command rewrites `openapi.json`, `generated/` and this package's version.
`tests/clients/python/test_drift.py` fails until it has been run.
