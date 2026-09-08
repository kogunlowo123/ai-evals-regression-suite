"""The command line.

Seven commands, and the split between them is the design of the whole tool:

``run``      execute a suite and apply its own thresholds and budgets
``gate``     the same, plus a comparison against a baseline. The CI command.
``baseline`` record what the suite does today, to be committed
``mutate``   the meta-gate: does this suite discriminate at all?
``record``   capture cassettes from a live provider, once, deliberately
``doctor``   what this installation would do, without needing a recording
``list``     the grader, provider and mutator registries

Two conventions hold everywhere:

**The report goes to stdout and everything else to stderr.** A build script
pipes stdout into a parser; a log line in the middle of that is a broken build
with a confusing cause.

**Exit codes distinguish "the gate failed" (2) from "the harness failed" (3).**
They call for different responses — one is a bad change, the other is a broken
runner — and collapsing them into a single non-zero code makes a CI job unable
to tell a regression from an outage.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from aievals import __version__
from aievals.baseline import Baseline, compare
from aievals.config import Settings, load
from aievals.errors import (
    EXIT_ERROR,
    EXIT_GATE_FAILED,
    EXIT_OK,
    AievalsError,
)
from aievals.gate import GateReport, evaluate
from aievals.graders import registered_names as grader_names
from aievals.logging import configure
from aievals.mutation import MutationHarness, MutationReport, mutator_names, select
from aievals.providers import Cassette, Provider, build_provider
from aievals.providers import registered_names as provider_names
from aievals.providers.base import CompletionRequest
from aievals.report import json_report, junit, markdown
from aievals.runner import Runner, RunOptions, RunResult
from aievals.suite import Suite, load_suite, parse_suite, suite_digest
from aievals.suite.models import ProviderSpec

#: A suite used by ``doctor``: small, self-contained, and passing by
#: construction against the echo provider, so the command proves the pipeline
#: works on a machine with no recordings and no credentials.
DOCTOR_SUITE = """
name: doctor
description: A self-check that needs no model, no recording and no credential.
provider:
  name: echo
  model: echo
cases:
  - id: echo-returns-the-prompt
    prompt: The quick brown fox jumps over 3 lazy dogs.
    graders:
      - type: contains_all
        params:
          values: ["quick brown fox"]
      - type: numeric_close
        params:
          expected: 3
          where: first
