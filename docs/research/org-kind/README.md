# Org-kind gold set (#877)

`gold.csv` labels 293 organizations with one org kind each. It is the bar a
classification source must clear before its suggestions are trusted: design
D6 in [`2026-11-24-org-kind-suggestions-design.md`](../../plans/2026-11-24-org-kind-suggestions-design.md),
and step 8 of [`2026-11-26-877-phase-2-jev-suggestions.md`](../../plans/2026-11-26-877-phase-2-jev-suggestions.md).

Only `org_id,kind` is committed. Names, registries and role titles stay in
private worksheets (`data/gold-877/`, git-ignored), because the repo is public.

## Method

1. **Sample** (`scripts/org_kind_gold.py sample`, read-only, 2026-11-26). The
   active orgs are split into strata: recent, party, PDC committee, WSLCB, UBI,
   PDC, legislature, ambiguous title (#303 D2), no identifier. Each org goes to
   the first stratum it matches, and each stratum keeps its first N orgs by
   `md5('gold-877:' || id)` (`QUOTAS` in the script).
2. **Label.** Each kind was drafted from the seeded `org_kinds` definitions
   before any Jev call on these orgs, with the uncertain rows flagged (35).
3. **Review** (`review`). The operator checked every flagged row, plus 30
   others picked at random by `md5('review-877:' || id)`. The draft agreed on
   29 of the 30 random rows (30 after the city-council ruling below) and on 27
   of the 35 flagged ones.
4. **Class rulings** (operator, 2026-11-27), written into the seeded
   definitions so that Jev is asked the same thing the gold set scores:
   - an association whose members are governments or their officials is an `association`;
   - a legislative task force or work group is a `legislature`;
   - a federation of unions is a `labor_union`. A council of police guilds
     was ruled an `association`, row by row;
   - city and county councils stay `government`, as the definition says;
   - an organization that exists for another purpose and also registers as
     a political committee takes the kind of that purpose. With this ruling
     the rule `org_wa_pdc_committee` → `campaign_committee` was retired
     (rules-2), after the gold set found a trade association that holds a
     PDC committee filer ID.
5. **Finalize** (`finalize`). Each worksheet label is kept unless the review
   names another kind.

## Scoring

`eval-set` builds the vendored `typesafe-eval.py` set from this file and live
state, using production's question builder (`src/core/org_kind_question.py`).
It writes the set outside every checkout. `score` reads the harness cache and
the rules source's live suggestions, and prints aggregates only. A kind is
trusted when ≥ 90 % of ≥ 20 answers at confidence ≥ 0.7 are right; kinds too
small to reach 20 answers fail closed and go to review.

## Known bias

The feasibility spike's 61 orgs were labelled after Jev's answers had been
seen. The salted ordering keeps most of them out of this sample, but nothing
guarantees it: the spike's ids weren't kept.

Gold orgs archived or merged after the sample are left out of a score, and the
report counts them.
