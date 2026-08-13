# power-map — Testing

How to run each tier: the Python suite and its integration marker, the Vitest JS suite
and its conventions, the marker-gated browser a11y sweep, and the bats shell suite. Fixture and client
recipes live in `docs/SCHEMA.md`; the a11y rules themselves in `docs/ACCESSIBILITY.md`.

---

## Testing


```bash
# Run all tests (excludes integration)
uv run pytest

# Run with coverage
uv run pytest --cov

# Run a specific file
uv run pytest tests/path/to/test_file.py --no-cov

# Run integration tests (hits live external services)
uv run pytest -m integration
```

---

## Browser Testing (axe-core a11y sweep, #300)


The rules this tier enforces live in [`docs/ACCESSIBILITY.md`](ACCESSIBILITY.md).

Real-browser tier: headless Chromium + axe-core full ruleset (colour contrast,
ARIA roles, landmarks, focus order) over every full-page admin GET route —
coverage the render-based lxml sweep (#246) can't reach. Marker-gated
(`-m browser`), **excluded by default and never run in pre-commit**. Reuses the
route enumeration + seed dataset in `tests/api/admin/admin_routes.py`, so it and
the lxml tier never drift.

```bash
# One-time setup (installs Playwright + a ~120MB Chromium; not in the dev group)
uv sync --group browser
uv run --group browser playwright install chromium

# Run the whole tier (needs TEST_DATABASE_URL; env flags per § Environment)
uv run --group browser --env-file /etc/power-map/.env --env-file .env \
    pytest tests/api/admin/ -m browser
```

Notes:
- **Automated weekly** by `power-map-a11y.timer` (#369, below) — it runs this tier
  plus the lxml render tier and surfaces failures. Run it manually too as a
  pre-release gate (before tagging a version / restarting prod).
- **Isolation:** the tier launches uvicorn on an ephemeral port against the
  dedicated test DB, which it truncates-and-seeds at session start and resets on
  teardown (the managed-PG test role has no `CREATEDB`, so a disposable
  `CREATE DATABASE` per session isn't possible). Run it **alone** — never
  alongside the integration suite against the same DB.
- axe-core is SHA-pinned under `tests/vendor/` (see that README); the run
  verifies the hash at import.
- **Three files, one marker** — the tier is marker-gated over the whole admin test
  dir, so a new browser file is swept automatically (the weekly timer runs the same
  invocation):
  - `test_a11y_browser.py` (#300) — axe over every full-page admin GET route.
  - `test_a11y_browser_interactions.py` (#367) — axe over post-interaction DOM the
    server never renders: inline edit rows, merge mode, portal and stacked modals,
    the archived-entity delete confirm.
  - `test_browser_smoke.py` (#368) — not a11y: real-browser flow smoke (typeahead
    select, merge confirm) behind the fast happy-dom Vitest tier. It **mutates**
    data, so it seeds its own disposable rows and never touches the shared session
    seed — keep that rule when adding flows.

### Weekly a11y sweep timer (production, #369)

`power-map-a11y.timer` runs `scripts/run-a11y-sweep.sh` weekly (Sundays 04:00 UTC):
both a11y tiers (lxml `test_a11y_render.py -m integration` + browser
`test_a11y_browser.py -m browser`) against the test DB. A **Chromium guard**
launches a real browser first and exits 2 if it's absent, so a missing install
fails loudly instead of the browser tier importorskipping to a vacuous pass.

Surfacing (two layers): the unit shows in `systemctl --failed` on any failure
(the ambient signal the SessionStart hook `.claude/hooks/a11y-status-reminder.sh`
reads and echoes when you open Claude on the VM); and on failure the runner
opens-or-updates a single `a11y-regression` GitHub issue (closing it on the next
green run — GitHub's notification email covers the "email me" need). The issue
carries a **one-line summary + a pointer to the journal only** — never raw
output, since this is a public repo. Full failing detail (axe violations,
tracebacks) lives in `journalctl -u power-map-a11y` on the VM.

To exercise the failure → open-issue → recover → close cycle without breaking a
tier, run with the self-test hatch: `A11Y_SWEEP_FORCE_FAIL=1 bash
scripts/run-a11y-sweep.sh` (opens a synthetic-failure issue), then a normal green
run closes it. `A11Y_SWEEP_NO_GH=1` logs the GitHub actions instead of doing them.

One-time VM setup (the guard fails until this is done):

```bash
uv sync --group browser && uv run --group browser playwright install chromium
```

Install / update the timer:

```bash
sudo cp infra/power-map-a11y.service infra/power-map-a11y.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now power-map-a11y.timer

# Inspect
systemctl list-timers power-map-a11y.timer     # next/last run
sudo systemctl start power-map-a11y.service    # run once, now (~60s)
sudo journalctl -u power-map-a11y -f           # live run + surfacing log
```

---

## Shell testing (bats, #373)


The two #369 bash entrypoints (`scripts/run-a11y-sweep.sh`,
`.claude/hooks/a11y-status-reminder.sh`) are covered by bats-core suites in
`tests/sh/`. Fully hermetic: `uv`, `gh`, and `systemctl` are PATH shims from
`tests/sh/stubs/`, driven by `STUB_*` env knobs — no network, GitHub, systemd,
or DB. Runs in pre-commit (fast), alongside a `shellcheck` hook over
`scripts/*.sh` + `.claude/hooks/*.sh` (vendored-skill symlink hooks excluded).

```bash
npm run test:sh          # bats (bats-core via npm devDependency)
uv run shellcheck scripts/*.sh   # shellcheck-py, dev dependency group
```

---

## JS Testing


```bash
# Run JS tests (one-shot)
npm run test:js

# Run JS tests in watch mode
npm run test:js:watch
```

Note: Node ≥22 required. `npm install` first if `node_modules/` is absent.
Uses vitest v2 + happy-dom. happy-dom was chosen over jsdom historically
due to a CJS/ESM incompatibility in jsdom v29 on Node 18; kept on happy-dom
for speed.

---

## Vitest test conventions


JS test files in `tests/js/` mount admin scripts via `eval(scriptCode)` against a happy-dom DOM. The scripts are IIFEs that auto-attach listeners on load — there is no exported teardown hook. The conventions below normalize how stubs are built and how listener leaks are prevented across tests.

### `vi.fn()` vs `vi.spyOn()`

- **`vi.fn()`** — a fresh function with no original behavior. Use for stubs that *replace* a function (no call-through). Example: stubbing `window.initTypeaheadCombobox` so the script-under-test reaches it without us caring what the real combobox factory does.
- **`vi.spyOn(obj, 'method')`** — wraps the existing method, calls through, and records every invocation in `.mock.calls`. Use when you need the real behavior plus call inspection.

Both surface `.mock.calls` (array of `[arg0, arg1, ...]` per call) and `.mock.results`. Prefer Vitest helpers over hand-rolled `const calls = []; fn = (x) => calls.push(x)` accumulators — the helpers also restore cleanly via `mockRestore()` / `vi.restoreAllMocks()`.

### Listener cleanup is mandatory for `eval()`-mounted scripts

The `eval(scriptCode)` mount pattern re-runs the IIFE on every test. Every `document.addEventListener(...)` call inside the IIFE attaches a *new* handler — and there is no teardown hook to remove it. Without explicit cleanup the handlers accumulate across tests: a single `document.dispatchEvent(...)` in test N triggers N listener firings, which silently inflates call counts (or papers over real bugs by way of duplicate idempotent handlers).

Spy on `document.addEventListener` in `beforeEach`, then in `afterEach` walk the spy's `.mock.calls` and `removeEventListener` each `(type, fn)` pair before restoring the spy.

### Cleanup template

Add to every test file whose script-under-test attaches `document` listeners:

```js
let addSpy;

beforeEach(() => {
  addSpy = vi.spyOn(document, 'addEventListener');
});

afterEach(() => {
  for (const [type, fn] of addSpy.mock.calls) {
    document.removeEventListener(type, fn);
  }
  addSpy.mockRestore();
  document.body.innerHTML = '';
});
```

Reference implementation: `tests/js/person-name-row-typeahead.test.js:63-87`.

Files that do NOT need this block:

- Pure expression-extractor tests that never `eval` script source or attach DOM listeners (e.g. `tests/js/name-typeahead-hx-vals.test.js`).
- Factory-style scripts where the test cleans up via the script's own teardown path (e.g. dispatching Escape to invoke the factory's `closeDropdown` removes the document-level listeners it registered) — but the spy-based block is still preferred for symmetry and to catch listeners the factory itself doesn't track.