"""


def _use_utf8(stream: Any) -> None:
    """Force UTF-8 on a standard stream.

    Without this the tool crashes on a Windows console, whose default encoding
    is cp1252: the report is JSON, model output is arbitrary Unicode, and a
    single ``≥`` or an emoji in a grader detail raises ``UnicodeEncodeError``
    *after* the whole run has been paid for. Found by the end-to-end tests, not
    by anything smaller — the failure needs a real process with a real console
    encoding.

    ``errors="replace"`` rather than ``strict``: losing a character from a
    report is a blemish, and losing the report is a build nobody can diagnose.
    """
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is None:  # pragma: no cover - not a TextIOWrapper
        return
    try:
        reconfigure(encoding="utf-8", errors="replace")
    except (ValueError, OSError):  # pragma: no cover - a stream that refuses
        return


def main(argv: list[str] | None = None) -> int:
    """Entry point. Returns a process exit code rather than raising."""
    _use_utf8(sys.stdout)
    _use_utf8(sys.stderr)

    parser = build_parser()
    args = parser.parse_args(argv)

    # Inside the try, not above it. Reading the settings is the first thing that
    # can fail on a badly configured CI runner, and it was the one failure that
    # escaped as a traceback and exit 1 — in exactly the deployment where the
    # settings module's own docstring says a typo has to be visible.
    try:
        settings = load()
        configure(
            level=args.log_level or settings.log.level,
            json_output=settings.log.format == "json",
        )
        return int(args.handler(args, settings))
    except AievalsError as exc:
        print(exc.render(), file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:  # pragma: no cover - interactive only
        print("interrupted", file=sys.stderr)
        return EXIT_ERROR


# -- parser --------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser."""
    parser = argparse.ArgumentParser(
        prog="aievals",
        description="Evaluations that fail builds.",
    )
    parser.add_argument("--version", action="version", version=f"aievals {__version__}")
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default=None,
        help="override AIEVALS_LOG__LEVEL for this invocation",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("run", help="run a suite and apply its thresholds")
    _add_suite_arguments(run)
    _add_output_arguments(run)
    run.set_defaults(handler=_command_run)

    gate = subparsers.add_parser("gate", help="run a suite and compare it against a baseline")
    _add_suite_arguments(gate)
    _add_output_arguments(gate)
    gate.add_argument("--baseline", required=True, type=Path, help="path to the baseline file")
    gate.add_argument(
        "--allow-stale-baseline",
        action="store_true",
        help=(
            "downgrade a suite-digest mismatch from a failure to a warning. "
            "The comparison it produces is not a fact about this change."
        ),
    )
    gate.add_argument(
        "--mutate",
        action="store_true",
        help="also run the meta-gate and include it in the report",
    )
    gate.add_argument(
        "--min-caught",
        type=float,
        default=None,
        help="with --mutate, the mutation score required to pass (default 1.0)",
    )
    gate.set_defaults(handler=_command_gate)

    baseline = subparsers.add_parser("baseline", help="record a baseline from a run")
    _add_suite_arguments(baseline)
    baseline.add_argument("--out", required=True, type=Path, help="where to write the baseline")
    baseline.add_argument("--note", default="", help="a line recorded in the baseline")
    baseline.set_defaults(handler=_command_baseline)

    mutate = subparsers.add_parser("mutate", help="the meta-gate: can this suite catch anything?")
    _add_suite_arguments(mutate)
    mutate.add_argument(
        "--set",
        dest="mutator_set",
        choices=["core", "extended", "all"],
        default="core",
        help="which mutators to apply (default: core)",
    )
    mutate.add_argument(
        "--mutator",
        dest="mutators",
        action="append",
        default=[],
        help="apply only this mutator; repeatable",
    )
    mutate.add_argument(
        "--min-caught",
        type=float,
        default=1.0,
        help="fraction of mutants that must be caught (default: 1.0)",
    )
    mutate.add_argument("--json-out", type=Path, default=None, help="write the report here")
    mutate.set_defaults(handler=_command_mutate)

    record = subparsers.add_parser("record", help="capture cassettes from a live provider")
    _add_suite_arguments(record)
    record.add_argument("--out", required=True, type=Path, help="where to write the cassette")
    record.set_defaults(handler=_command_record)

    doctor = subparsers.add_parser("doctor", help="what this installation would do")
    doctor.add_argument("--suite", type=Path, default=None, help="inspect this suite too")
    doctor.set_defaults(handler=_command_doctor)

    listing = subparsers.add_parser("list", help="the grader, provider and mutator registries")
    listing.add_argument(
        "what",
        choices=["graders", "providers", "mutators", "all"],
        nargs="?",
        default="all",
    )
    listing.set_defaults(handler=_command_list)

    return parser


def _add_suite_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--suite", required=True, type=Path, help="path to the suite file")
    parser.add_argument(
        "--cassette",
        type=Path,
        default=None,
        help="replay from this cassette, whatever provider the suite names",
    )
    parser.add_argument(
        "--judge-cassette",
        type=Path,
        default=None,
        help="replay model-graded checks from this cassette",
    )
    parser.add_argument("--judge-model", default="", help="model name recorded for judge requests")
    parser.add_argument(
        "--samples",
        type=int,
        default=None,
        help="completions per case. Above 1 the comparison becomes statistical.",
    )
    parser.add_argument(
        "--policy",
        choices=["all", "majority", "any"],
        default="all",
        help="how per-sample outcomes combine into a case verdict (default: all)",
    )
    parser.add_argument(
        "--tag", dest="tags", action="append", default=[], help="only run cases with this tag"
    )
    parser.add_argument("--concurrency", type=int, default=None, help="cases in flight at once")
    parser.add_argument(
        "--allow-network",
        action="store_true",
        help=(
            "permit a provider that opens a socket. Off by default: a gate that "
            "can reach a vendor has a verdict that depends on that vendor."
        ),
    )


def _add_output_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json-out", type=Path, default=None, help="write the JSON report here")
    parser.add_argument("--junit-out", type=Path, default=None, help="write JUnit XML here")
    parser.add_argument("--markdown-out", type=Path, default=None, help="write a summary here")
    parser.add_argument(
        "--include-responses",
        action="store_true",
        help=(
            "embed full model responses in the JSON report. Off by default: "
            "reports are artefacts, and responses are derived from prompts that "
            "may carry personal data. With it off, a response is replaced by its "
            "length and a digest; a grader detail may still quote a bounded "
            "fragment, because that fragment is why the build is red."
        ),
    )
    parser.add_argument(
        "--min-pass-rate",
        type=float,
        default=None,
        help="override the suite's own minimum pass rate",
    )


# -- commands ------------------------------------------------------------


