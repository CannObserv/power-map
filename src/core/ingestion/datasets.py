"""Pulling usa-wa's published dataset snapshots (#496).

The producer publishes immutable versioned datasets and PM pulls them; that is
the whole cross-repo contract under #490. This module is the consumer half:
read the catalog, fetch what the subscription asks for, verify it, and store it
verbatim.

Four things here are shaped by how this surface actually fails rather than by
how it is specified:

* **The catalog is served from a private exe.dev proxy.** An unauthenticated
  request does not 401 — it answers 307 with an HTML login page, so a puller
  that follows redirects and checks only for a 2xx parses that page as a catalog
  and reports an empty one. Redirects are therefore not followed, the content
  type is asserted, and every auth-shaped outcome is named as authentication so
  the reader looks at the token rather than at usa-wa's publisher.
* **A digest is published prefixed** (``sha256:02a6…``). Kept whole it fails
  later as a content mismatch, which reads as "this file is corrupt" when the
  truth is "nobody checked it".
* **A snapshot lands or it does not.** Files are verified before the version
  directory exists under its final name, so a partially written snapshot is
  never visible to the applier as a complete one.
* **The catalog names the directories this writes to.** A dataset name and a
  version reach ``mkdir(parents=True)`` and ``shutil.rmtree`` straight from a
  fetched document, so both are validated as single path segments — at the
  parse door and again at the write.
"""

import hashlib
import json
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from src.core.logging import get_logger

__all__ = [
    "CATALOG_PATH",
    "CONFORMED_TIER",
    "CatalogEntry",
    "CatalogError",
    "DatasetNotFound",
    "PullReport",
    "SnapshotStore",
    "Subscription",
    "fetch_catalog",
    "parse_catalog",
    "pull",
]

logger = get_logger(__name__)

CATALOG_PATH = "/datasets/catalog.json"

# The exe.dev proxy consumes and strips this before forwarding, so usa-wa's app
# never sees it. `Authorization` also works but is deprecated upstream.
AUTH_HEADER = "X-Exedev-Authorization"

_REQUIRED_FIELDS = ("name", "latest_version", "schema_version", "hash", "rows", "bytes")

# A dataset name and a version are both interpolated straight into filesystem
# paths that `SnapshotStore` mkdirs and rmtrees, and both are read verbatim from
# a document fetched over the network. Anything outside this alphabet — a
# separator, a leading dot, `..` — either escapes the store root or nests a
# directory that `has()` and `versions()` cannot then see. Validated where the
# document is parsed, and asserted again where the path is built.
_SAFE_PATH_SEGMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")

_SHA256_HEX = re.compile(r"[0-9a-f]{64}\Z")

# The default subscription. Staging datasets are the triage and lineage surface
# and the escape hatch for source-granular consumption — never something PM
# applies, so they are opt-in by name rather than by tier.
CONFORMED_TIER = "conformed"

# Every version directory holds these two, verbatim. `data.csv` is the one the
# catalog states a digest for; `datapackage.json` is its schema.
DATA_FILE = "data.csv"
PACKAGE_FILE = "datapackage.json"

# The store's own provenance record, written beside the files it describes.
# A consumer of a pulled snapshot needs the digest and the version that produced
# it; carrying them here means verification never depends on a second network
# round trip, which by then may answer with a different version.
SNAPSHOT_FILE = "snapshot.json"


class CatalogError(RuntimeError):
    """The catalog could not be read, or is not a catalog."""


class DatasetNotFound(CatalogError):
    """The publisher answered 404 — a statement that this document does not exist.

    Distinct from its parent because that statement is the only failure a caller
    may treat as "there is none", as opposed to "nobody could read it just now".
    """


@dataclass(frozen=True)
class CatalogEntry:
    """One dataset as the catalog states it."""

    name: str
    tier: str
    latest_version: str
    schema_version: str
    sha256: str
    rows: int
    bytes: int
    generated_at: str
    derived_from: tuple[str, ...] = ()

    @property
    def schema_major(self) -> int:
        """The pinned half of the schema version — a bump here is a contract break."""
        return int(self.schema_version.split(".", 1)[0])


def _safe_segment(value: object, *, label: str, where: str) -> str:
    """Return ``value`` if it is usable as a single path component."""
    if not isinstance(value, str) or not _SAFE_PATH_SEGMENT.match(value):
        raise CatalogError(f"{where}: unsafe {label} {value!r} — not a single path segment")
    return value


