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
  never visible to the applier as a complete one. Each landing stages into its
  own directory, so two runs of the same version cannot promote each other's
  half-written one.
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
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import httpx
import yaml

from src.core.logging import get_logger

__all__ = [
    "CATALOG_PATH",
    "Catalog",
    "CatalogEntry",
    "CatalogError",
    "DatasetNotFound",
    "PINS_PATH",
    "PINS_SOURCE",
    "PULL_FILE",
    "Pin",
    "PullReport",
    "SnapshotStore",
    "Subscription",
    "fetch_catalog",
    "fmt_moment",
    "load_subscription",
    "parse_catalog",
    "parse_moment",
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

# Where each subscribed dataset is pinned (#536): `meta` on its source in the
# mapping project, beside the `read_csv` that depends on its shape, so a model
# change and its pin change land in one diff. Located by path rather than by
# import, because `src.core.ingestion.mapping` imports dbt and the puller's
# environment does not carry it.
PINS_PATH = Path(__file__).resolve().parent / "mapping" / "models" / "sources.yml"
PINS_SOURCE = "usa_wa"

# Every version directory holds these two, verbatim. `data.csv` is the one the
# catalog states a digest for; `datapackage.json` is its schema.
DATA_FILE = "data.csv"
PACKAGE_FILE = "datapackage.json"

# The store's own provenance record, written beside the files it describes.
# A consumer of a pulled snapshot needs the digest and the version that produced
# it; carrying them here means verification never depends on a second network
# round trip, which by then may answer with a different version.
SNAPSHOT_FILE = "snapshot.json"

# The pull's own run record, at the store root rather than inside a version:
# it describes the run, not a snapshot. `versions()` lists directories only, so
# a file here is invisible to every reader of the store's datasets.
PULL_FILE = "pull.json"

# What the wire and every record here spell a moment as (#440).
TS_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


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
    # The published contract's fingerprint (usa-wa#385), bare like `sha256`.
    # None when an entry carries none; whether that matters is the pin's call.
    contract_hash: str | None = None

    @property
    def schema_major(self) -> int:
        """The major its pin compares; meaningful only within one dataset (usa-wa#385)."""
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


def _digest(raw: object, *, where: str, label: str = "hash") -> str:
    """Return the bare hex digest from a published `sha256:…` (or bare) hash."""
    if not isinstance(raw, str):
        raise CatalogError(f"{where}: {label} {raw!r} is not a sha256 digest")
    algorithm, _, digest = raw.rpartition(":")
    if algorithm and algorithm.lower() != "sha256":
        raise CatalogError(f"{where}: {label} uses unsupported digest algorithm {algorithm!r}")
    digest = digest.lower()
    # Checked here rather than at comparison time: an empty or malformed digest
    # otherwise surfaces as "digest mismatch — catalog says , downloaded 02a6…",
    # which sends the reader looking for a bug in this module.
    if not _SHA256_HEX.match(digest):
        raise CatalogError(f"{where}: {label} {raw!r} is not a sha256 digest")
    return digest


def parse_moment(raw: object, *, label: str) -> datetime | None:
    """Parse a published timestamp, or None when the document states none.

    Raises `CatalogError` on anything else: unparseable, it would surface as a
    TypeError at the comparison instead.
    """
    if raw is None:
        return None
    try:
        moment = datetime.fromisoformat(str(raw))
    except ValueError as exc:
        raise CatalogError(f"{label} {raw!r} is not a timestamp ({exc})") from exc
    # Everything on this wire is UTC (#440). A published value with no zone is
    # read as one rather than refused, because naive-vs-aware is a TypeError at
    # the comparison — a crash a long way from the document that caused it.
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


@dataclass(frozen=True)
class Catalog:
    """A catalog document: its entries, and the publisher's heartbeat (#551).

    `checked_at` says the publisher completed a run, and advances every night
    whether or not anything minted. `stale_after` is the deadline for the next
    one — the next scheduled run plus its grace. Both are absent from every
    catalog published before usa-wa#386, which is why they are optional and why
    an absent deadline is never late.

    A fresh `checked_at` covers the publisher, not the whole chain upstream of
    it: a failed source harvest and a registrar conflict both leave it fresh
    (usa-wa `docs/PIPELINE-PUBLICATION.md` § The catalog's heartbeat).
    """

    entries: tuple[CatalogEntry, ...]
    checked_at: datetime | None = None
    stale_after: datetime | None = None

    def stale(self, now: datetime) -> bool:
        """True when the publisher has missed its own deadline for the next run."""
        return self.stale_after is not None and now > self.stale_after

    def lateness(self, now: datetime) -> str:
        """The finding a stale heartbeat reads as, for the log and the record."""
        return (
            f"usa-wa is behind the clock: it last completed a run at "
            f"{fmt_moment(self.checked_at)} and undertook to publish the next by "
            f"{fmt_moment(self.stale_after)} — {now - self.stale_after} late"
        )


def fmt_moment(moment: datetime | None) -> str | None:
    """A parsed moment back in the spelling every record here uses (#440)."""
    return None if moment is None else moment.astimezone(UTC).strftime(TS_FORMAT)


def parse_catalog(payload: dict) -> Catalog:
    """Validate a catalog document and return its entries and heartbeat."""
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
                contract_hash=(
                    _digest(
                        raw["contract_hash"],
                        where=f"catalog entry {name}",
                        label="contract_hash",
                    )
                    # `null` is JSON's spelling of "none": absent, not malformed,
                    # or one entry nobody subscribes to refuses the whole catalog.
                    if raw.get("contract_hash") is not None
                    else None
                ),
            )
        )
    return Catalog(
        entries=tuple(entries),
        checked_at=parse_moment(payload.get("checked_at"), label="catalog checked_at"),
        stale_after=parse_moment(payload.get("stale_after"), label="catalog stale_after"),
    )


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


