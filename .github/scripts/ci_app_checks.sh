#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# ci_app_checks.sh — run the terminal's headless checks against a built binary.
#
#   ci_app_checks.sh selftests <binary> [profile] [per-test-timeout-seconds]
#   ci_app_checks.sh smoke     <binary> [profile] [timeout-seconds]
#
# WHY THIS IS A SCRIPT AND NOT INLINE YAML
#   The self-test flag list used to be hand-copied into build-cpp.yml. It
#   drifted: main.cpp dispatched 10 --selftest-* flags while CI ran 9, so
#   --selftest-live-table was never executed anywhere. One implementation,
#   called from every workflow, cannot drift between workflows — and the list
#   itself is *discovered from the binary* (see below) rather than typed here.
#
# FLAG DISCOVERY
#   Preferred: `<binary> --profile <isolated> --selftest-list` prints one flag
#   per line. The profile is essential: without it, an already-running normal
#   app makes the probe a secondary instance, which exits 0 without reaching
#   self-test dispatch. The probe first greps the binary image for the literal
#   string: if an older main.cpp has no --selftest-list handler, an unknown flag
#   would make the app boot the *full GUI* instead of printing a list, so we
#   must never run the probe blind.
#
# PROFILE ISOLATION
#   Every run passes --profile <name> (default "ci"). --selftest-paper and
#   --selftest-fno-algo drive the paper-trading and F&O engines against
#   whatever profile they are given; without this they would mutate a real
#   user profile's database. Never remove it.
#
# CHECK_SEVERITY
#   Honoured by the `smoke` mode (default "error", so existing
#   callers are unchanged). A non-blocking step sets CHECK_SEVERITY=warning and
#   gets ::warning:: annotations for failures and hangs; the exit status is the
#   same either way. `selftests` always reports errors.
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail

# Self-tests registered in src/app/main.cpp's dispatch loop. FALLBACK ONLY —
# used when the binary does not support --selftest-list. --dump-tools is
# deliberately absent: it is a catalog dump, not a pass/fail check.
FALLBACK_SELFTESTS="
--selftest-tools
--selftest-llm-tools
--selftest-mcp-server
--selftest-ui-tools
--selftest-feeds
--selftest-dock-layout
--selftest-live-table
--selftest-chart-kit
--selftest-fno-algo
--selftest-universe-scan
--selftest-paper
--selftest-portfolio-monitor
--selftest-portfolio-replication
--selftest-arena
"

usage() {
    echo "usage: $0 selftests|smoke <binary> [profile] [timeout-seconds]" >&2
    exit 2
}

# Portable timeout: GNU `timeout` is absent on macOS runners and unreliable in
# git-bash. Run in the background, poll, SIGKILL on overrun. Returns 124 on
# timeout, otherwise the child's exit status.
run_with_timeout() {
    local secs="$1"; shift
    # </dev/null: the self-test loop feeds the flag list on stdin via a
    # heredoc — a child that reads stdin would swallow the remaining flags.
    "$@" </dev/null &
    local pid=$! waited=0
    while kill -0 "$pid" 2>/dev/null; do
        if [ "$waited" -ge "$secs" ]; then
            # SEV is set only by modes that honour CHECK_SEVERITY (smoke).
            echo "::${SEV:-error}::timed out after ${secs}s: $*" >&2
            kill -9 "$pid" 2>/dev/null || true
            wait "$pid" 2>/dev/null || true
            return 124
        fi
        sleep 1
        waited=$((waited + 1))
    done
    wait "$pid"
}