def _schema_version(raw: object, *, where: str) -> str:
    """Return ``raw`` if its major component is the integer the pin compares against."""
    if not isinstance(raw, str) or not raw.split(".", 1)[0].isdigit():
        raise CatalogError(f"{where}: schema_version {raw!r} has no numeric major")
    return raw


def _count(raw: object, *, label: str, where: str) -> int:
    """Coerce a published count, as a `CatalogError` rather than a bare `ValueError`.

    ``label`` rather than ``field``: `dataclasses.field` is imported in this
    module, and a parameter of that name shadows it silently — ruff's F402
    covers loop variables only, which is how the same collision reached a commit
    once already here.
    """
    try:
        return int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise CatalogError(f"{where}: {label} {raw!r} is not a number") from exc


def _digest(raw: object, *, where: str) -> str:
    """Return the bare hex digest from a published `sha256:…` (or bare) hash."""
    if not isinstance(raw, str):
        raise CatalogError(f"{where}: hash {raw!r} is not a sha256 digest")
    algorithm, _, digest = raw.rpartition(":")
    if algorithm and algorithm.lower() != "sha256":
        raise CatalogError(f"{where}: unsupported digest algorithm {algorithm!r}")
    digest = digest.lower()
    # Checked here rather than at comparison time: an empty or malformed digest
    # otherwise surfaces as "digest mismatch — catalog says , downloaded 02a6…",
    # which sends the reader looking for a bug in this module.
    if not _SHA256_HEX.match(digest):
        raise CatalogError(f"{where}: hash {raw!r} is not a sha256 digest")
    return digest


def parse_catalog(payload: dict) -> list[CatalogEntry]:
    """Validate a catalog document and return its entries."""
    if not isinstance(payload, dict) or "datasets" not in payload:
        raise CatalogError("not a catalog: no 'datasets' key")
    datasets = payload["datasets"]
    if not isinstance(datasets, list):
        raise CatalogError("not a catalog: 'datasets' is not a list")

    entries: list[CatalogEntry] = []
    for raw in datasets:
        name = raw.get("name", "<unnamed>") if isinstance(raw, dict) else "<unnamed>"
        if not isinstance(raw, dict):
            raise CatalogError(f"catalog entry {name}: not an object")
        missing = [f for f in _REQUIRED_FIELDS if f not in raw]
        if missing:
            raise CatalogError(f"catalog entry {name}: missing {', '.join(missing)}")
        entries.append(
            CatalogEntry(
                name=_safe_segment(raw["name"], label="name", where=f"catalog entry {name}"),
                tier=raw.get("tier", "unknown"),
                latest_version=_safe_segment(
                    raw["latest_version"],
                    label="latest_version",
                    where=f"catalog entry {name}",
                ),
                schema_version=_schema_version(
                    raw["schema_version"], where=f"catalog entry {name}"
                ),
                sha256=_digest(raw["hash"], where=f"catalog entry {name}"),
                rows=_count(raw["rows"], label="rows", where=f"catalog entry {name}"),
                bytes=_count(raw["bytes"], label="bytes", where=f"catalog entry {name}"),
                generated_at=raw.get("generated_at", ""),
                derived_from=tuple(raw.get("derived_from", ())),
            )
        )
    return entries


def _looks_like_a_login_page(response: httpx.Response) -> bool:
    """The proxy answers an unauthenticated request with HTML, not a 401."""
    content_type = response.headers.get("content-type", "")
    return "html" in content_type.lower() or b"__exe.dev/login" in response.content[:2048]


def _check_response(response: httpx.Response, url: str) -> None:
    """Raise a `CatalogError` that names which of the failures this is."""
    if response.is_redirect or response.status_code in (401, 403):
        raise CatalogError(
            f"{url}: authentication failed (HTTP {response.status_code}) — the exe.dev VM "
            "token is missing, expired, or scoped to another VM"
        )
    if response.status_code == 404:
        raise DatasetNotFound(f"{url}: not found (HTTP 404) — the publisher is not serving this")
    if response.status_code >= 400:
        raise CatalogError(f"{url}: HTTP {response.status_code}")
    if _looks_like_a_login_page(response):
        raise CatalogError(
            f"{url}: authentication failed — the proxy returned its login page with "
            f"HTTP {response.status_code} instead of the document"
        )


