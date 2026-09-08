"""The subset JSON Schema validator.

The most important tests here are the refusals. A validator that ignores a
keyword it does not implement reports every document as valid against a schema
whose whole meaning is that keyword, and the suite using it goes green while
checking nothing.
"""

from __future__ import annotations

from typing import Any

import pytest

from aievals.graders.jsonschema import SUPPORTED, SchemaError, check_schema, validate

pytestmark = pytest.mark.unit


class TestSchemaAcceptance:
    def test_an_empty_schema_is_valid_and_accepts_anything(self):
        check_schema({})
        assert validate({"anything": 1}, {}) == []

    @pytest.mark.parametrize("keyword", sorted(SUPPORTED))
    def test_every_supported_keyword_is_accepted_in_a_schema(self, keyword: str):
        values = {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
            "items": {"type": "string"},
            "minItems": 0,
            "maxItems": 1,
            "enum": [1],
            "const": 1,
            "minimum": 0,
            "maximum": 1,
            "minLength": 0,
            "maxLength": 1,
            "description": "x",
            "title": "x",
        }
        check_schema({keyword: values[keyword]})

    @pytest.mark.parametrize(
        "keyword",
        ["oneOf", "anyOf", "allOf", "not", "$ref", "patternProperties", "if", "format"],
    )
    def test_an_unimplemented_keyword_is_refused_rather_than_ignored(self, keyword: str):
        with pytest.raises(SchemaError, match=keyword.replace("$", r"\$")):
            check_schema({keyword: {}})

    def test_the_refusal_explains_why_it_is_a_refusal(self):
        with pytest.raises(SchemaError, match="ignored keyword"):
            check_schema({"oneOf": []})

    def test_an_unknown_type_is_refused(self):
        with pytest.raises(SchemaError, match="unknown type"):
            check_schema({"type": "date"})

    def test_a_non_object_schema_is_refused(self):
        with pytest.raises(SchemaError, match="must be an object"):
            check_schema(["not", "a", "schema"])

    def test_deep_nesting_is_refused(self):
        schema: dict[str, Any] = {"type": "object"}
        for _ in range(30):
            schema = {"type": "object", "properties": {"child": schema}}
        with pytest.raises(SchemaError, match="nests deeper"):
            check_schema(schema)

    def test_a_bad_required_list_is_refused(self):
        with pytest.raises(SchemaError, match="list of property names"):
            check_schema({"required": "name"})

    def test_a_schema_valued_additional_properties_is_refused(self):
        with pytest.raises(SchemaError, match="not implemented"):
            check_schema({"additionalProperties": {"type": "string"}})

    def test_a_nested_property_schema_is_checked(self):
        with pytest.raises(SchemaError, match=r"\$\.child"):
            check_schema({"properties": {"child": {"oneOf": []}}})


class TestValidation:
    def test_a_matching_object_produces_no_errors(self):
        schema = {"type": "object", "required": ["a"], "properties": {"a": {"type": "string"}}}
        assert validate({"a": "x"}, schema) == []

    def test_a_type_mismatch_is_reported_once_not_as_a_cascade(self):
        schema = {"type": "object", "required": ["a", "b"], "properties": {}}
        errors = validate("a string", schema)
        assert len(errors) == 1
        assert "expected object" in errors[0]

    def test_a_boolean_is_not_an_integer(self):
        # True is an int in Python, and a schema asking for an integer is not
        # asking for a boolean.
        assert validate(True, {"type": "integer"}) != []

    def test_an_integer_is_a_number(self):
        assert validate(3, {"type": "number"}) == []

    def test_a_union_type_accepts_either(self):
        schema = {"type": ["string", "null"]}
        assert validate("x", schema) == []
        assert validate(None, schema) == []
        assert validate(1, schema) != []

    def test_string_length_bounds_are_enforced(self):
        assert validate("abc", {"type": "string", "minLength": 4}) != []
        assert validate("abcde", {"type": "string", "maxLength": 4}) != []

    def test_numeric_bounds_are_enforced(self):
        assert validate(0, {"type": "integer", "minimum": 1}) != []
        assert validate(9, {"type": "integer", "maximum": 5}) != []

    def test_array_bounds_and_item_schema_are_enforced(self):
        schema = {"type": "array", "items": {"type": "string"}, "minItems": 2}
        assert validate(["a"], schema) != []
        assert validate(["a", 1], schema) != []
        assert validate(["a", "b"], schema) == []

    def test_const_and_enum_are_enforced(self):
        assert validate("b", {"const": "a"}) != []
        assert validate("c", {"enum": ["a", "b"]}) != []
        assert validate("a", {"enum": ["a", "b"]}) == []

    def test_every_failure_is_reported_not_just_the_first(self):
        schema = {
            "type": "object",
            "required": ["a", "b", "c"],
            "properties": {},
        }
        assert len(validate({}, schema)) == 3

    def test_error_paths_name_the_offending_field(self):
        schema = {"properties": {"outer": {"properties": {"inner": {"type": "integer"}}}}}
        errors = validate({"outer": {"inner": "x"}}, schema)
        assert errors[0].startswith("$.outer.inner:")

    def test_array_error_paths_carry_the_index(self):
        errors = validate(["a", 1], {"type": "array", "items": {"type": "string"}})
        assert "[1]" in errors[0]
