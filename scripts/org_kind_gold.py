"""The org-kind gold set: sample, label, score (#877 phase 2b).

Design D6 (``docs/plans/2026-11-24-org-kind-suggestions-design.md``) and plan
step 8 (``docs/plans/2026-11-26-877-phase-2-jev-suggestions.md``).

``sample`` draws about 300 active orgs, read-only, into a **private
worksheet**: org id, stratum, display name, the registries that list it, its
active role titles, and blank ``kind`` / ``unsure`` / ``note`` columns to
label. Each org takes the first stratum it matches, in ``QUOTAS`` order, and
each stratum keeps its first N orgs by ``md5('gold-877:' || id)``. The salt
keeps the sample away from the feasibility spike's unsalted ordering. Labels
are drafted from D1's definitions **before any Jev call on these orgs**, then
the operator reviews the unsure rows and a seeded random 30.

The repo is public: the worksheet must resolve outside every git work tree,
or the run refuses before connecting. Only ``(org_id, kind)`` is ever
committed, at ``docs/research/org-kind/gold.csv``.

``review`` picks the rows the operator checks: every row marked unsure, plus
``REVIEW_RANDOM`` of the rest by ``md5('review-877:' || id)``. They go into a
private review sheet with a blank ``reviewed_kind`` column.

Usage:
    uv run python -m scripts.org_kind_gold sample --out ~/gold-877/worksheet.csv
    uv run python -m scripts.org_kind_gold review --worksheet ~/gold-877/worksheet.csv \
        --out ~/gold-877/review.csv
"""

import asyncio
import csv
import hashlib
import json
import os
import subprocess
from dataclasses import astuple, dataclass
from pathlib import Path

import asyncpg

from scripts._dsn import add_dsn_args, build_parser, resolve_dsn
from src.core.logging import configure_logging, get_logger

logger = get_logger(__name__)

SALT = "gold-877:"

# Stratum → how many orgs it keeps. Order is priority: an org takes the first
# stratum it matches. Small strata are taken whole.
QUOTAS: dict[str, int] = {
    "recent": 11,  # created in the last 90 days
    "party": 8,  # org_wa_party
    "pdc_committee": 3,  # org_wa_pdc_committee
    "wslcb": 13,  # org_wslcb (rules say business; tribal licensees are the known risk)
    "ubi": 12,  # org_ubi
    "pdc": 40,  # org_wa_pdc (lobbyist employers of every kind)
    "legislature": 25,  # org_wa_legislature*
    "ambiguous_title": 80,  # no identifier above, holds a #303 D2 ambiguous title
    "no_identifier": 110,  # everything else
}

# #303 design D2: titles that mean different things in different kinds of org.
AMBIGUOUS_TITLES = (
    "member",
    "chair",
    "chairman",
    "co-chair",
    "vice chair",
    "director",
    "president",
    "principal",
    "partner",
    "managing partner",
    "managing director",
    "treasurer",
    "secretary",
    "founding member",
    "advisory board member",
    "principal officer",
)

WORKSHEET_COLUMNS = (
    "org_id",
    "stratum",
    "name",
    "listed_in",
    "role_titles",
    "kind",
    "unsure",
    "note",
)
TITLES_CHARS = 600

# The kinds a label may name: org_kinds' seeded slugs plus the question's `none`.
KINDS = frozenset(
    {
        "business",
        "nonprofit",
        "association",
        "coalition",
        "labor_union",
        "government",
        "legislature",
        "political_party",
        "campaign_committee",
        "tribe",
        "academic",
        "none",
    }
)
REVIEW_SALT = "review-877:"
REVIEW_RANDOM = 30
REVIEW_COLUMNS = (*WORKSHEET_COLUMNS, "reviewed_kind")


class PrivatePathError(ValueError):
    """A worksheet path that could land in a git work tree."""


def _in_work_tree(path: Path) -> bool | None:
    """True inside a git work tree, False outside, None when git can't say.

    The nearest existing ancestor is asked after symlinks resolve, with every
    GIT_ variable dropped (they answer for another repo). As the vendored
    ``typesafe-eval.py`` guard does.
    """
    probe = Path(os.path.realpath(path))
    while not probe.is_dir():
        probe = probe.parent
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["LC_ALL"] = "C"
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=probe,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    if out.returncode == 0:
        return out.stdout.strip() == "true"
    return False if "not a git repository" in out.stderr else None


def require_private(path: Path) -> None:
    """Refuse *path* unless it is plainly outside every git work tree."""
    inside = _in_work_tree(path)
    if inside is not False:
        reason = "is inside a git work tree" if inside else "can't be checked by git"
        raise PrivatePathError(
            f"{path} {reason}: the gold-set worksheet holds org names and must stay private"
        )


@dataclass(frozen=True)
class SampleRow:
    """One sampled org, as the worksheet shows it (labels blank)."""

    org_id: str
    stratum: str
    name: str
    listed_in: str
    role_titles: str


