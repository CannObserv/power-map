"""Read-only DigitalOcean API helpers shared by scripts (#409, #410).

Two callers, one shape: ``write_terraform_credentials`` seeds
``allowed_external_ips`` from the live Trusted Sources, and ``check_egress_ip``
compares this host's egress address against them. Both want the same
authoritative answer, and neither writes anything.

The token lives in ``/etc/power-map/.env`` as ``DO_API_TOKEN``. It is scoped:
databases and VPCs read, no account/projects/spaces access.
"""

import json
import urllib.request
from urllib.request import urlopen

API_ROOT = "https://api.digitalocean.com/v2"
DEFAULT_CLUSTER = "co-pm-db-1"
DEFAULT_TIMEOUT = 30.0
# Large enough that a single page covers any realistic account.
PAGE_SIZE = 200


def _request(url: str, token: str) -> urllib.request.Request:
    """Build an authenticated DO API request."""
    return urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})


def _get(url: str, token: str, *, opener, timeout: float) -> dict:
    with opener(_request(url, token), timeout=timeout) as response:
        return json.loads(response.read())


def fetch_allowed_ips(
    token: str, cluster_name: str, *, opener=None, timeout: float = DEFAULT_TIMEOUT
) -> list[str]:
    """Return the cluster's Trusted Sources, ``ip_addr`` rules only.

    A firewall may also carry droplet/k8s/tag rules; those are not addresses,
    so they must not reach ``allowed_external_ips`` or an egress comparison.
    """
    opener = opener or urlopen
    # DO pages at 20 by default, so a cluster past page 1 used to look absent
    # (CR1 finding 3). Ask for a large page, then follow the cursor anyway.
    url = f"{API_ROOT}/databases?per_page={PAGE_SIZE}"
    match = None
    while url and match is None:
        page = _get(url, token, opener=opener, timeout=timeout)
        match = next((c for c in page.get("databases", []) if c.get("name") == cluster_name), None)
        url = page.get("links", {}).get("pages", {}).get("next")
    if match is None:
        raise LookupError(f"no DigitalOcean database cluster named {cluster_name!r}")
    firewall = _get(
        f"{API_ROOT}/databases/{match['id']}/firewall", token, opener=opener, timeout=timeout
    )
    return [r["value"] for r in firewall.get("rules", []) if r.get("type") == "ip_addr"]