# Echo the discovered self-test flags, one per line. A binary that advertises
# --selftest-list but cannot produce a clean list fails closed: silently using
# the fallback there can skip a newly added suite while reporting success.
discover_selftests() {
    local bin="$1" profile="$2" listing rc=1
    if grep -aq -- '--selftest-list' "$bin" 2>/dev/null; then
        listing="$(mktemp)"
        if run_with_timeout 60 "$bin" --profile "$profile" --selftest-list >"$listing" 2>/dev/null; then
            rc=0
        else
            rc=$?
        fi
        if [ "$rc" -ne 0 ]; then
            echo "::error::${bin##*/} --selftest-list failed (exit ${rc})" >&2
            rm -f "$listing"
            return "$rc"
        fi
        # Accept only if EVERY non-empty line is a --selftest-* flag; any
        # other output means we caught log noise, not a flag listing.
        local cleaned
        cleaned="$(tr -d '\r' <"$listing" | sed '/^[[:space:]]*$/d')"
        rm -f "$listing"
        if [ -z "$cleaned" ]; then
            echo "::error::${bin##*/} advertises --selftest-list but returned no suites" >&2
            return 1
        fi
        if printf '%s\n' "$cleaned" | grep -qvE '^--selftest-[a-z0-9-]+$'; then
            echo "::error::${bin##*/} returned malformed --selftest-list output" >&2
            return 1
        fi
        local count
        count="$(printf '%s\n' "$cleaned" | wc -l | tr -d '[:space:]')"
        echo "discovered ${count} self-test(s) via --selftest-list" >&2
        printf '%s\n' "$cleaned"
        return 0
    fi
    echo "::warning::${bin##*/} predates --selftest-list — using this script's fallback list" >&2
    printf '%s\n' "$FALLBACK_SELFTESTS" | sed '/^[[:space:]]*$/d'
}

SEV=""   # annotation level; set below only by the modes that honour CHECK_SEVERITY
MODE="${1:-}"; BINARY="${2:-}"
[ -n "$MODE" ] && [ -n "$BINARY" ] || usage
if [ ! -f "$BINARY" ]; then
    echo "::error::binary not found: $BINARY" >&2
    exit 1
fi
[ -x "$BINARY" ] || chmod +x "$BINARY" 2>/dev/null || true

# Headless defaults. Windows keeps its native 'windows' platform plugin: the
# staged tree's qt.conf pins Prefix=. so only bundled plugins resolve, and the
# release smoke gate already proves that path works.
case "$(uname -s 2>/dev/null || echo unknown)" in
    MINGW*|MSYS*|CYGWIN*) : ;;
    *) export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-offscreen}" ;;
esac
export QT_OPENGL="${QT_OPENGL:-software}"
export QTWEBENGINE_DISABLE_SANDBOX="${QTWEBENGINE_DISABLE_SANDBOX:-1}"
export QTWEBENGINE_CHROMIUM_FLAGS="${QTWEBENGINE_CHROMIUM_FLAGS:---disable-gpu --no-sandbox --disable-gpu-compositing}"

case "$MODE" in
    selftests)
        PROFILE="${3:-ci}"
        PER_TEST_TIMEOUT="${4:-300}"
        echo "Self-tests: binary=$BINARY profile=$PROFILE platform=${QT_QPA_PLATFORM:-<native>}"
        FAILED=""
        PASSED=0
        if ! SELFTESTS="$(discover_selftests "$BINARY" "$PROFILE")"; then
            echo "::error::could not discover the packaged self-test suite"
            exit 1
        fi
        while IFS= read -r flag; do
            [ -n "$flag" ] || continue
            echo "::group::self-test $flag"
            echo "Running: $BINARY --profile $PROFILE $flag"
            if run_with_timeout "$PER_TEST_TIMEOUT" "$BINARY" --profile "$PROFILE" "$flag"; then
                PASSED=$((PASSED + 1))
                echo "PASS $flag"
            else
                status=$?
                echo "::error::self-test $flag failed (exit $status)"
                FAILED="${FAILED} ${flag}"
            fi
            echo "::endgroup::"
        done <<EOF
$SELFTESTS
EOF
        if [ -n "$FAILED" ]; then
            echo "::error::self-tests failed:${FAILED}"
            exit 1
        fi
        echo "All ${PASSED} self-test(s) passed."
        ;;

    smoke)
        PROFILE="${3:-ci-smoke}"
        TIMEOUT="${4:-600}"
        SEV="${CHECK_SEVERITY:-error}"
        echo "Smoke test: binary=$BINARY profile=$PROFILE platform=${QT_QPA_PLATFORM:-<native>}"
        echo "Walks every registered screen; the last '[Smoke] >>> constructing <id>'"
        echo "line names the screen if the process dies."
        if run_with_timeout "$TIMEOUT" "$BINARY" --smoke-test --profile "$PROFILE"; then
            echo "Smoke test passed — every screen constructed on this runtime."
        else
            status=$?
            if [ "$status" -eq 124 ]; then
                echo "::${SEV}::Smoke test hung (>${TIMEOUT}s) — likely a blocking dialog or a stalled screen"
            else
                echo "::${SEV}::Smoke test failed (exit $status) — see the last '[Smoke] >>> constructing <id>' line above"
            fi
            exit 1
        fi
        ;;

    *) usage ;;
esac
