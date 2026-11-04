#!/usr/bin/env bats
# Guards scripts/precommit-vendor-gate.sh (#751): the pre-commit wrapper that
# runs a vendor-sensitive subset, not the whole tier, on a commit staging only
# skills-vendor/ submodule pointers and .skills/doctor.sh — the commit the
# SessionStart hook makes, which must finish inside its 120 s timeout.
#
# Real git in a throwaway repo; the gated commands are `echo`s, so nothing of
# the real suite runs.

load helpers

setup() {
    GATE="$(repo_root)/scripts/precommit-vendor-gate.sh"

    # Hermetic against git's own environment: under a pre-commit hook, `git
    # commit` exports GIT_DIR and GIT_INDEX_FILE for the REAL repository, and
    # those beat `-C` — a fixture call would then stage into this repo.
    unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_PREFIX GIT_COMMON_DIR
    unset GIT_OBJECT_DIRECTORY GIT_ALTERNATE_OBJECT_DIRECTORIES
    unset GIT_AUTHOR_DATE GIT_COMMITTER_DATE GIT_EDITOR GIT_REFLOG_ACTION
    export GIT_CONFIG_GLOBAL="$BATS_TEST_TMPDIR/gitconfig-global"
    export GIT_CONFIG_SYSTEM=/dev/null
    : > "$GIT_CONFIG_GLOBAL"

    # A registered submodule gitlink (no clone needed: a gitlink's object is
    # never checked), a doctor, and one ordinary file.
    REPO="$BATS_TEST_TMPDIR/fixture-repo"
    mkdir -p "$REPO/.skills" "$REPO/src"
    git -C "$REPO" init --quiet --template=
    git -C "$REPO" config user.email t@example.invalid
    git -C "$REPO" config user.name t
    git -C "$REPO" config commit.gpgsign false
    git -C "$REPO" config core.hooksPath /dev/null
    printf '[submodule "skills-vendor/fake"]\n\tpath = skills-vendor/fake\n\turl = https://example.invalid/fake.git\n' \
        > "$REPO/.gitmodules"
    echo doctor > "$REPO/.skills/doctor.sh"
    echo a > "$REPO/src/a.py"
    git -C "$REPO" add .gitmodules .skills/doctor.sh src/a.py
    git -C "$REPO" update-index --add --cacheinfo \
        "160000,1111111111111111111111111111111111111111,skills-vendor/fake"
    git -C "$REPO" commit --quiet --no-verify -m init

    [ -d "$REPO/.git" ] || {
        echo "fixture repo was not created — GIT_* scrub failed" >&2
        return 1
    }
}

bump_pointer() {
    git -C "$REPO" update-index --cacheinfo \
        "160000,2222222222222222222222222222222222222222,skills-vendor/fake"
}

bump_doctor() {
    echo doctor2 >> "$REPO/.skills/doctor.sh"
    git -C "$REPO" add .skills/doctor.sh
}

gate() {
    run bash -c "cd '$REPO' && bash '$GATE' $*"
}

@test "a pointer-only commit runs the vendored subset" {
    bump_pointer
    gate pytest echo VENDOR ::: echo FULL
    [ "$status" -eq 0 ]
    [[ "$output" == *"VENDOR"* ]]
    [[ "$output" != *"FULL"* ]]
    [[ "$output" == *"pytest: vendor-only commit (#751)"* ]]
}

@test "a pointer-only commit is seen through diff.ignoreSubmodules" {
    # Hidden, the pointer would read as nothing staged: the full tier, and the
    # hook's commit killed at 120 s again.
    git -C "$REPO" config diff.ignoreSubmodules all
    bump_pointer
    gate pytest echo VENDOR ::: echo FULL
    [[ "$output" == *"VENDOR"* ]]
    [[ "$output" != *"FULL"* ]]
}

@test "a doctor-only commit runs the vendored subset" {
    bump_doctor
    gate pytest echo VENDOR ::: echo FULL
    [ "$status" -eq 0 ]
    [[ "$output" == *"VENDOR"* ]]
    [[ "$output" != *"FULL"* ]]
}

