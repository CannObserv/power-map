"""Pin `.github/workflows/gate.yml` to the local gate's own definition (#727).

CI re-runs `pre-commit run --all-files`, the definition every commit runs, so
nothing reaches `main` past a gate a developer could not have run, whether a
Dependabot PR, a `--no-verify` commit or a stale hook. Never a hand-copied job
list: one that drifts from `.pre-commit-config.yaml` checks something other
than what a developer's commit checked, and still shows green. These tests
fail when the two drift:

- a hook whose tool the runner doesn't provision (it would fail, or worse, skip);
- an optional dependency group CI doesn't sync (its tests `importorskip` into
  skips, and the run reads green having run less: #450, #497);
- a second command, a `SKIP=`, a path filter or a `push` trigger.

The DB tiers (integration, browser) stay local (#579). Nothing here may name a
secret or a DSN.
"""

import json
import re
import shlex
from pathlib import Path

import pytest
import yaml

from tests.optional_groups import OPTIONAL_GROUPS

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "gate.yml"
PRE_COMMIT = REPO_ROOT / ".pre-commit-config.yaml"

GATE = "uv run pre-commit run --all-files --show-diff-on-failure"
# The first word of a hook's entry → the step that puts it on the runner's PATH.
PROVIDERS = {
    "uv": "astral-sh/setup-uv",
    "npm": "actions/setup-node",
    "npx": "actions/setup-node",
    "terraform": "hashicorp/setup-terraform",
}


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8")) if WORKFLOW.is_file() else {}


def _triggers() -> dict:
    """The `on:` table. PyYAML reads the bare key `on` as boolean True."""
    doc = _workflow()
    return doc.get("on", doc.get(True)) or {}


def _jobs() -> dict[str, dict]:
    return _workflow().get("jobs") or {}


def _steps() -> list[dict]:
    return [step for job in _jobs().values() for step in job.get("steps", [])]


def _runs() -> list[str]:
    return [" ".join(step["run"].split()) for step in _steps() if "run" in step]


def _uses(action: str) -> list[dict]:
    return [s for s in _steps() if s.get("uses", "").split("@")[0] == action]


def _hooks() -> list[dict]:
    config = yaml.safe_load(PRE_COMMIT.read_text(encoding="utf-8"))
    return [hook for repo in config["repos"] for hook in repo["hooks"]]


def test_workflow_exists() -> None:
    assert WORKFLOW.is_file(), "no PR workflow: a Dependabot PR reaches main past no gate"


def test_ci_runs_the_local_definition_and_nothing_else() -> None:
    """One gate command, after the provisioning steps. A job list copied from the hooks
    drifts from them; a second command checks something no commit checked."""
    gate = [r for r in _runs() if "pre-commit" in r]
    assert gate == [GATE], f"expected exactly one `{GATE}`, found {gate}"
    others = [r for r in _runs() if r != GATE]
    allowed = re.compile(r"^(npm ci|uv sync( --group [\w-]+)+)$")
    assert all(allowed.match(r) for r in others), f"not provisioning: {others}"


def test_no_hook_is_skipped() -> None:
    """`SKIP=` is a second, hand-kept list of what CI leaves out."""
    text = WORKFLOW.read_text(encoding="utf-8")
    assert not re.search(r"\bSKIP\b", text), "SKIP= drops hooks the local gate runs"


@pytest.mark.parametrize("hook", _hooks(), ids=lambda h: h["id"])
def test_every_hook_tool_is_provisioned(hook: dict) -> None:
    """A tool the runner lacks fails the hook, which is loud; a new hook needing a
    binary no step installs must fail here first, with the fix named."""
    assert hook["language"] == "system", f"{hook['id']}: only `system` hooks are mapped"
    tool = shlex.split(hook["entry"])[0]
    if (REPO_ROOT / tool).is_file():
        return
    assert tool in PROVIDERS, f"{hook['id']}: nothing provisions `{tool}`; add a setup step"
    assert _uses(PROVIDERS[tool]), f"{hook['id']}: `{tool}` needs a {PROVIDERS[tool]} step"


def _synced_groups() -> set[str]:
    syncs = [r for r in _runs() if r.startswith("uv sync")]
    assert len(syncs) == 1, f"expected one `uv sync`, found {syncs}"
    return set(re.findall(r"--group ([\w-]+)", syncs[0]))


