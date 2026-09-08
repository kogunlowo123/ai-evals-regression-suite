"""Reading suite files, safely.

Three things happen here and the order matters:

1. **Bounded read.** A suite file is read with a size cap before it is parsed.
   Handing an unbounded file to a parser is how a 40-byte YAML document becomes
   a memory exhaustion (the "billion laughs" shape); the cap is the cheap half
   of that defence and ``yaml.safe_load`` is the other half.
2. **Safe parse.** ``yaml.safe_load``, never ``yaml.load``. The default loader
   constructs arbitrary Python objects from tags such as
   ``!!python/object/apply:os.system`` — with a suite file arriving in a pull
   request, that is remote code execution in CI.
3. **Validate, then resolve graders.** Grader names are checked against the
   registry *at load time*. A suite naming a grader that does not exist must
   fail before a single completion is requested, not after paying for the run.

Both YAML and JSON are accepted; JSON is a subset of YAML 1.2 and
``safe_load`` handles it, so there is one path rather than two.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from aievals.errors import SuiteError
from aievals.suite.models import Suite

#: Suite files are hand-written or generated from a spreadsheet. A megabyte is
#: roughly ten thousand cases of ordinary length, which is already past
#: MAX_CASES.
MAX_SUITE_BYTES = 4 * 1024 * 1024

SUFFIXES = frozenset({".yaml", ".yml", ".json"})


def parse_suite(text: str, *, origin: str = "<string>") -> Suite:
    """Parse and validate a suite from *text*.

    *origin* appears in error messages; it is a label, never opened.
    """
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        # str(exc) from PyYAML already carries line and column.
        raise SuiteError(
            f"{origin} is not valid YAML or JSON: {exc}",
            remedy="Check indentation and quoting around the failing line.",
        ) from exc

    if raw is None:
        raise SuiteError(
            f"{origin} is empty.",
            remedy="A suite needs at least 'name' and one entry under 'cases'.",
        )
    if not isinstance(raw, dict):
        raise SuiteError(
            f"{origin} must be a mapping at the top level, not {type(raw).__name__}.",
            remedy="The document starts with 'name:', not with a list.",
        )

    try:
        suite = Suite.model_validate(raw)
    except ValidationError as exc:
        raise SuiteError(
            f"{origin} is not a valid suite:\n{_render_validation(exc)}",
            remedy="See docs/suites.md for the schema.",
        ) from exc

    _check_graders_exist(suite, origin)
    return suite


def load_suite(path: str | Path) -> Suite:
    """Read, parse and validate the suite file at *path*."""
    file = Path(path)
    if file.suffix.lower() not in SUFFIXES:
        raise SuiteError(
            f"{file.name} does not look like a suite file.",
            remedy=f"Expected one of: {', '.join(sorted(SUFFIXES))}.",
        )
    try:
        size = file.stat().st_size
    except OSError as exc:
        raise SuiteError(
            f"{file} could not be opened: {exc.strerror or exc}.",
            remedy="Check the path and permissions.",
        ) from exc

    if size > MAX_SUITE_BYTES:
        raise SuiteError(
            f"{file.name} is {size} bytes, over the {MAX_SUITE_BYTES}-byte limit.",
            remedy="Split the suite, or raise the limit deliberately in loader.py.",
        )

    try:
        text = file.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise SuiteError(
            f"{file.name} is not valid UTF-8.",
            remedy="Re-save the file as UTF-8 without a byte-order mark.",
        ) from exc
    except OSError as exc:
        raise SuiteError(f"{file} could not be read: {exc.strerror or exc}.") from exc

    return parse_suite(text, origin=file.name)


def _check_graders_exist(suite: Suite, origin: str) -> None:
    """Fail now, with every unknown name, rather than one per run."""
    # Imported here rather than at module scope: the registry imports the suite
    # models to type its own signatures, and a top-level import would close the
    # cycle.
    from aievals.graders import (  # noqa: PLC0415 - see the comment above
        build_grader,
        is_registered,
        registered_names,
    )

    unknown: list[str] = []
    for case in suite.cases:
        for spec in suite.graders_for(case):
            if not is_registered(spec.type) and spec.type not in unknown:
                unknown.append(spec.type)

    if unknown:
        names = ", ".join(sorted(registered_names()))
        raise SuiteError(
            f"{origin} names {len(unknown)} unknown grader(s): {', '.join(sorted(unknown))}.",
            remedy=f"Graders are resolved from a registry, not imported. Known: {names}.",
        )

    # Construct every grader once so a bad parameter — an uncompilable pattern,
    # a negative tolerance — is a load-time error naming the case.
    for case in suite.cases:
        for spec in suite.graders_for(case):
            try:
                build_grader(spec)
            except Exception as exc:
                raise SuiteError(
                    f"{origin}, case {case.id!r}: grader {spec.type!r} is misconfigured: {exc}",
                    remedy="See docs/graders.md for each grader's parameters.",
                ) from exc


def _render_validation(exc: ValidationError) -> str:
    lines: list[str] = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"]) or "<root>"
        lines.append(f"  {location}: {error['msg']}")
    return "\n".join(lines)
