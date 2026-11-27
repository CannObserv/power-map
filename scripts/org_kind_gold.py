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
or to a path git ignores (``data/gold-877/``), or the run refuses before
connecting. Only ``(org_id, kind)`` is ever
committed, at ``docs/research/org-kind/gold.csv``.

``review`` picks the rows the operator checks: every row marked unsure, plus
``REVIEW_RANDOM`` of the rest by ``md5('review-877:' || id)``. They go into a
private review sheet with a blank ``reviewed_kind`` column.

``finalize`` writes the committed ``org_id,kind`` set: each worksheet label,
or the review's ``reviewed_kind`` where filled. ``eval-set`` builds the
vendored ``typesafe-eval.py`` set from it through production's question
builder, outside every work tree; ``--kinds-dsn-env`` reads the definitions
from another DB (a branch's reworded seed in the test DB). ``score`` reads the
harness cache and the rules' live suggestions and prints the #877 report,
aggregates only.

Usage:
    uv run python -m scripts.org_kind_gold sample --out data/gold-877/worksheet.csv
    uv run python -m scripts.org_kind_gold review --worksheet data/gold-877/worksheet.csv \
        --out data/gold-877/review.csv
    uv run python -m scripts.org_kind_gold finalize --worksheet data/gold-877/worksheet.csv \
        --review data/gold-877/review.csv
    uv run python -m scripts.org_kind_gold eval-set --out ~/gold-877-eval/set.jsonl
    python3 skills-vendor/gregoryfoster-skills/scripts/typesafe-eval.py ~/gold-877-eval/set.jsonl \
        --cache ~/gold-877-eval/cache.jsonl --model jev-1.14.0   # TYPESAFE_OPT_IN=on, this run only
    uv run python -m scripts.org_kind_gold score --set ~/gold-877-eval/set.jsonl \
        --cache ~/gold-877-eval/cache.jsonl
"""

import asyncio
import csv
import hashlib
import json
import os
import subprocess
from collections import Counter
from dataclasses import astuple, dataclass, field
from pathlib import Path

import asyncpg

from scripts._dsn import add_dsn_args, build_parser, resolve_dsn
from src.core import jev_client as jev
from src.core import org_kind_question as q
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

GOLD_PATH = Path(__file__).resolve().parents[1] / "docs" / "research" / "org-kind" / "gold.csv"
GOLD_COLUMNS = ("org_id", "kind")
EVAL_SOURCE = "gold-877"
# The trust bar (design D6): a kind Jev answers at the threshold is trusted
# when at least TRUST_PRECISION of at least TRUST_MIN_ANSWERS answers are right.
TRUST_PRECISION = 0.9
TRUST_MIN_ANSWERS = 20


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


def _git_ignored(path: Path) -> bool:
    """True when git ignores *path* (resolved, GIT_ variables dropped)."""
    real = Path(os.path.realpath(path))
    probe = real
    while not probe.is_dir():
        probe = probe.parent
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    try:
        out = subprocess.run(
            ["git", "check-ignore", "-q", str(real)],
            cwd=probe,
            env=env,
            capture_output=True,
            check=False,
        )
    except OSError:
        return False
    return out.returncode == 0


def require_private(path: Path) -> None:
    """Refuse *path* unless it is outside every git work tree, or git ignores it.

    The repo's own ``data/gold-877/`` is ignored (``.gitignore``), so the
    worksheets can live in the clone without ever being committable.
    """
    inside = _in_work_tree(path)
    if inside is False:
        return
    if inside is None:
        reason = "can't be checked by git"
    elif _git_ignored(path):
        return
    else:
        reason = "is inside a git work tree and not ignored"
    raise PrivatePathError(
        f"{path} {reason}: the gold-set worksheet holds org names and must stay private"
    )


def require_outside(path: Path) -> None:
    """Refuse *path* unless it is outside every git work tree (ignored is not enough).

    The eval set, the harness cache and the report go where the vendored
    ``typesafe-eval.py`` accepts them, which is never inside a work tree.
    """
    inside = _in_work_tree(path)
    if inside is not False:
        reason = "can't be checked by git" if inside is None else "is inside a git work tree"
        raise PrivatePathError(
            f"{path} {reason}: the eval set holds org names and must stay outside every checkout"
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


def finalize(
    worksheet: list[dict[str, str]], review: list[dict[str, str]]
) -> list[tuple[str, str]]:
    """The gold labels: each worksheet kind, or the review's ``reviewed_kind`` where filled.

    Sorted by org id. Refuses a kind outside ``KINDS`` and a review row the
    worksheet lacks.
    """
    review_pick(worksheet)  # every row labelled, every kind in the vocabulary
    labels = {r["org_id"]: r["kind"] for r in worksheet}
    for r in review:
        if r["org_id"] not in labels:
            raise ValueError(f"review row {r['org_id']} is not in the worksheet")
        reviewed = r.get("reviewed_kind", "").strip()
        if not reviewed:
            continue
        if reviewed not in KINDS:
            raise ValueError(f"review row {r['org_id']}: {reviewed!r} is not a kind")
        labels[r["org_id"]] = reviewed
    return sorted(labels.items())


def write_gold(rows: list[tuple[str, str]], out: Path) -> None:
    """Write ``org_id,kind`` rows: the only form of the gold set that is committed."""
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(GOLD_COLUMNS)
        w.writerows(rows)


def read_gold(path: Path) -> list[tuple[str, str]]:
    """Read ``org_id,kind`` rows."""
    with path.open(newline="") as f:
        return [(r["org_id"], r["kind"]) for r in csv.DictReader(f)]


_ACTIVE_SQL = "SELECT id FROM organizations WHERE id = ANY($1::text[]) AND archived_at IS NULL"


async def eval_cases(
    db: asyncpg.Connection, gold: list[tuple[str, str]], kinds: list[tuple[str, str]]
) -> tuple[list[dict], list[str]]:
    """Harness cases for the gold orgs still active, through the production builder.

    Returns ``(cases, missing)``: *missing* lists gold orgs since archived or
    merged away, in gold order.
    """
    ids = [oid for oid, _ in gold]
    active = {r["id"] for r in await db.fetch(_ACTIVE_SQL, ids)}
    states = await q.load_states(db, [oid for oid in ids if oid in active])
    questions = q.build_questions(q.criteria(kinds))
    cases, missing = [], []
    for oid, kind in gold:
        if oid not in active:
            missing.append(oid)
            continue
        cases.append(
            {
                "id": oid,
                "state": q.build_state(states[oid]),
                "questions": questions,
                "expected": {q.QUESTION_NAME: kind},
                "source": EVAL_SOURCE,
            }
        )
    return cases, missing


@dataclass(frozen=True)
class KindScore:
    """One kind's row: gold orgs of the kind, answers naming it, and how many were right."""

    gold: int = 0
    answered: int = 0
    correct: int = 0

    @property
    def precision(self) -> float | None:
        """Right answers over answers, or None with no answers."""
        return self.correct / self.answered if self.answered else None


@dataclass
class JevScore:
    """Aggregates of one scored run: never an org id or a name."""

    cases: int = 0
    answered: int = 0
    correct_all: int = 0
    kinds: dict[str, KindScore] = field(default_factory=dict)
    confusion: Counter = field(default_factory=Counter)
    models: set[str] = field(default_factory=set)
    input_tokens: int = 0


def _tally(gold: list[tuple[str, str]], predicted: dict[str, str]) -> dict[str, KindScore]:
    counts = {k: [0, 0, 0] for k in sorted(KINDS)}
    for oid, kind in gold:
        counts[kind][0] += 1
        guess = predicted.get(oid)
        if guess is not None:
            counts[guess][1] += 1
            counts[guess][2] += guess == kind
    return {k: KindScore(*v) for k, v in counts.items()}


def score_jev(
    gold: list[tuple[str, str]],
    replies: dict[str, object],
    crit: dict[str, str],
    model: str,
    threshold: float,
) -> JevScore:
    """Score each case's reply: an answer is a choice at or above *threshold*.

    Each reply is validated as production validates it
    (:func:`src.core.jev_client.parse_response`). Refuses a case with no reply.
    """
    score = JevScore(cases=len(gold))
    predicted: dict[str, str] = {}
    for oid, kind in gold:
        if oid not in replies:
            raise ValueError(f"case {oid} has no reply: run the harness to completion first")
        answer = jev.parse_response(replies[oid], crit, model)
        score.models.add(answer.model)
        score.input_tokens += answer.input_tokens
        score.confusion[(kind, answer.kind)] += 1
        score.correct_all += answer.kind == kind
        if answer.confidence >= threshold:
            predicted[oid] = answer.kind
    score.answered = len(predicted)
    score.kinds = _tally(gold, predicted)
    return score


def score_rules(gold: list[tuple[str, str]], rules: dict[str, str]) -> dict[str, KindScore]:
    """Score the rules source's live suggestions against the gold labels."""
    return _tally(gold, rules)


def trust_set(kinds: dict[str, KindScore]) -> list[str]:
    """Kinds whose answers clear ``TRUST_PRECISION`` on at least ``TRUST_MIN_ANSWERS``."""
    return [
        k
        for k, s in kinds.items()
        if s.answered >= TRUST_MIN_ANSWERS and s.correct >= TRUST_PRECISION * s.answered
    ]


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value:.0%}"