def test_sync_installs_every_optional_group() -> None:
    """A group CI doesn't install turns its tests into skips and the run stays green.
    Derived from the registry, so a new group fails here until CI syncs it."""
    assert _synced_groups() == set(OPTIONAL_GROUPS)


def test_sync_covers_every_group_a_hook_names() -> None:
    named = {g for h in _hooks() for g in re.findall(r"--group ([\w-]+)", h["entry"])}
    assert named <= _synced_groups(), f"hooks name {sorted(named - _synced_groups())}"


def test_the_lock_is_asserted_not_resolved() -> None:
    """Without UV_LOCKED, `uv run` re-resolves a stale uv.lock and passes on a tree no
    one locked; `npm ci` already refuses a package-lock.json out of step."""
    assert str(_workflow().get("env", {}).get("UV_LOCKED")) == "1"
    assert "npm ci" in _runs()


def test_submodules_are_checked_out() -> None:
    """The pytest-vendored and bats-vendored hooks read the vendored submodule (#755).

    Full history too: at checkout's default depth 1 the submodules are shallow, and
    8 pin-ancestry tests (test_socraticode_*_parity, test_doc_sensitive_paths, ...)
    skip as "ancestry unverifiable" where a local run checks them (#727)."""
    (checkout,) = _uses("actions/checkout")
    assert checkout.get("with", {}).get("submodules") == "recursive"
    assert checkout["with"].get("fetch-depth") == 0, "shallow submodules skip pin checks"


def test_node_meets_the_engines_floor() -> None:
    engines = json.loads((REPO_ROOT / "package.json").read_text(encoding="utf-8"))
    floor = re.fullmatch(r">=(\d+)\.\d+", engines["engines"]["node"])
    assert floor, f"unexpected engines.node {engines['engines']['node']!r}"
    (node,) = _uses("actions/setup-node")
    major = str(node["with"]["node-version"])
    assert major.isdigit() and int(major) >= int(floor.group(1)), (
        f"node-version {major!r}: a major, at or above engines' {floor.group(1)}"
    )


def test_terraform_meets_required_version() -> None:
    provider = (REPO_ROOT / "infra" / "terraform" / "provider.tf").read_text(encoding="utf-8")
    floor = re.search(r'required_version\s*=\s*">=\s*(\d+)\.(\d+)"', provider)
    assert floor, "required_version is no longer a `>= X.Y` floor"
    (tf,) = _uses("hashicorp/setup-terraform")
    version = tuple(int(p) for p in str(tf["with"]["terraform_version"]).split(".")[:2])
    assert version >= (int(floor.group(1)), int(floor.group(2)))
    assert tf["with"].get("terraform_wrapper") is False, "the wrapper rewrites fmt's exit"


def test_triggers_are_prs_dispatch_and_a_weekly_main_run() -> None:
    """No `push: main`: a PR already checked the tree. The schedule covers the
    pushes no PR carries (the skills-refresh hook's, the operator's)."""
    assert set(_triggers()) == {"pull_request", "workflow_dispatch", "schedule"}
    (cron,) = [c["cron"] for c in _triggers()["schedule"]]
    assert cron.split()[2:4] == ["*", "*"] and cron.split()[4] != "*", "weekly, not daily"


def test_every_pr_runs_the_gate() -> None:
    """No path filter. Replayed over the 151 PRs merged in the 90 days to 2026-11-06:
    3 were Markdown-only and all 3 changed a doc the unit suite reads
    (test_context_surface, test_doc_sensitive_paths, ...): 3 skips, 3 false skips."""
    assert not (_triggers().get("pull_request") or {}), "pull_request takes no filter"


def test_job_hygiene() -> None:
    assert _workflow().get("permissions") == {"contents": "read"}
    concurrency = _workflow().get("concurrency") or {}
    assert "pull_request" in str(concurrency.get("cancel-in-progress")), (
        "cancel superseded PR pushes only; a scheduled run on main finishes"
    )
    for job_id, job in _jobs().items():
        assert job.get("timeout-minutes"), f"{job_id}: no timeout-minutes"
        # GitHub's own update jobs report as `Dependabot`, and one failed on
        # every refresh for a week (#92); a gate failure must not read as that.
        assert "gate" in job.get("name", ""), f"{job_id}: name it as the gate"


def test_ci_is_db_free() -> None:
    """The integration and browser tiers stay local: the DB is DO-only (#579)."""
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "secrets." not in text
    assert "DATABASE_URL" not in text