async def fetch_catalog(base_url: str, *, token: str, client: httpx.AsyncClient) -> Catalog:
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
class Pin:
    """The contract PM's mapping models were written against, for one dataset."""

    schema_major: int
    contract_hash: str

    def __post_init__(self) -> None:
        # Compared verbatim with the catalog's hash, which `parse_catalog` makes
        # bare and lowercase. Any other spelling could never match, and would
        # read every night as a contract change upstream.
        if not _SHA256_HEX.match(self.contract_hash):
            raise ValueError(
                f"contract_hash {self.contract_hash!r} must be a bare lowercase sha256 digest"
            )


@dataclass(frozen=True)
class Subscription:
    """What PM pulls: exactly the pinned datasets, each held to its own contract."""

    pins: Mapping[str, Pin]

    def wants(self, entry: CatalogEntry) -> bool:
        return entry.name in self.pins

    def refusal(self, entry: CatalogEntry) -> str | None:
        """Why ``entry`` must not land under its pin, or None when it may.

        The hash is the gate and the major only words the refusal: usa-wa's own
        gate demands *a* bump for a contract change, not the right one, so a
        major that did not move proves nothing about the shape.
        """
        pin = self.pins[entry.name]
        # Both refusals that follow a moved contract name the published pair, so
        # the line is everything a re-pin needs.
        contract = (
            f"contract sha256:{entry.contract_hash}" if entry.contract_hash else "no contract_hash"
        )
        published = f"schema {entry.schema_version}, {contract}"
        if entry.schema_major != pin.schema_major:
            return (
                f"publishes {published}; pinned to major {pin.schema_major}:"
                " the mapping models need a change before it lands"
            )
        if entry.contract_hash is None:
            return (
                f"publishes no contract_hash, pinned to sha256:{pin.contract_hash}:"
                " the publisher stopped stating its contract"
            )
        if entry.contract_hash != pin.contract_hash:
            return (
                f"contract changed within major {pin.schema_major} ({published}):"
                " review it, then re-pin"
            )
        return None


def load_subscription(path: Path | str = PINS_PATH, *, source: str = PINS_SOURCE) -> Subscription:
    """Pin every table of ``source`` in a dbt sources file by its ``meta``.

    A table with no pin is an error rather than an omission: the puller would
    never fetch it, and the model reading it would build from whatever the store
    last held — the silent staleness the pin exists to prevent.
    """
    document = yaml.safe_load(Path(path).read_text()) or {}
    tables = next(
        (s.get("tables") or [] for s in document.get("sources") or [] if s.get("name") == source),
        None,
    )
    if not tables:
        raise ValueError(f"{path}: no tables under a source named {source!r} to subscribe to")

    pins: dict[str, Pin] = {}
    for table in tables:
        where = f"{path}: {source}.{table['name']}"
        meta = table.get("meta") or {}
        major = meta.get("schema_major")
        if type(major) is not int:  # `bool` is an int; `True` is not a major
            raise ValueError(f"{where}: meta.schema_major {major!r} is not an integer")
        try:
            contract = _digest(meta.get("contract_hash"), where=where, label="meta.contract_hash")
        except CatalogError as exc:
            # Raised as a pin error, not a catalog one: `main` reads a
            # `CatalogError` as "the publisher's document is unreadable".
            raise ValueError(str(exc)) from None
        pins[table["name"]] = Pin(major, contract)
    return Subscription(pins)