def render_report(
    jev_score: JevScore, rules: dict[str, KindScore], missing: int, threshold: float
) -> str:
    """The #877 report as markdown: aggregates only, no org id or name."""
    s = jev_score
    top = s.correct_all / s.cases if s.cases else None
    coverage = s.answered / s.cases if s.cases else None
    lines = [
        f"**Jev** ({', '.join(sorted(s.models))}): {s.cases} gold orgs scored"
        f" ({missing} since archived or merged, left out).",
        f"Top choice right on {s.correct_all}/{s.cases} ({_pct(top)});"
        f" at confidence ≥ {threshold}, {s.answered} answered"
        f" ({_pct(coverage)} coverage),"
        f" {sum(k.correct for k in s.kinds.values())} right. {s.input_tokens:,} input tokens.",
        "",
        f"| kind | gold | Jev answers ≥ {threshold} | right | precision"
        " | rules suggest | right | precision |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for kind, k in s.kinds.items():
        r = rules.get(kind, KindScore())
        lines.append(
            f"| {kind} | {k.gold} | {k.answered} | {k.correct} | {_pct(k.precision)}"
            f" | {r.answered} | {r.correct} | {_pct(r.precision)} |"
        )
    trusted = trust_set(s.kinds)
    lines += [
        "",
        f"**Proposed trust set** (≥ {TRUST_PRECISION:.0%} on ≥ {TRUST_MIN_ANSWERS} answers):"
        f" {', '.join(trusted) if trusted else 'none'}.",
        "",
        "Confusion (rows gold, columns Jev's top choice; zero rows and columns left out):",
        "",
    ]
    rows = sorted({g_ for g_, _ in s.confusion})
    cols = sorted({p for _, p in s.confusion})
    lines.append("| gold \\ Jev | " + " | ".join(cols) + " |")
    lines.append("|---" * (len(cols) + 1) + "|")
    for g_ in rows:
        cells = [str(s.confusion.get((g_, p), "")) or "·" for p in cols]
        lines.append(f"| {g_} | " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


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


_RULES_SQL = """
SELECT entity_id, value FROM classification_suggestions
WHERE entity_type = 'organization' AND field = 'org_kind' AND source = 'rules'
  AND ignored_at IS NULL AND entity_id = ANY($1::text[])
"""


async def _connect_read_only(dsn: str) -> asyncpg.Connection:
    conn = await asyncpg.connect(dsn)
    await conn.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
    return conn


async def run_eval_set(dsn: str, kinds_dsn: str | None, out: Path) -> None:
    """Build the harness set from the committed gold set and live state (read-only).

    *kinds_dsn*, when given, supplies the kind definitions: a branch's reworded
    seed applied to the test DB, scored before it reaches production.
    """
    gold = read_gold(GOLD_PATH)
    conn = await _connect_read_only(dsn)
    try:
        if kinds_dsn:
            kconn = await _connect_read_only(kinds_dsn)
            try:
                kinds = await q.load_kinds(kconn)
            finally:
                await kconn.close()
        else:
            kinds = await q.load_kinds(conn)
        cases, missing = await eval_cases(conn, gold, kinds)
    finally:
        await conn.close()
    out.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        for case in cases:
            f.write(json.dumps(case, ensure_ascii=False) + "\n")
    logger.info("eval-set: %d cases into %s (%d gold orgs gone)", len(cases), out, len(missing))


def read_replies(cache: Path) -> dict[str, object]:
    """The harness cache's last reply per case id."""
    replies: dict[str, object] = {}
    with cache.open() as f:
        for line in f:
            if line.strip():
                row = json.loads(line)
                replies[row["id"]] = row["reply"]
    return replies


async def run_score(dsn: str, eval_set: Path, cache: Path, model: str, threshold: float) -> str:
    """Score the harness's replies and the rules source; return the markdown report."""
    with eval_set.open() as f:
        cases = [json.loads(line) for line in f if line.strip()]
    gold = [(c["id"], c["expected"][q.QUESTION_NAME]) for c in cases]
    crit = cases[0]["questions"][q.QUESTION_NAME]["criteria"] if cases else {}
    jev_score = score_jev(gold, read_replies(cache), crit, model, threshold)
    conn = await _connect_read_only(dsn)
    try:
        rows = await conn.fetch(_RULES_SQL, [oid for oid, _ in gold])
    finally:
        await conn.close()
    rules = score_rules(gold, {r["entity_id"]: r["value"] for r in rows})
    missing = len(read_gold(GOLD_PATH)) - len(gold)
    return render_report(jev_score, rules, missing, threshold)


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
    f = sub.add_parser("finalize", help="Write the committed org_id,kind gold set (no DB)")
    f.add_argument("--worksheet", type=Path, required=True, help="The labelled worksheet")
    f.add_argument("--review", type=Path, required=True, help="The operator's review sheet")
    e = sub.add_parser("eval-set", help="Build the harness JSONL from the gold set (read-only)")
    e.add_argument("--out", type=Path, required=True, help="Set JSONL, outside every work tree")
    e.add_argument(
        "--kinds-dsn-env",
        metavar="NAME",
        help="Read the kind definitions from the DB this env var names (e.g. TEST_DATABASE_URL)",
    )
    c = sub.add_parser("score", help="Score the harness cache and the rules (read-only)")
    c.add_argument("--set", type=Path, required=True, help="The eval-set JSONL")
    c.add_argument("--cache", type=Path, required=True, help="typesafe-eval.py's --cache file")
    c.add_argument("--model", default=jev.DEFAULT_MODEL, help="The model the harness asked")
    c.add_argument("--threshold", type=float, default=jev.DEFAULT_THRESHOLD)
    args = parser.parse_args()

    if args.command == "finalize":
        gold = finalize(
            read_worksheet(args.worksheet.expanduser()), read_worksheet(args.review.expanduser())
        )
        write_gold(gold, GOLD_PATH)
        logger.info("finalize: %d labels into %s", len(gold), GOLD_PATH)
        return
    if args.command == "score":
        dsn = resolve_dsn(args, parser)
        print(asyncio.run(run_score(dsn, args.set, args.cache, args.model, args.threshold)), end="")
        return
    out = args.out.expanduser()
    try:
        (require_outside if args.command == "eval-set" else require_private)(out)
    except PrivatePathError as err:
        parser.error(str(err))
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
    if args.command == "eval-set":
        kinds_dsn = None
        if args.kinds_dsn_env:
            kinds_dsn = os.environ.get(args.kinds_dsn_env)
            if not kinds_dsn:
                parser.error(f"{args.kinds_dsn_env} is not set")
        asyncio.run(run_eval_set(dsn, kinds_dsn, out))
        return
    asyncio.run(run_sample(dsn, out))


if __name__ == "__main__":
    main()
