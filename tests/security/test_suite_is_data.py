"""A suite file is data, and these tests are what keeps it that way.

Suite files arrive in pull requests and run on a CI runner holding repository
credentials. Every case below is an attempt to make one execute something, and
a failure here is a security regression rather than a bug.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from aievals.errors import SuiteError
from aievals.suite import load_suite, parse_suite
from aievals.suite.loader import MAX_SUITE_BYTES

pytestmark = pytest.mark.security


class TestNoCodeExecutionFromYaml:
    @pytest.mark.parametrize(
        "document",
        [
            "name: !!python/object/apply:os.system ['echo pwned']\ncases: []\n",
            "!!python/object/apply:subprocess.check_output [['echo', 'pwned']]\n",
            "name: x\ncases: !!python/object/apply:os.getcwd []\n",
            "name: !!python/name:os.system\ncases: []\n",
        ],
    )
    def test_python_object_tags_are_refused(self, document: str):
        # yaml.safe_load, never yaml.load. The default loader constructs
        # arbitrary Python objects from these tags.
        with pytest.raises(SuiteError):
            parse_suite(document)

    def test_the_loader_used_is_the_safe_one(self):
        import inspect

        from aievals.suite import loader

        source = inspect.getsource(loader)
        assert "yaml.safe_load" in source
        assert "yaml.load(" not in source
        assert "yaml.unsafe_load" not in source


class TestNoCodeExecutionFromGraderNames:
    @pytest.mark.parametrize(
        "name",
        [
            "os.system",
            "mypackage.checks:looks_right",
            "builtins.eval",
            "../../etc/passwd",
            "contains_all; rm -rf /",
        ],
    )
    def test_an_import_path_is_not_a_grader_name(self, name: str):
        # The convenient design is `grader: mypkg.checks:fn` plus importlib,
        # which is arbitrary code execution from a data file.
        document = textwrap.dedent(
            f"""
            name: x
            cases:
              - id: a
                prompt: p
                graders:
                  - type: {name!r}
            """
        )
        with pytest.raises(SuiteError):
            parse_suite(document)

    def test_the_registry_does_not_import_anything(self):
        import inspect

        from aievals.graders import registry

        source = inspect.getsource(registry)
        assert "importlib" not in source
        assert "__import__" not in source
        assert "eval(" not in source

    def test_a_grader_name_that_is_merely_unknown_is_a_plain_error(self):
        document = "name: x\ncases:\n  - {id: a, prompt: p, graders: [{type: nope}]}\n"
        with pytest.raises(SuiteError, match="unknown grader"):
            parse_suite(document)


class TestResourceBounds:
    def test_an_oversized_file_is_refused_before_it_is_parsed(self, tmp_path: Path):
        # Handing an unbounded file to a parser is how a small document becomes
        # a memory exhaustion.
        path = tmp_path / "big.yaml"
        path.write_text("#" * (MAX_SUITE_BYTES + 1), encoding="utf-8")
        with pytest.raises(SuiteError, match="over the"):
            load_suite(path)

    def test_an_alias_bomb_does_not_expand_without_bound(self):
        # The "billion laughs" shape. safe_load still expands aliases, so the
        # protection is that the *model* rejects the result rather than the
        # parser accepting an enormous structure.
        document = textwrap.dedent(
            """
            a: &a ["x", "x", "x", "x", "x", "x", "x", "x", "x"]
            b: &b [*a, *a, *a, *a, *a, *a, *a, *a, *a]
            c: &c [*b, *b, *b, *b, *b, *b, *b, *b, *b]
            name: bomb
            cases: *c
            """
        )
        with pytest.raises(SuiteError):
            parse_suite(document)

    def test_a_suite_over_the_case_limit_is_refused(self):
        from aievals.suite.models import MAX_CASES

        cases = "\n".join(f"  - {{id: c{index}, prompt: p}}" for index in range(MAX_CASES + 1))
        with pytest.raises(SuiteError, match="at most"):
            parse_suite(f"name: many\ncases:\n{cases}\n")

    def test_a_suite_with_no_cases_is_refused_rather_than_passing_everything(self):
        with pytest.raises(SuiteError, match="trivially"):
            parse_suite("name: empty\ncases: []\n")


class TestNoPathTraversal:
    def test_the_origin_label_is_never_opened(self):
        # parse_suite takes text; the label is for error messages only.
        suite = parse_suite("name: x\ncases:\n  - {id: a, prompt: p}\n", origin="../../etc/passwd")
        assert suite.name == "x"

    def test_a_directory_is_not_a_suite_file(self, tmp_path: Path):
        directory = tmp_path / "notafile.yaml"
        directory.mkdir()
        with pytest.raises(SuiteError):
            load_suite(directory)