@dataclass
class PullReport:
    """What one pull did, in the shape an operator reads at 08:00."""

    landed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    # (name, why its pin refused it) — `Subscription.refusal`'s sentence.
    incompatible: list[tuple[str, str]] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    # Landed, verified, but with no schema file — not a failure (the exit code
    # stays 0), yet the thing #497 will trip over, so the report carries it
    # rather than leaving it to a WARNING among httpx's INFO lines.
    landed_without_package: list[str] = field(default_factory=list)
    # `Catalog.lateness`'s sentence when the publisher missed its own deadline
    # (#551), else None. Not per-dataset: it is one statement about the producer.
    producer_stale: str | None = None

    @property
    def failed_run(self) -> bool:
        """True when the run must exit non-zero so `systemctl --failed` shows it.

        A subscribed dataset the catalog does not carry counts: a rename that
        pulls nothing looks exactly like a quiet night otherwise. So does a
        producer past its heartbeat deadline (#551) — every dataset then reads
        `unchanged`, which is the same green a settled night gives.
        """
        return bool(self.failed or self.incompatible or self.missing or self.producer_stale)


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

    def record_pull(self, catalog: Catalog, *, at: datetime) -> dict:
        """Write the run record: what the publisher's heartbeat said at ``at`` (#551).

        Staleness itself is not recorded, only derived: `stale_after` is a fixed
        deadline, so a reader at any later moment reaches the same verdict the
        pull did — and a build hours or days later reaches a truer one.
        """
        record = {
            "pulled_at": fmt_moment(at),
            "checked_at": fmt_moment(catalog.checked_at),
            "stale_after": fmt_moment(catalog.stale_after),
        }
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / PULL_FILE).write_text(json.dumps(record, indent=2) + "\n")
        return record

    def pull_record(self) -> dict | None:
        """The last pull's record, or None when no readable one exists.

        Unreadable reads as absent: this is provenance for a build, and a
        truncated write must not be the thing that stops one.
        """
        path = self.root / PULL_FILE
        try:
            return json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            return None

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
        for filename in files:
            # The third path input into this function. `pull` passes module
            # constants, so this is for whoever calls `land()` next.
            if not _SAFE_PATH_SEGMENT.match(filename):
                raise ValueError(f"unsafe filename {filename!r} — not a single path segment")

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
        final.parent.mkdir(parents=True, exist_ok=True)
        # One staging directory per landing, not per version: a name derived from
        # the version alone is shared by every process, so the nightly timer and
        # a manual run landing the same version race — one deletes the other's
        # half-written directory, and either can then `os.replace` it into place
        # as a complete snapshot. `versions()` ignores dotted names, so the
        # prefix keeps these invisible as versions.
        staging = Path(
            tempfile.mkdtemp(prefix=f".incoming-{entry.latest_version}-", dir=final.parent)
        )
        staging.chmod(0o755)  # mkdtemp is 0700; landed versions were always 0755
        try:
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
                        "contract_hash": entry.contract_hash,
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
        finally:
            # A no-op once `os.replace` has moved it; on any failure it is the
            # difference between one inert directory per failed write and none.
            shutil.rmtree(staging, ignore_errors=True)
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

        # Everything per-entry lives inside the guard, the pin check included:
        # `schema_major` parses the version string, and a failure there once
        # aborted the whole run after earlier datasets had already landed.
        try:
            reason = subscription.refusal(entry)
            if reason is not None:
                # Not landed, and not silently skipped either: the mapping models
                # were written against a contract this version no longer states.
                report.incompatible.append((entry.name, reason))
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

    report.missing = sorted(set(subscription.pins) - seen)
    return report