_SAMPLE_SQL = """
WITH o AS (
    SELECT id, created_at FROM organizations WHERE archived_at IS NULL
),
slugs AS (
    SELECT i.entity_id, array_agg(DISTINCT t.slug) AS slugs,
           string_agg(DISTINCT t.full_name, '; ') AS listed_in
    FROM identifiers i
    JOIN entity_identifier_types t ON t.id = i.entity_identifier_type_id
    WHERE t.entity_type = 'organization' AND NOT t.is_internal
    GROUP BY i.entity_id
),
amb AS (
    SELECT DISTINCT organization_id FROM roles
    WHERE archived_at IS NULL AND lower(title) = ANY($1::text[])
),
strat AS (
    SELECT o.id, s.listed_in,
           CASE
               WHEN o.created_at > now() - interval '90 days' THEN 'recent'
               WHEN 'org_wa_party' = ANY(s.slugs) THEN 'party'
               WHEN 'org_wa_pdc_committee' = ANY(s.slugs) THEN 'pdc_committee'
               WHEN 'org_wslcb' = ANY(s.slugs) THEN 'wslcb'
               WHEN 'org_ubi' = ANY(s.slugs) THEN 'ubi'
               WHEN 'org_wa_pdc' = ANY(s.slugs) THEN 'pdc'
               WHEN EXISTS (SELECT 1 FROM unnest(s.slugs) x WHERE x LIKE 'org_wa_legislature%')
                   THEN 'legislature'
               WHEN amb.organization_id IS NOT NULL THEN 'ambiguous_title'
               ELSE 'no_identifier'
           END AS stratum
    FROM o
    LEFT JOIN slugs s ON s.entity_id = o.id
    LEFT JOIN amb ON amb.organization_id = o.id
),
ranked AS (
    SELECT strat.*,
           row_number() OVER (PARTITION BY stratum ORDER BY md5($2 || id), id) AS n
    FROM strat
)
SELECT r.id, r.stratum, COALESCE(v.display_name, '') AS name,
       COALESCE(r.listed_in, '') AS listed_in,
       COALESCE((SELECT string_agg(title, '; ' ORDER BY lower(title), title)
                 FROM roles WHERE organization_id = r.id AND archived_at IS NULL), '')
           AS role_titles
FROM ranked r
JOIN jsonb_each_text($3::jsonb) q ON q.key = r.stratum
LEFT JOIN v_org_display_names v ON v.organization_id = r.id
WHERE r.n <= q.value::int
ORDER BY array_position($4::text[], r.stratum), r.n
"""


async def sample(db: asyncpg.Connection) -> list[SampleRow]:
    """Draw the stratified sample (read-only)."""
    rows = await db.fetch(
        _SAMPLE_SQL,
        list(AMBIGUOUS_TITLES),
        SALT,
        json.dumps(QUOTAS),
        list(QUOTAS),
    )
    return [
        SampleRow(
            org_id=r["id"],
            stratum=r["stratum"],
            name=" ".join(r["name"].split()),
            listed_in=r["listed_in"],
            role_titles=" ".join(r["role_titles"].split())[:TITLES_CHARS],
        )
        for r in rows
    ]


def write_worksheet(rows: list[SampleRow], out: Path) -> None:
    """Write the worksheet, owner-only, with blank label columns."""
    out.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(WORKSHEET_COLUMNS)
        for row in rows:
            w.writerow([*astuple(row), "", "", ""])
    os.chmod(out, 0o600)


def read_worksheet(path: Path) -> list[dict[str, str]]:
    """Read a worksheet CSV into dicts keyed by ``WORKSHEET_COLUMNS``."""
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def review_pick(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """The rows the operator reviews: every unsure one, then a seeded random 30 of the rest.

    Refuses an unlabelled row, or a kind outside ``KINDS``.
    """
    for r in rows:
        if not r["kind"]:
            raise ValueError(f"row {r['org_id']} is unlabelled")
        if r["kind"] not in KINDS:
            raise ValueError(f"row {r['org_id']}: {r['kind']!r} is not a kind")
    unsure = [r for r in rows if r["unsure"] == "Y"]
    sure = sorted(
        (r for r in rows if r["unsure"] != "Y"),
        key=lambda r: hashlib.md5((REVIEW_SALT + r["org_id"]).encode()).hexdigest(),
    )
    return unsure + sure[:REVIEW_RANDOM]


def write_review(rows: list[dict[str, str]], out: Path) -> None:
    """Write the review sheet, owner-only, with a blank ``reviewed_kind`` column."""
    out.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=REVIEW_COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow({**r, "reviewed_kind": ""})
    os.chmod(out, 0o600)


async def run_sample(dsn: str, out: Path) -> None:
    """Connect read-only, draw the sample, write the worksheet, log counts by stratum."""
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
        rows = await sample(conn)
    finally:
        await conn.close()
    write_worksheet(rows, out)
    counts: dict[str, int] = {}
    for r in rows:
        counts[r.stratum] = counts.get(r.stratum, 0) + 1
    logger.info(
        "sampled %d orgs into %s: %s",
        len(rows),
        out,
        ", ".join(f"{k}={counts.get(k, 0)}" for k in QUOTAS),
    )


def main() -> None:
    """CLI entry point."""
    configure_logging()
    parser = build_parser(__doc__)
    add_dsn_args(parser)
    sub = parser.add_subparsers(dest="command", required=True)
    s = sub.add_parser("sample", help="Draw the sample into a private worksheet (read-only)")
    s.add_argument("--out", type=Path, required=True, help="Worksheet CSV, outside any repo")
    r = sub.add_parser("review", help="Pick the rows to review into a private sheet (no DB)")
    r.add_argument("--worksheet", type=Path, required=True, help="The labelled worksheet")
    r.add_argument("--out", type=Path, required=True, help="Review CSV, outside any repo")
    args = parser.parse_args()
    out = args.out.expanduser()
    try:
        require_private(out)
    except PrivatePathError as e:
        parser.error(str(e))
    if args.command == "review":
        picked = review_pick(read_worksheet(args.worksheet.expanduser()))
        write_review(picked, out)
        unsure = sum(1 for p in picked if p["unsure"] == "Y")
        logger.info(
            "review: %d rows (%d unsure, %d random) into %s",
            len(picked),
            unsure,
            len(picked) - unsure,
            out,
        )
        return
    dsn = resolve_dsn(args, parser)
    asyncio.run(run_sample(dsn, out))


if __name__ == "__main__":
    main()
