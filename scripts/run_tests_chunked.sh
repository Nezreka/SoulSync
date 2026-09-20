#!/usr/bin/env bash
# Run the test suite as independent processes, one per tests/ subdirectory plus
# four slices of the ~700 files directly under tests/.
#
# Why not one `pytest`: the suite starts real background workers (metadata,
# artwork, video repair) and shares one asyncio loop through
# utils.async_helpers. State a file leaves behind has repeatedly wedged a later
# file that passes on its own, and once --timeout breaks such a test the shared
# loop stays broken, so every later test using it errors too. One run then
# reports dozens of failures that are neither real nor reproducible. Separate
# processes cannot leak into each other, and the whole suite still finishes in
# about the same wall time.
#
# Exit code is the worst of the chunks. Each chunk prints its own summary line.
set -uo pipefail
cd "$(dirname "$0")/.."

# CI has the venv on PATH as `python`; locally point PYTHON at .venv/bin/python
PYTHON="${PYTHON:-python}"
PYTEST=("$PYTHON" -m pytest -q -p no:cacheprovider)
worst=0
run() {
    local label="$1"; shift
    local out status
    out=$("${PYTEST[@]}" "$@" 2>&1)
    status=$?
    printf '%-34s %s\n' "$label" "$(tail -n 1 <<<"$out")"
    if [ "$status" -ne 0 ] && [ "$status" -ne 5 ]; then   # 5 = no tests collected
        printf '%s\n' "$out" | tail -n 60
        worst=1
    fi
}

for d in tests/*/; do
    case "$d" in
        */__pycache__/|tests/data/|tests/support/|tests/static/) continue ;;
    esac
    run "$d" "$d"
done

mapfile -t root < <(ls tests/*.py | sort)
slice_size=$(( (${#root[@]} + 3) / 4 ))
for i in 0 1 2 3; do
    part=("${root[@]:$((i * slice_size)):$slice_size}")
    [ ${#part[@]} -eq 0 ] && continue
    run "tests/*.py [$((i + 1))/4]" "${part[@]}"
done

exit "$worst"
