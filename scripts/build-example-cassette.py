#!/usr/bin/env python
"""Build the example cassettes from the authored answers.

Deterministic and reproducible: run it twice, get the same files apart from the
recorded timestamps. It exists so the worked example in this repository can be
regenerated after a suite edit rather than hand-maintained, and so that nobody
has to take on trust that the cassettes match the suite — the digest check in
``aievals gate`` would catch it, but regenerating is faster than debugging it.

The judge cassette is built by asking the ``judge`` grader itself to compose the
prompt it will later send. That keeps the recording keyed to the exact prompt
the grader produces; hand-writing that string would work until the prompt
changed, and then fail as a cassette miss with no obvious cause.

``--check`` answers the question CI actually has — *would the committed
cassettes still serve this suite?* — without writing anything.

It is not implemented as "regenerate and ``git diff``", which is the obvious
thing and is wrong: ``Cassette.put`` stamps ``recorded_at`` with the current
time, so a regenerated file always differs from the committed one and the check
would fail on every run, for every contributor, with a message telling them to
run a command that cannot help. The comparison is over what a replay consumes —
the fingerprints and the completion text — and deliberately not over the
timestamps or the ``recorded_with`` version string, neither of which a gate
reads.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from aievals.graders.judge import JUDGE_SYSTEM, Judge
from aievals.graders.registry import build_grader
from aievals.providers.base import Completion, CompletionRequest
from aievals.providers.cassette import Cassette
from aievals.suite import load_suite

SUITE = ROOT / "examples" / "support.yaml"
ANSWERS = ROOT / "examples" / "support-answers.yaml"
CASSETTE = ROOT / "examples" / "support-cassette.json"
JUDGE_CASSETTE = ROOT / "examples" / "support-judge-cassette.json"
JUDGE_MODEL = "demo-judge"


def _replayable(payload: dict[str, Any]) -> dict[str, str]:
    """Project a cassette payload down to what a replay actually reads.

    Fingerprint to completion text. Everything a gate consults is covered:
    a changed prompt, model, temperature or token limit moves the fingerprint,
    and a changed answer moves the text. Nothing a gate ignores is covered, so
    the check does not fire on a timestamp or a version bump.
    """
    projection: dict[str, str] = {}
    for entry in payload.get("entries", []):
        fingerprint = str(entry.get("fingerprint", ""))
        completion = entry.get("completion", {})
        text = completion.get("text", "") if isinstance(completion, dict) else ""
        projection[fingerprint] = str(text)
    return projection


def _check(built: Cassette, committed_path: Path) -> list[str]:
    """Return one complaint per way *committed_path* has drifted from *built*."""
    if not committed_path.exists():
        return [f"{committed_path.name} is missing"]
    try:
        committed = json.loads(committed_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [f"{committed_path.name} is not valid JSON: {exc}"]

    want = _replayable(built.to_payload())
    have = _replayable(committed if isinstance(committed, dict) else {})

    problems: list[str] = []
    for fingerprint, text in want.items():
        short = fingerprint[:19]
        if fingerprint not in have:
            problems.append(f"{committed_path.name}: no recording for {short}...")
        elif have[fingerprint] != text:
            problems.append(f"{committed_path.name}: the answer for {short}... has changed")
    # Sorted, because a set difference has no order and a check that reports its
    # findings in a different order each run is a check nobody can diff.
    problems.extend(
        f"{committed_path.name}: {fingerprint[:19]}... is recorded but unused"
        for fingerprint in sorted(have.keys() - want.keys())
    )
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify the committed cassettes still serve the suite; write nothing",
    )
    args = parser.parse_args()

    suite = load_suite(SUITE)
    data = yaml.safe_load(ANSWERS.read_text(encoding="utf-8"))
    responses: dict[str, str] = data["responses"]
    judge_answers: dict[str, str] = data.get("judge", {})

    missing = [case.id for case in suite.cases if case.id not in responses]
    if missing:
        print(f"no authored answer for: {', '.join(missing)}", file=sys.stderr)
        return 1

    cassette = Cassette()
    judge_cassette = Cassette()

    for case in suite.cases:
        text = responses[case.id].strip()
        request = CompletionRequest(
            prompt=case.prompt,
            model=suite.provider.model,
            system=case.system,
            temperature=suite.provider.temperature,
            max_tokens=suite.provider.max_tokens,
        )
        cassette.put(
            request,
            Completion(
                text=text,
                model=suite.provider.model,
                provider="authored",
                latency_ms=120.0,
                prompt_tokens=len(case.prompt.split()),
                completion_tokens=len(text.split()),
            ),
        )

        for spec in suite.graders_for(case):
            if spec.type != "judge":
                continue
            grader = build_grader(spec)
            if not isinstance(grader, Judge):  # pragma: no cover - registry guarantees it
                continue
            verdict = judge_answers.get(case.id)
            if verdict is None:
                print(f"no authored judge verdict for: {case.id}", file=sys.stderr)
                return 1
            judge_request = CompletionRequest(
                prompt=grader.build_prompt(text, case),
                model=JUDGE_MODEL,
                system=JUDGE_SYSTEM,
                temperature=0.0,
                max_tokens=256,
            )
            judge_cassette.put(
                judge_request,
                Completion(
                    text=verdict.strip(),
                    model=JUDGE_MODEL,
                    provider="authored",
                    latency_ms=90.0,
                    prompt_tokens=len(judge_request.prompt.split()),
                    completion_tokens=len(verdict.split()),
                ),
            )

    if args.check:
        problems = _check(cassette, CASSETTE) + _check(judge_cassette, JUDGE_CASSETTE)
        if problems:
            for problem in problems:
                print(problem, file=sys.stderr)
            print("\nRun: python tasks.py fixtures", file=sys.stderr)
            return 1
        print(f"the committed cassettes serve {len(suite.cases)} case(s) unchanged")
        return 0

    cassette.save(CASSETTE)
    judge_cassette.save(JUDGE_CASSETTE)
    print(f"wrote {len(cassette)} completion(s) to {CASSETTE.relative_to(ROOT)}")
    print(f"wrote {len(judge_cassette)} judge verdict(s) to {JUDGE_CASSETTE.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