@test "pointer plus doctor runs the vendored subset" {
    bump_pointer
    bump_doctor
    gate pytest echo VENDOR ::: echo FULL
    [[ "$output" == *"VENDOR"* ]]
    [[ "$output" != *"FULL"* ]]
}

@test "a pointer bump with any other path runs the full command" {
    bump_pointer
    echo b >> "$REPO/src/a.py"
    git -C "$REPO" add src/a.py
    gate pytest echo VENDOR ::: echo FULL
    [ "$status" -eq 0 ]
    [[ "$output" == *"FULL"* ]]
    [[ "$output" != *"VENDOR"* ]]
}

@test "a deletion-only commit runs the full command" {
    # pre-commit's own file list drops deletions (--diff-filter=ACMRTUXB), which
    # is why a files:/exclude: rule could not express this gate.
    git -C "$REPO" rm --quiet src/a.py
    gate pytest echo VENDOR ::: echo FULL
    [[ "$output" == *"FULL"* ]]
    [[ "$output" != *"VENDOR"* ]]
}

@test "a gitlink removed from the index runs the full command" {
    # .gitmodules untouched, and the subset would read the working-tree copy.
    git -C "$REPO" rm --quiet --cached skills-vendor/fake
    gate pytest echo VENDOR ::: echo FULL
    [[ "$output" == *"FULL"* ]]
    [[ "$output" != *"VENDOR"* ]]
}

@test "a gitlink replaced by a file runs the full command" {
    git -C "$REPO" rm --quiet --cached skills-vendor/fake
    mkdir -p "$REPO/skills-vendor"
    echo vendored-by-hand > "$REPO/skills-vendor/fake"
    git -C "$REPO" add skills-vendor/fake
    gate pytest echo VENDOR ::: echo FULL
    [[ "$output" == *"FULL"* ]]
    [[ "$output" != *"VENDOR"* ]]
}

@test "a deleted .skills/doctor.sh runs the full command" {
    git -C "$REPO" rm --quiet .skills/doctor.sh
    gate pytest echo VENDOR ::: echo FULL
    [[ "$output" == *"FULL"* ]]
    [[ "$output" != *"VENDOR"* ]]
}

@test "a .skills/doctor.sh replaced by a symlink runs the full command" {
    # The hook only commits the doctor as a regular file (#753, from
    # CannObserv/cli's port, gregoryfoster/skills#572).
    ln -sfn /dev/null "$REPO/.skills/doctor.sh"
    git -C "$REPO" add .skills/doctor.sh
    gate pytest echo VENDOR ::: echo FULL
    [[ "$output" == *"FULL"* ]]
    [[ "$output" != *"VENDOR"* ]]
}

@test "an executable .skills/doctor.sh runs the vendored subset" {
    # The installed doctor is 100755; both regular-file modes stay on the fast path.
    bump_doctor
    chmod +x "$REPO/.skills/doctor.sh"
    git -C "$REPO" add .skills/doctor.sh
    gate pytest echo VENDOR ::: echo FULL
    [[ "$output" == *"VENDOR"* ]]
    [[ "$output" != *"FULL"* ]]
}

@test "a pointer bump plus a deletion runs the full command" {
    # A diff filter that dropped deletions would leave the pointer alone and
    # read this as vendor-only (CannObserv/cli's port, gregoryfoster/skills#572).
    bump_pointer
    git -C "$REPO" rm --quiet src/a.py
    gate pytest echo VENDOR ::: echo FULL
    [[ "$output" == *"FULL"* ]]
    [[ "$output" != *"VENDOR"* ]]
}

@test "a file moved onto .skills/doctor.sh runs the full command" {
    # Rename detection would list only the destination, hiding the deletion.
    git -C "$REPO" rm --quiet .skills/doctor.sh
    git -C "$REPO" commit --quiet --no-verify -m "drop doctor"
    mkdir -p "$REPO/.skills"
    git -C "$REPO" mv src/a.py .skills/doctor.sh
    gate pytest echo VENDOR ::: echo FULL
    [[ "$output" == *"FULL"* ]]
    [[ "$output" != *"VENDOR"* ]]
}

