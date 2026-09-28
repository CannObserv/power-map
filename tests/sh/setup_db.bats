#!/usr/bin/env bats
# Tests for scripts/setup-db.sh's install/start step (#576).
#
# The VM keeps postgresql-client (psql, pg_isready) after its dormant server was
# purged, so "psql on PATH" no longer means "a server is installed". Gating the
# install on psql skipped it, `service postgresql start` failed, and the
# `until pg_isready` loop spun forever. These pin the two halves of the fix:
# detect the server itself, and give up on readiness instead of hanging.
#
# Hermetic: the script runs from a copy under $BATS_TEST_TMPDIR (it writes
# <repo>/env), on a PATH built here — sudo, psql, pg_isready, sleep and
# pg_ctlcluster are stubs; nothing installs, starts or connects to anything.

load helpers

setup() {
    FAKE_REPO="$BATS_TEST_TMPDIR/repo"
    mkdir -p "$FAKE_REPO/scripts" "$FAKE_REPO/src/core"
    cp "$(repo_root)/scripts/setup-db.sh" "$FAKE_REPO/scripts/setup-db.sh"
    : > "$FAKE_REPO/src/core/schema.sql"

    BIN="$BATS_TEST_TMPDIR/bin"
    mkdir -p "$BIN"
    local tool resolved
    for tool in bash env grep head cut sed tr touch cat dirname openssl; do
        resolved="$(command -v "$tool")" || { echo "no $tool on PATH" >&2; return 1; }
        ln -sf "$resolved" "$BIN/$tool"
    done

    STUB_SUDO_LOG="$BATS_TEST_TMPDIR/sudo-calls.log"
    STUB_PG_READY_RC=0
    # sudo: record argv, run nothing.
    printf '#!/usr/bin/env bash\necho "$*" >> "$STUB_SUDO_LOG"\n' > "$BIN/sudo"
    printf '#!/usr/bin/env bash\nexit "$STUB_PG_READY_RC"\n' > "$BIN/pg_isready"
    printf '#!/usr/bin/env bash\nexit 0\n' > "$BIN/psql"
    printf '#!/usr/bin/env bash\nexit 0\n' > "$BIN/sleep"
    chmod +x "$BIN"/sudo "$BIN"/pg_isready "$BIN"/psql "$BIN"/sleep
}

# The server package's cluster tooling; absent on a client-only host.
with_server() {
    printf '#!/usr/bin/env bash\nexit 0\n' > "$BIN/pg_ctlcluster"
    chmod +x "$BIN/pg_ctlcluster"
}

# Clean env, so the stub knobs are passed through explicitly. `timeout` turns a
# hang into exit 124 rather than a stuck suite.
run_setup() {
    run timeout 20 env -i HOME="$HOME" PATH="$BIN" \
        STUB_SUDO_LOG="$STUB_SUDO_LOG" STUB_PG_READY_RC="$STUB_PG_READY_RC" \
        bash "$FAKE_REPO/scripts/setup-db.sh"
}

@test "client-only host (psql present, no server) installs the server" {
    run_setup
    [ "$status" -eq 0 ]
    grep -q -- "apt-get install -y postgresql" "$STUB_SUDO_LOG"
}

@test "host with the server installed skips the install" {
    with_server
    run_setup
    [ "$status" -eq 0 ]
    ! grep -q -- "apt-get install" "$STUB_SUDO_LOG"
}

@test "a server that never becomes ready fails instead of hanging" {
    with_server
    STUB_PG_READY_RC=2
    run_setup
    [ "$status" -ne 0 ]
    [ "$status" -ne 124 ]
    [[ "$output" == *"PostgreSQL did not become ready"* ]]
}