async def _get(url: str, token: str, client: httpx.AsyncClient) -> httpx.Response:
    # follow_redirects stays off: following turns an auth failure into whatever
    # the login page happens to return, which is where the silent-empty-catalog
    # failure comes from.
    response = await client.get(
        url, headers={AUTH_HEADER: f"Bearer {token}"}, follow_redirects=False
    )
    _check_response(response, url)
    return response


async def fetch_catalog(
    base_url: str, *, token: str, client: httpx.AsyncClient
) -> list[CatalogEntry]:
    """Fetch and validate the dataset catalog from ``base_url``."""
    url = base_url.rstrip("/") + CATALOG_PATH
    response = await _get(url, token, client)

    content_type = response.headers.get("content-type", "")
    if "json" not in content_type.lower():
        raise CatalogError(f"{url}: unexpected content type {content_type!r} — expected JSON")
    try:
        payload = response.json()
    except json.JSONDecodeError as exc:
        raise CatalogError(f"{url}: response is not valid JSON ({exc})") from exc
    return parse_catalog(payload)


@dataclass(frozen=True)
class Subscription:
    """What PM pulls, and the schema major it is pinned to.

    ``names=None`` means "every conformed product", which is the default the
    #490 design settles on. A named set is how a staging dataset gets pulled at
    all — deliberately, since staging mirrors the wire, contradictions included.
    """

    names: frozenset[str] | None
    schema_major: int

    def wants(self, entry: CatalogEntry) -> bool:
        if self.names is None:
            return entry.tier == CONFORMED_TIER
        return entry.name in self.names


@dataclass
class PullReport:
    """What one pull did, in the shape an operator reads at 08:00."""

    landed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    incompatible: list[tuple[str, str]] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    # Landed, verified, but with no schema file — not a failure (the exit code
    # stays 0), yet the thing #497 will trip over, so the report carries it
    # rather than leaving it to a WARNING among httpx's INFO lines.
    landed_without_package: list[str] = field(default_factory=list)

    @property
    def failed_run(self) -> bool:
        """True when the run must exit non-zero so `systemctl --failed` shows it.

        A subscribed dataset the catalog does not carry counts: a rename that
        pulls nothing looks exactly like a quiet night otherwise.
        """
        return bool(self.failed or self.incompatible or self.missing)


class SnapshotStore:
    """Verbatim, versioned local storage for pulled snapshots.

    A version directory exists only once its contents are verified, so the
    applier can treat presence as completeness.
    """

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    def dataset_dir(self, name: str) -> Path:
        return self.root / name

    def version_dir(self, name: str, version: str) -> Path:
        return self.dataset_dir(name) / version

    def has(self, name: str, version: str) -> bool:
        return self.version_dir(name, version).is_dir()

    def versions(self, name: str) -> list[str]:
        """Stored versions, oldest first. Published versions sort lexically by time."""
        d = self.dataset_dir(name)
        if not d.is_dir():
            return []
        # `.incoming-*` is a staging directory a crashed `land()` left behind —
        # never verified, so never a version. A published version cannot begin
        # with a dot (`_SAFE_PATH_SEGMENT`), so the prefix is unambiguous.
        return sorted(p.name for p in d.iterdir() if p.is_dir() and not p.name.startswith("."))

    def land(self, entry: CatalogEntry, files: dict[str, bytes]) -> Path:
        """Verify ``files`` against ``entry`` and store them as a complete version.

        Written to a sibling staging directory and moved into place, so a failed
        verification or a crash mid-write leaves no directory rather than a
        half-populated one.
        """
        for value, label in ((entry.name, "name"), (entry.latest_version, "latest_version")):
            # `parse_catalog` guards the catalog; this guards every other caller,
            # because what follows is a `mkdir(parents=True)` and an `rmtree`.
            if not _SAFE_PATH_SEGMENT.match(value):
                raise ValueError(f"unsafe {label} {value!r} — not a single path segment")

        data = files.get(DATA_FILE)
        if data is None:
            raise ValueError(f"{entry.name} {entry.latest_version}: no {DATA_FILE} to verify")
        if len(data) != entry.bytes:
            # Checked before the digest because it names the failure better: a
            # truncated transfer reads as "10 bytes, not 105" rather than as two
            # unequal hashes. It is also the only thing that reads `bytes`, which
            # the catalog requires and nothing else consults.
            raise ValueError(
                f"{entry.name} {entry.latest_version}: length mismatch — "
                f"catalog says {entry.bytes} bytes, downloaded {len(data)}"
            )
        actual = hashlib.sha256(data).hexdigest()
        if actual != entry.sha256:
            raise ValueError(
                f"{entry.name} {entry.latest_version}: digest mismatch — "
                f"catalog says {entry.sha256}, downloaded {actual}"
            )

        final = self.version_dir(entry.name, entry.latest_version)
        staging = final.with_name(f".incoming-{entry.latest_version}")
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True)
        for filename, payload in files.items():
            (staging / filename).write_bytes(payload)
        (staging / SNAPSHOT_FILE).write_text(
            json.dumps(
                {
                    "name": entry.name,
                    "version": entry.latest_version,
                    "tier": entry.tier,
                    "schema_version": entry.schema_version,
                    "sha256": entry.sha256,
                    "rows": entry.rows,
                    "generated_at": entry.generated_at,
                },
                indent=2,
            )
            + "\n"
        )
        # Re-landing repairs a corrupted local copy, so replacing is supported.
        if final.exists():
            shutil.rmtree(final)
        os.replace(staging, final)
        return final

    def prune(self, name: str, *, keep: int, keep_version: str | None) -> list[str]:
        """Drop all but the newest ``keep`` versions, always sparing ``keep_version``.

        ``keep_version`` is a version the caller wants kept whatever ``keep``
        says. Nothing yet records which snapshot the applier last applied, so
        the puller passes the newest **published** version — enough to stop a
        small ``--keep`` deleting what the run just fetched, but not the same
        guarantee. Protecting the applied version is #499's to add once there is
        something that knows it.
        """
        stored = self.versions(name)
        survivors = set(stored[-keep:]) if keep > 0 else set()
        if keep_version is not None:
            survivors.add(keep_version)
        removed = [v for v in stored if v not in survivors]
        for version in removed:
            shutil.rmtree(self.version_dir(name, version))
        return removed