@test "a pointer bump with .gitmodules changed runs the full command" {
    bump_pointer
    printf '# edited\n' >> "$REPO/.gitmodules"
    git -C "$REPO" add .gitmodules
    gate pytest echo VENDOR ::: echo FULL
    [[ "$output" == *"FULL"* ]]
}

@test "a file under skills-vendor/ that is no registered submodule runs the full command" {
    mkdir -p "$REPO/skills-vendor"
    echo x > "$REPO/skills-vendor/README"
    git -C "$REPO" add skills-vendor/README
    gate pytest echo VENDOR ::: echo FULL
    [[ "$output" == *"FULL"* ]]
    [[ "$output" != *"VENDOR"* ]]
}

@test "nothing staged runs the full command" {
    gate pytest echo VENDOR ::: echo FULL
    [[ "$output" == *"FULL"* ]]
    [[ "$output" != *"VENDOR"* ]]
}

@test "outside a git repo the full command runs" {
    run bash -c "cd '$BATS_TEST_TMPDIR' && GIT_CEILING_DIRECTORIES='$BATS_TEST_TMPDIR' bash '$GATE' pytest echo VENDOR ::: echo FULL"
    [[ "$output" == *"FULL"* ]]
    [[ "$output" != *"VENDOR"* ]]
}

@test "an empty subset skips the stage on a vendor-only commit" {
    bump_pointer
    gate vitest ::: echo FULL
    [ "$status" -eq 0 ]
    [[ "$output" != *"FULL"* ]]
    [[ "$output" == *"vitest: vendor-only commit (#751)"*"skipped"* ]]
}

@test "the subset's failure is the gate's failure" {
    bump_pointer
    gate pytest false ::: echo FULL
    [ "$status" -ne 0 ]
}

@test "the full command's failure is the gate's failure" {
    gate pytest echo VENDOR ::: false
    [ "$status" -ne 0 ]
}

@test "no ::: separator is a usage error" {
    gate pytest echo FULL
    [ "$status" -eq 2 ]
}

@test "the hook's pathspec commit sees only its own paths" {
    # The SessionStart hook commits `git commit -m … -- <its paths>`, which
    # builds a temporary index; a file the operator had staged must not pull
    # the commit onto the full tier, nor be swept into it. Crosses the seam:
    # a real pre-commit hook calling the gate inside a real commit.
    mkdir -p "$REPO/hooks"
    printf '#!/bin/sh\nexec bash %q pytest echo VENDOR ::: echo FULL >> %q\n' \
        "$GATE" "$BATS_TEST_TMPDIR/gate.out" > "$REPO/hooks/pre-commit"
    chmod +x "$REPO/hooks/pre-commit"
    git -C "$REPO" config core.hooksPath hooks
    echo b >> "$REPO/src/a.py"
    git -C "$REPO" add src/a.py
    # A pathspec commit re-reads its paths from the working tree, so the pointer
    # needs a real repo behind it: its HEAD is the bumped gitlink.
    local sub="$REPO/skills-vendor/fake"
    git init --quiet --template= "$sub"
    git -C "$sub" -c user.email=t@example.invalid -c user.name=t \
        commit --quiet --allow-empty -m upstream
    git -C "$REPO" -c advice.addEmbeddedRepo=false add skills-vendor/fake
    bump_doctor
    run git -C "$REPO" commit --quiet -m bump -- skills-vendor/fake .skills/doctor.sh
    [ "$status" -eq 0 ]
    run git -C "$REPO" show --name-only --format= HEAD
    [ "$output" = $'.skills/doctor.sh\nskills-vendor/fake' ]
    run cat "$BATS_TEST_TMPDIR/gate.out"
    [[ "$output" == *"VENDOR"* ]]
    [[ "$output" != *"FULL"* ]]
    # The operator's staged file is still staged, not committed.
    run git -C "$REPO" diff --cached --name-only
    [ "$output" = "src/a.py" ]
}