def _command_run(args: argparse.Namespace, settings: Settings) -> int:
    suite, result = asyncio.run(_execute(args, settings))
    report = evaluate(result, suite, min_pass_rate=args.min_pass_rate)
    _emit(report, args)
    return report.exit_code


def _command_gate(args: argparse.Namespace, settings: Settings) -> int:
    suite, result = asyncio.run(_execute(args, settings))
    baseline = Baseline.load(args.baseline)
    comparison = compare(baseline, result, alpha=suite.thresholds.alpha)
    report = evaluate(
        result,
        suite,
        comparison=comparison,
        allow_stale_baseline=args.allow_stale_baseline,
        min_pass_rate=args.min_pass_rate,
    )

    mutation: MutationReport | None = None
    exit_code = report.exit_code
    if args.mutate:
        mutation = asyncio.run(MutationHarness().analyse(suite, result))
        minimum = 1.0 if args.min_caught is None else args.min_caught
        if mutation.score < minimum:
            # Reported separately from the gate findings so a reader can tell
            # "the model got worse" from "the suite cannot tell".
            print(
                f"FAIL  MUTATION_SCORE: {mutation.summary()}, below the required {minimum:.0%}.",
                file=sys.stderr,
            )
            exit_code = EXIT_GATE_FAILED

    _emit(report, args, mutation=mutation)
    return exit_code


def _command_baseline(args: argparse.Namespace, settings: Settings) -> int:
    suite, result = asyncio.run(_execute(args, settings))
    written = Baseline.from_run(result, note=args.note).save(args.out)
    print(
        f"recorded {result.passed}/{result.total} for {suite.name!r} into {written}",
        file=sys.stderr,
    )
    print(json.dumps({"baseline": str(written), "pass_rate": round(result.pass_rate, 6)}))
    return EXIT_OK


def _command_mutate(args: argparse.Namespace, settings: Settings) -> int:
    suite, result = asyncio.run(_execute(args, settings))
    try:
        mutators = select(tuple(args.mutators), mutator_set=args.mutator_set)
    except KeyError as exc:
        raise AievalsError(str(exc.args[0])) from exc

    mutation = asyncio.run(MutationHarness(mutators=mutators).analyse(suite, result))
    document = mutation.as_dict()
    # File or stdout, never both — the same convention `_emit` uses for the
    # gate report. Writing to both means a job that redirects stdout into a
    # file gets the document twice, in two places, and has to know which one
    # the next step reads.
    if args.json_out:
        _write(args.json_out, json.dumps(document, indent=2, ensure_ascii=False))
    else:
        print(json.dumps(document, indent=2, ensure_ascii=False))

    print(f"\n{mutation.summary()}", file=sys.stderr)
    for mutant in mutation.survivors:
        print(
            f"  SURVIVED  {mutant.case_id} / {mutant.mutator}: {mutant.expectation}\n"
            f"            the suite accepted: {mutant.excerpt}",
            file=sys.stderr,
        )
    for case_id, reason in mutation.skipped:
        print(f"  skipped   {case_id}: {reason}", file=sys.stderr)

    if mutation.score < args.min_caught:
        print(
            f"FAIL  the mutation score {mutation.score:.1%} is below the required "
            f"{args.min_caught:.1%}.",
            file=sys.stderr,
        )
        return EXIT_GATE_FAILED
    return EXIT_OK


def _command_record(args: argparse.Namespace, _settings: Settings) -> int:
    if not args.allow_network:
        raise AievalsError(
            "recording needs a live provider, and this run is hermetic.",
            remedy="Pass --allow-network, having checked which endpoint the suite names.",
        )
    suite = load_suite(args.suite)
    provider = build_provider(suite.provider, hermetic=False)
    cassette = Cassette()

    async def capture() -> int:
        count = 0
        for case in suite.cases:
            request = CompletionRequest(
                prompt=case.prompt,
                model=suite.provider.model,
                system=case.system,
                temperature=suite.provider.temperature,
                max_tokens=suite.provider.max_tokens,
            )
            cassette.put(request, await provider.complete(request))
            count += 1
        await provider.aclose()
        return count

    recorded = asyncio.run(capture())
    written = cassette.save(args.out)
    print(f"recorded {recorded} completion(s) into {written}", file=sys.stderr)
    print(json.dumps({"cassette": str(written), "entries": recorded}))
    return EXIT_OK


