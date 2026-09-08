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
"""

from __future__ import annotations

import sys
from pathlib import Path

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


def main() -> int:
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

    cassette.save(CASSETTE)
    judge_cassette.save(JUDGE_CASSETTE)
    print(f"wrote {len(cassette)} completion(s) to {CASSETTE.relative_to(ROOT)}")
    print(f"wrote {len(judge_cassette)} judge verdict(s) to {JUDGE_CASSETTE.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