async def _fetch_file(
    base_url: str,
    entry: CatalogEntry,
    filename: str,
    token: str,
    client: httpx.AsyncClient,
) -> bytes:
    url = f"{base_url.rstrip('/')}/datasets/{entry.name}/{entry.latest_version}/{filename}"
    response = await _get(url, token, client)
    return response.content


async def pull(
    base_url: str,
    catalog: list[CatalogEntry],
    store: SnapshotStore,
    *,
    token: str,
    client: httpx.AsyncClient,
    subscription: Subscription,
) -> PullReport:
    """Fetch every subscribed dataset the store does not already hold."""
    report = PullReport()
    seen: set[str] = set()

    for entry in catalog:
        if not subscription.wants(entry):
            continue
        seen.add(entry.name)

        # Everything per-entry lives inside the guard, `schema_major` included:
        # it parses the version string, and a failure there once aborted the whole
        # run after earlier datasets had already landed.
        try:
            if entry.schema_major != subscription.schema_major:
                # Not landed, and not silently skipped either: a major bump means
                # the mapping models were written against a shape that no longer
                # holds.
                report.incompatible.append((entry.name, entry.schema_version))
                continue

            if store.has(entry.name, entry.latest_version):
                report.skipped.append(entry.name)
                continue

            files = {DATA_FILE: await _fetch_file(base_url, entry, DATA_FILE, token, client)}
            try:
                files[PACKAGE_FILE] = await _fetch_file(
                    base_url, entry, PACKAGE_FILE, token, client
                )
            except DatasetNotFound:
                # Only a 404 — the publisher stating there is no schema file. The
                # digest covers data.csv, so landing without it is right. Anything
                # else (a 500, an expired token) must fail the dataset instead:
                # `store.has()` is true once a version lands and hash-skip never
                # re-fetches it, so a one-second blip would otherwise leave a
                # permanently schema-less snapshot for #497 to read.
                logger.warning(
                    "%s %s: no %s published — landing without it",
                    entry.name,
                    entry.latest_version,
                    PACKAGE_FILE,
                )
                report.landed_without_package.append(entry.name)
            store.land(entry, files)
        except (CatalogError, ValueError, httpx.HTTPError, OSError) as exc:
            # OSError included: `land()` is all filesystem calls, and a full
            # disk should fail its dataset like any other cause rather than
            # abandon the datasets after it.
            report.failed.append((entry.name, str(exc)))
            continue
        report.landed.append(entry.name)

    if subscription.names is not None:
        report.missing = sorted(subscription.names - seen)
    return report
