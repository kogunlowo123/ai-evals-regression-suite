"""The mutators themselves, and their applicability rules.

Applicability is the part worth testing hardest. A mutator that claims to apply
to prose it cannot change produces a survivor that blames the suite for the
mutator's own no-op, and a mutation score nobody believes is a mutation score
nobody acts on.
"""

from __future__ import annotations

import json
import random

import pytest

from aievals.mutation.mutators import MUTATORS, mutator_names, select

pytestmark = pytest.mark.unit

RNG = random.Random(1)
PROSE = (
    "You can request a refund within 30 days of purchase. After that we offer "
    "store credit. A supervisor can review anything unusual."
)
DOCUMENT = '{"category": "billing", "severity": 3}'


def apply(name: str, text: str) -> str:
    mutator = next(m for m in MUTATORS if m.name == name)
    return mutator.apply(text, random.Random(7))


class TestRegistry:
    def test_every_mutator_has_a_distinct_name(self):
        assert len(mutator_names()) == len(MUTATORS)

    def test_every_mutator_states_what_it_does_and_what_to_expect(self):
        for mutator in MUTATORS:
            assert mutator.description
            assert mutator.expectation

    def test_the_core_set_is_a_subset_of_all(self):
        assert set(select(mutator_set="core")) <= set(select(mutator_set="all"))

    def test_core_and_extended_partition_the_whole_set(self):
        combined = set(select(mutator_set="core")) | set(select(mutator_set="extended"))
        assert combined == set(MUTATORS)

    def test_naming_mutators_selects_exactly_those(self):
        chosen = select(("empty", "refusal"))
        assert [m.name for m in chosen] == ["empty", "refusal"]

    def test_an_unknown_name_is_refused_with_the_known_ones(self):
        with pytest.raises(KeyError, match="Known:"):
            select(("nonsense",))


class TestApplicability:
    def test_empty_and_refusal_always_apply(self):
        for name in ("empty", "refusal"):
            mutator = next(m for m in MUTATORS if m.name == name)
            assert mutator.applicable("")
            assert mutator.applicable(PROSE)

    def test_truncate_needs_something_to_truncate(self):
        mutator = next(m for m in MUTATORS if m.name == "truncate")
        assert not mutator.applicable("short")
        assert mutator.applicable(PROSE)

    def test_drop_numbers_needs_a_digit(self):
        mutator = next(m for m in MUTATORS if m.name == "drop_numbers")
        assert not mutator.applicable("no digits here")
        assert mutator.applicable(PROSE)

    def test_corrupt_json_needs_valid_json(self):
        mutator = next(m for m in MUTATORS if m.name == "corrupt_json")
        assert not mutator.applicable(PROSE)
        assert not mutator.applicable("{not json}")
        assert mutator.applicable(DOCUMENT)

    def test_shuffle_sentences_needs_several_sentences(self):
        mutator = next(m for m in MUTATORS if m.name == "shuffle_sentences")
        assert not mutator.applicable("one sentence only")
        assert mutator.applicable(PROSE)


class TestMutations:
    def test_empty_produces_an_empty_string(self):
        assert apply("empty", PROSE) == ""

    def test_refusal_produces_no_answer(self):
        result = apply("refusal", PROSE)
        assert "refund" not in result
        assert "sorry" in result.lower()

    def test_truncate_keeps_roughly_a_third(self):
        result = apply("truncate", PROSE)
        assert 0 < len(result) < len(PROSE) * 0.5
        assert PROSE.startswith(result)

    def test_drop_numbers_removes_every_digit(self):
        result = apply("drop_numbers", PROSE)
        assert not any(character.isdigit() for character in result)

    def test_corrupt_json_makes_the_document_unparseable(self):
        result = apply("corrupt_json", DOCUMENT)
        with pytest.raises(json.JSONDecodeError):
            json.loads(result)

    def test_swap_numbers_changes_a_figure(self):
        result = apply("swap_numbers", PROSE)
        assert result != PROSE
        assert "30 days" not in result

    def test_negate_flips_an_assertion(self):
        assert apply("negate", "The window is 30 days.") == "The window is not 30 days."

    def test_negate_falls_back_when_nothing_matches(self):
        assert apply("negate", "Refunds within 30 days.").startswith("It is not the case")

    def test_boilerplate_is_prefixed(self):
        assert apply("boilerplate", PROSE).startswith("As an AI language model")

    def test_inject_instruction_appends_an_instruction(self):
        assert "IGNORE ALL PREVIOUS INSTRUCTIONS" in apply("inject_instruction", PROSE)

    def test_strip_formatting_removes_fences_and_markers(self):
        text = "# Heading\n```json\n{}\n```\n- one\n2. two"
        result = apply("strip_formatting", text)
        assert "```" not in result
        assert "# " not in result
        assert "- " not in result

    def test_shuffle_sentences_actually_changes_the_order(self):
        # A shuffle that lands on the original order is a mutant identical to
        # the response, which every suite trivially "survives".
        for seed in range(20):
            mutator = next(m for m in MUTATORS if m.name == "shuffle_sentences")
            assert mutator.apply(PROSE, random.Random(seed)) != PROSE

    def test_mutations_are_deterministic_for_a_given_seed(self):
        mutator = next(m for m in MUTATORS if m.name == "swap_numbers")
        assert mutator.apply(PROSE, random.Random(3)) == mutator.apply(PROSE, random.Random(3))
