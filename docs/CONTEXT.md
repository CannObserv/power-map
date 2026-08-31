# power-map — The Agent-Context Surface

The rules `AGENTS.md` itself obeys: what the surface is, what it costs, how its
index is shaped, and how a number is allowed to appear in it. The per-subject
content rules live in the docs this one does not duplicate; this file is about
the container.

Enforced by `tests/test_context_surface.py`. Measured by the weekly cadence
(#471) and, at the moment an edit lands, by `.claude/hooks/context-budget-guard.sh`.

---

## What the surface is

- **`AGENTS.md`** — loaded on **every** invocation. Budget **6,000 tokens**
  (`.skills/context-budget`). Every token here is paid on every task, whether or
  not the task touches the subject.
- **`docs/*.md`** — loaded on demand. Budget **10,000 tokens** each
  (`.skills/context-doc-budget`). Cost is paid by whoever loads it.
- **`docs/plans/`, `docs/research/`, `docs/archive/`** — written once, never
  loaded as context. No budget.

The gap between the two budgets is the whole economics of the surface: moving a
paragraph from `AGENTS.md` into a doc does not delete it, it stops charging
every task for it. That is why the rules below all resolve to *relocate*, never
to *discard*.

---

## Rule 1 — an index line is a pointer (#484)

`AGENTS.md`'s `## Detail Docs` section is an index. Each line says **what a task
would need the doc for**; the doc's own opening paragraph says what is in it.

- **A new doc may add a line.** That is the only legitimate way the index grows.
- **An existing line may not accrete clauses.** If a doc gained a subject, the
  subject is described *in the doc*, not in the pointer at it.
- **Ceiling: 200 characters per line**, whole line including the link. A pointer
  that will not fit is summarising rather than routing.
- The same ceiling (100 characters) applies to the `Covers` cells of
  `docs/API_ENTITIES.md`'s routing table, the one doc-side index of the same
  shape. `docs/SCHEMA.md`'s "DB key rules at a glance" is deliberately *not*
  covered: those lines are rule statements ending `Full rules → …`, a digest
  rather than an index, and a length ceiling would push the rules themselves out
  of reach.

### Why this rule and not #428's

#428 wrote it as "the index cannot grow", which is unenforceable — adding a doc
must be allowed — so nothing enforced it, and the file went over budget without
any single step looking like the culprit. `AGENTS.md` was last measured under
budget at `9ecae78` (5,976 / 6,000); the next reading was 6,045. `git log`
attributes the +69 to two commits that widened *existing* index blurbs —
`1fc4841` (#459, `OBSERVATIONS.md`) and `ebc76a5` (#467, `MERGE.md`) — not to new
policy, though #473 initially recorded it as new policy from #469, which never
touched the file.

A delta check ("this line grew since `HEAD`") was the more precise candidate and
was rejected: it goes quiet the moment the growth is committed, so it guards a
diff rather than a property, and it tells the next reader nothing.

---

## Rule 2 — a count carries its method, or drops its precision (#483)

`AGENTS.md:60` claimed "180 `hx-get` reveals". Three parties measured that claim
this week and got **180** (the original), **189** (the #473 curation agent) and
**182** (`grep -ro 'hx-get' src/templates/ | wc -l`). Nobody was wrong: the
sentence never said what was being counted — occurrences or lines, templates
only or Python too — so three reasonable methods gave three answers and none
could be checked against it. A number that cannot be reproduced cannot be
maintained, and it is worse than no number, because it reads as precise.

A count earns its place in the context surface in exactly one of three forms:

1. **Attach the command.** ``the `hx-get` reveals (`grep -ro 'hx-get'
   src/templates/ | wc -l`)``. A stale number becomes a falsifiable claim.
2. **Drop the precision.** "the admin's `hx-get` reveals mean the forms don't
   exist without JS anyway" is the same argument and cannot rot. **This is the
   default**, and it is what every count in `AGENTS.md` became: the number was
   rhetorical in all six places.
3. **Make it a gate.** Where a count is genuinely load-bearing, a test asserting
   it is the only form that stays true. If it is not worth a test, it is not
   worth stating precisely.

The rot is not hypothetical beyond the one case: `AGENTS.md` said "the three
a11y test tiers" while `docs/ACCESSIBILITY.md` — which owns the subject — has
described **four** since the axe-after-interaction tier landed. #444 had already
had to correct `docs/AUDITS.md`'s preamble from six timers to four.

### What the gate actually catches

`test_the_policy_file_carries_no_bare_counts` scans `AGENTS.md` for cardinal
*words* ("two" … "twenty", "dozen") and for a digit qualifying a backticked term
("180 `hx-get`"). A line carrying a `wc -l` command in backticks is exempt —
that is form (1).

Bare digits are deliberately not scanned: in this file they are overwhelmingly
status codes, ports, standards and versions, and a gate that cries wolf on `403`
and `ISO 8601` is a gate someone deletes. So "182 hx-get reveals", written
without backticks, would pass. The gate approximates the rule; the rule is the
one stated above.

The reference docs are **not** gated for counts. They are loaded on demand
rather than on every invocation, and the same scan finds roughly 190 hits across
that tree — a sweep of them is its own piece of work. When you touch a doc and
find a count, apply the three forms above by hand.

---

## Rule 3 — no doc is loadable-but-unfindable

Every live `docs/*.md` must be reachable by following links from `AGENTS.md`,
directly or through a routing doc (`SCHEMA.md`, `ADMIN.md`, `API_ENTITIES.md`
carry pointers to their own sub-docs). That reachability, not membership of the
top-level index, is what keeps the index one line per *subject* while the tree
holds one file per *topic* — and it is why splitting a doc (#407, #428, #444)
adds no line to `AGENTS.md` at all.