def _command_doctor(args: argparse.Namespace, settings: Settings) -> int:
    lines: list[str] = [f"aievals {__version__}", ""]
    lines.append(f"graders    {', '.join(grader_names())}")
    lines.append(f"providers  {', '.join(provider_names())}")
    lines.append(f"mutators   {', '.join(mutator_names())}")
    lines.append("")
    lines.append(f"concurrency     {settings.run.concurrency}")
    lines.append(f"case timeout    {settings.run.case_timeout_s}s")
    lines.append(f"log             {settings.log.level} as {settings.log.format}, to stderr")
    lines.append("")

    suite = parse_suite(DOCTOR_SUITE, origin="<doctor>")
    provider = build_provider(suite.provider, hermetic=True)
    result = asyncio.run(Runner(provider).run(suite))
    verdict = "ok" if result.pass_rate == 1.0 else "FAILED"
    lines.append(f"self-check      {verdict}: {result.passed}/{result.total} through 'echo'")

    if args.suite:
        inspected = load_suite(args.suite)
        lines.append("")
        lines.append(f"suite           {inspected.name} ({len(inspected.cases)} cases)")
        lines.append(f"digest          {suite_digest(inspected)}")
        lines.append(f"provider        {inspected.provider.name} / {inspected.provider.model}")
        thresholds = inspected.thresholds
        lines.append(
            f"thresholds      min_pass_rate={thresholds.min_pass_rate} "
            f"p95={thresholds.max_p95_latency_ms} tokens={thresholds.max_total_tokens}"
        )

    print("\n".join(lines))
    return EXIT_OK if result.pass_rate == 1.0 else EXIT_ERROR


def _command_list(args: argparse.Namespace, _settings: Settings) -> int:
    payload: dict[str, Any] = {}
    if args.what in {"graders", "all"}:
        payload["graders"] = list(grader_names())
    if args.what in {"providers", "all"}:
        payload["providers"] = list(provider_names())
    if args.what in {"mutators", "all"}:
        payload["mutators"] = list(mutator_names())
    print(json.dumps(payload, indent=2))
    return EXIT_OK


# -- shared --------------------------------------------------------------


async def _execute(args: argparse.Namespace, settings: Settings) -> tuple[Suite, RunResult]:
    suite = load_suite(args.suite)
    hermetic = not args.allow_network

    provider = _provider_for(suite, args, hermetic=hermetic)
    judge = _judge_for(args)

    options = RunOptions(
        samples=args.samples if args.samples is not None else settings.run.samples,
        policy=args.policy,
        concurrency=(
            args.concurrency if args.concurrency is not None else settings.run.concurrency
        ),
        case_timeout_s=settings.run.case_timeout_s,
        tags=tuple(args.tags),
        hermetic=hermetic,
    )
    runner = Runner(provider, options=options, judge=judge, judge_model=args.judge_model)
    try:
        return suite, await runner.run(suite)
    finally:
        await provider.aclose()
        if judge is not None:
            await judge.aclose()


def _provider_for(suite: Suite, args: argparse.Namespace, *, hermetic: bool) -> Provider:
    if args.cassette is not None:
        # An explicit cassette overrides the suite's provider. This is what a CI
        # job does: the suite names the model it was recorded against, and the
        # gate replays it.
        spec = ProviderSpec(
            name="replay",
            model=suite.provider.model,
            temperature=suite.provider.temperature,
            max_tokens=suite.provider.max_tokens,
            options={"cassette": Cassette.load(args.cassette)},
        )
        return build_provider(spec, hermetic=hermetic)
    return build_provider(suite.provider, hermetic=hermetic)


def _judge_for(args: argparse.Namespace) -> Provider | None:
    if args.judge_cassette is None:
        return None
    spec = ProviderSpec(
        name="replay",
        model=args.judge_model,
        options={"cassette": Cassette.load(args.judge_cassette)},
    )
    return build_provider(spec, hermetic=True)


def _emit(
    report: GateReport, args: argparse.Namespace, *, mutation: MutationReport | None = None
) -> None:
    document = json_report.render(
        report, include_responses=args.include_responses, mutation=mutation
    )
    if args.json_out:
        _write(args.json_out, document)
    else:
        print(document)

    if args.junit_out:
        _write(args.junit_out, junit.render(report))
    if args.markdown_out:
        _write(args.markdown_out, markdown.render(report, mutation=mutation))

    run = report.run
    verdict = "PASSED" if report.passed else "FAILED"
    print(
        f"\n{verdict}  {run.suite_name}: {run.passed}/{run.total} "
        f"({run.pass_rate:.1%}) via {run.provider}",
        file=sys.stderr,
    )
    for finding in report.findings:
        print(finding.render(), file=sys.stderr)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
