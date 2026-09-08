#!/usr/bin/env bash
# Exercises the built container image, not the source tree.
#
# The class of defect every other gate in this repository is blind to: the image
# builds, starts, answers its health check, and is still broken because
# something the code needs is not in it. So the assertions below run *inside*
# the container, and the central one is the project's own gate — an image that
# starts is not an image that works.
#
# Failures are counted rather than fatal, so one run reports everything that is
# wrong instead of the first thing.
set -euo pipefail

IMAGE="${1:?usage: smoke-test.sh <image[:tag]>}"
failures=0

# Docker Desktop on Windows does not share a `mktemp -d` path: a bind mount of
# one silently yields an empty directory and the command inside runs happily
# against nothing. Keep scratch space under the project directory and convert
# the path with `pwd -W` under Git Bash, where it yields the Windows form and
# fails everywhere else (in which case $PWD is already right).
WORKDIR_HOST="$(cd "${PWD}" && pwd -W 2>/dev/null || printf %s "${PWD}")"
SCRATCH_LOCAL="${PWD}/var/smoke"
SCRATCH_HOST="${WORKDIR_HOST}/var/smoke"
rm -rf "${SCRATCH_LOCAL}"
mkdir -p "${SCRATCH_LOCAL}"
# And make it group- and world-writable, because the image deliberately does not
# run as root. On Linux a bind mount carries the host's ownership through, so a
# directory owned by the CI runner's user is one the container's user cannot
# write to, and every report assertion fails with a permission error. Docker
# Desktop on Windows ignores ownership entirely, so this failure appears only in
# CI — which is exactly where it is most expensive to diagnose.
chmod 0777 "${SCRATCH_LOCAL}" 2>/dev/null || true

cleanup() {
  rm -rf "${SCRATCH_LOCAL}"
}
trap cleanup EXIT

run() { # run <docker run arguments...>
  docker run --rm \
    -v "${SCRATCH_HOST}:/app/reports" \
    "${IMAGE}" "$@"
}

check() { # check <description> <command...>
  local description="$1"
  shift
  if "$@" >/dev/null 2>&1; then
    echo "  ok    ${description}"
  else
    echo "  FAIL  ${description}"
    failures=$((failures + 1))
  fi
}

check_fails() { # check_fails <description> <expected code> <command...>
  local description="$1" expected="$2"
  shift 2
  local status=0
  "$@" >/dev/null 2>&1 || status=$?
  if [ "${status}" -eq "${expected}" ]; then
    echo "  ok    ${description} (exit ${status})"
  else
    echo "  FAIL  ${description}: expected exit ${expected}, got ${status}"
    failures=$((failures + 1))
  fi
}

EXAMPLE=(
  --suite examples/support.yaml
  --cassette examples/support-cassette.json
  --judge-cassette examples/support-judge-cassette.json
  --judge-model demo-judge
)

echo "--- the image is what it claims to be"
check "the entry point runs with no arguments" \
  docker run --rm "${IMAGE}"
check "the process does not run as root" \
  docker run --rm --entrypoint sh "${IMAGE}" -c '[ "$(id -u)" != "0" ]'
check "the package is importable from the installed environment" \
  docker run --rm --entrypoint python "${IMAGE}" -c "import aievals; print(aievals.__version__)"
check "the console script is on PATH" \
  docker run --rm --entrypoint sh "${IMAGE}" -c "command -v aievals"

echo
echo "--- the registries are populated in the shipped image"
check "graders are registered" \
  docker run --rm "${IMAGE}" list graders
check "every provider is registered" \
  docker run --rm --entrypoint sh "${IMAGE}" -c \
    "aievals list providers | grep -q openai_compatible"
check "mutators are registered" \
  docker run --rm --entrypoint sh "${IMAGE}" -c "aievals list mutators | grep -q corrupt_json"

echo
echo "--- the shipped image passes its own gate"
# Rule 1: assert against the artefact's own quality gate, not just its
# liveness. This is the assertion the whole script exists for.
gate_output="$(mktemp)"
if docker run --rm "${IMAGE}" gate "${EXAMPLE[@]}" \
    --baseline examples/support-baseline.json --mutate >"${gate_output}" 2>&1; then
  echo "  ok    the shipped image gates the worked example green"
else
  echo "  FAIL  the shipped image did not pass its own gate"
  tail -25 "${gate_output}"
  failures=$((failures + 1))
fi

check "the meta-gate catches every core mutant inside the image" \
  docker run --rm "${IMAGE}" mutate "${EXAMPLE[@]}"

echo
echo "--- the gate can still fail inside the image"
# A gate that only ever passes is indistinguishable from no gate. These assert
# the specific exit codes, not merely non-zero.
check_fails "an impossible pass rate fails the gate with exit 2" 2 \
  docker run --rm "${IMAGE}" run "${EXAMPLE[@]}" --min-pass-rate 1.1
check_fails "a missing suite fails with exit 3, not 2" 3 \
  docker run --rm "${IMAGE}" run --suite does-not-exist.yaml
check_fails "a live provider is refused in a hermetic run" 3 \
  docker run --rm --entrypoint sh "${IMAGE}" -c \
    "printf 'name: net\nprovider:\n  name: openai_compatible\n  model: m\n  options: {base_url: \"https://example.invalid/v1\"}\ncases:\n  - {id: a, prompt: p}\n' > /tmp/net.yaml && aievals run --suite /tmp/net.yaml"

echo
echo "--- reports are written where they are asked for"
mount_error="$(mktemp)"
if docker run --rm -v "${SCRATCH_HOST}:/app/reports" "${IMAGE}" \
    run "${EXAMPLE[@]}" \
    --json-out reports/evals.json --junit-out reports/evals.xml \
    --markdown-out reports/evals.md >/dev/null 2>"${mount_error}"; then
  check "the bind mount is visible and writable from inside the container" \
    test -s "${SCRATCH_LOCAL}/evals.json"
  check "the JUnit report is well-formed XML" \
    python -c "import sys,xml.etree.ElementTree as t; t.parse(sys.argv[1])" \
      "${SCRATCH_LOCAL}/evals.xml"
  check "the Markdown summary leads with the verdict" \
    grep -q "^# Evaluation gate" "${SCRATCH_LOCAL}/evals.md"
else
  echo "  FAIL  the image could not write reports to a mounted directory"
  # Print what the container said. Without this the assertion names a symptom
  # and the cause — almost always a permission on the mount — is invisible.
  sed 's/^/        /' "${mount_error}" | tail -5
  failures=$((failures + 1))
fi
rm -f "${mount_error}"

echo
if [ "${failures}" -eq 0 ]; then
  echo "smoke test passed for ${IMAGE}"
else
  echo "smoke test FAILED for ${IMAGE}: ${failures} assertion(s)"
  exit 1
fi
