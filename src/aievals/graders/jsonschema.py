"""A deliberately small JSON Schema validator.

Structured-output evaluation needs "does this response parse as JSON and have
the right shape", and nothing more. A full JSON Schema implementation would
bring ``$ref`` resolution, remote schema fetching and the composition keywords
— which is a dependency, a network surface and a large amount of behaviour this
package would then be claiming to have tested.

The important design choice is the one repo 03 got right and most subset
validators get wrong: **unimplemented keywords are rejected when the schema is
registered, not ignored when a document is checked.** A validator that silently
skips ``oneOf`` reports every document as valid against a schema whose whole
meaning is that keyword, and the suite passes while checking nothing. Refusing
the schema up front converts a silent hole into a load-time error naming the
keyword.
"""

from __future__ import annotations

from typing import Any

#: Keywords this validator implements. Anything else is refused.
SUPPORTED: frozenset[str] = frozenset(
    {
        "type",
        "properties",
        "required",
        "additionalProperties",
        "items",
        "minItems",
        "maxItems",
        "enum",
        "const",
        "minimum",
        "maximum",
        "minLength",
        "maxLength",
        "description",
        "title",
    }
)

#: JSON Schema types mapped to the Python types a parsed document produces.
#: ``bool`` before ``int`` matters: in Python ``True`` is an ``int``, so a
#: naive check reports a boolean as a valid integer.
_TYPES: dict[str, tuple[type, ...]] = {
    "object": (dict,),
    "array": (list,),
    "string": (str,),
    "number": (int, float),
    "integer": (int,),
    "boolean": (bool,),
    "null": (type(None),),
}

#: A schema nested deeper than this is a mistake, and following it is a way to
#: exhaust the stack from a data file.
MAX_DEPTH = 20


class SchemaError(ValueError):
    """The schema itself is unusable."""


def check_schema(schema: Any, *, path: str = "$", depth: int = 0) -> None:
    """Raise :class:`SchemaError` if *schema* uses anything unimplemented."""
    if depth > MAX_DEPTH:
        raise SchemaError(f"{path}: the schema nests deeper than {MAX_DEPTH} levels.")
    if not isinstance(schema, dict):
        raise SchemaError(f"{path}: a schema must be an object, not {type(schema).__name__}.")

    _check_keywords(schema, path)
    _check_type(schema, path)
    _check_members(schema, path, depth)


def _check_keywords(schema: dict[str, Any], path: str) -> None:
    unsupported = sorted(set(schema) - SUPPORTED)
    if unsupported:
        raise SchemaError(
            f"{path}: this validator does not implement {', '.join(unsupported)}. "
            f"Implemented keywords: {', '.join(sorted(SUPPORTED))}. "
            f"Unimplemented keywords are refused rather than ignored, because an "
            f"ignored keyword makes the schema pass documents it was written to reject."
        )


def _check_type(schema: dict[str, Any], path: str) -> None:
    declared = schema.get("type")
    if declared is None:
        return
    names = declared if isinstance(declared, list) else [declared]
    for name in names:
        if name not in _TYPES:
            raise SchemaError(f"{path}: unknown type {name!r}.")


def _check_members(schema: dict[str, Any], path: str, depth: int) -> None:
    properties = schema.get("properties")
    if properties is not None:
        if not isinstance(properties, dict):
            raise SchemaError(f"{path}: 'properties' must be an object.")
        for key, sub in properties.items():
            check_schema(sub, path=f"{path}.{key}", depth=depth + 1)

    items = schema.get("items")
    if items is not None:
        check_schema(items, path=f"{path}[]", depth=depth + 1)

    required = schema.get("required")
    if required is not None and (
        not isinstance(required, list) or not all(isinstance(item, str) for item in required)
    ):
        raise SchemaError(f"{path}: 'required' must be a list of property names.")

    extra = schema.get("additionalProperties")
    if extra is not None and not isinstance(extra, bool):
        raise SchemaError(
            f"{path}: 'additionalProperties' must be true or false here; a schema "
            f"in that position is not implemented."
        )


def validate(document: Any, schema: dict[str, Any], *, path: str = "$") -> list[str]:
    """Return every way *document* fails *schema*, as human-readable strings.

    Every error, not the first: a report listing one problem per run turns
    fixing a structured-output case into a sequence of builds.
    """
    errors: list[str] = []
    _validate(document, schema, path, errors)
    return errors


def _validate(document: Any, schema: dict[str, Any], path: str, errors: list[str]) -> None:
    declared = schema.get("type")
    if declared is not None and not _type_matches(document, declared):
        names = declared if isinstance(declared, list) else [declared]
        errors.append(f"{path}: expected {' or '.join(names)}, got {_name_of(document)}")
        # The remaining keywords assume the type held; checking them now would
        # produce a cascade of errors that all say the same thing.
        return

    if "const" in schema and document != schema["const"]:
        errors.append(f"{path}: expected the constant {schema['const']!r}, got {document!r}")
    if "enum" in schema and document not in schema["enum"]:
        errors.append(f"{path}: {document!r} is not one of {schema['enum']!r}")

    if isinstance(document, str):
        _validate_string(document, schema, path, errors)
    elif isinstance(document, (int, float)) and not isinstance(document, bool):
        _validate_number(document, schema, path, errors)
    elif isinstance(document, list):
        _validate_array(document, schema, path, errors)
    elif isinstance(document, dict):
        _validate_object(document, schema, path, errors)


def _validate_string(document: str, schema: dict[str, Any], path: str, errors: list[str]) -> None:
    minimum = schema.get("minLength")
    maximum = schema.get("maxLength")
    if isinstance(minimum, int) and len(document) < minimum:
        errors.append(f"{path}: {len(document)} characters, minimum {minimum}")
    if isinstance(maximum, int) and len(document) > maximum:
        errors.append(f"{path}: {len(document)} characters, maximum {maximum}")


def _validate_number(document: float, schema: dict[str, Any], path: str, errors: list[str]) -> None:
    minimum = schema.get("minimum")
    maximum = schema.get("maximum")
    if isinstance(minimum, (int, float)) and document < minimum:
        errors.append(f"{path}: {document} is below the minimum {minimum}")
    if isinstance(maximum, (int, float)) and document > maximum:
        errors.append(f"{path}: {document} is above the maximum {maximum}")


def _validate_array(
    document: list[Any], schema: dict[str, Any], path: str, errors: list[str]
) -> None:
    minimum = schema.get("minItems")
    maximum = schema.get("maxItems")
    if isinstance(minimum, int) and len(document) < minimum:
        errors.append(f"{path}: {len(document)} items, minimum {minimum}")
    if isinstance(maximum, int) and len(document) > maximum:
        errors.append(f"{path}: {len(document)} items, maximum {maximum}")
    items = schema.get("items")
    if isinstance(items, dict):
        for index, entry in enumerate(document):
            _validate(entry, items, f"{path}[{index}]", errors)


def _validate_object(
    document: dict[str, Any], schema: dict[str, Any], path: str, errors: list[str]
) -> None:
    properties = schema.get("properties")
    properties = properties if isinstance(properties, dict) else {}
    errors.extend(
        f"{path}: missing required property {name!r}"
        for name in schema.get("required", [])
        if name not in document
    )
    for name, sub in properties.items():
        if name in document:
            _validate(document[name], sub, f"{path}.{name}", errors)
    if schema.get("additionalProperties") is False:
        errors.extend(
            f"{path}: unexpected property {name!r}"
            for name in sorted(set(document) - set(properties))
        )


def _type_matches(document: Any, declared: Any) -> bool:
    names = declared if isinstance(declared, list) else [declared]
    for name in names:
        expected = _TYPES.get(name, ())
        if name != "boolean" and isinstance(document, bool):
            # True is an int in Python; a schema asking for an integer is not
            # asking for a boolean.
            continue
        if isinstance(document, expected):
            return True
    return False


def _name_of(document: Any) -> str:
    for name, types in _TYPES.items():
        if name == "number":
            continue
        if isinstance(document, bool):
            return "boolean"
        if isinstance(document, types):
            return name
    return type(document).__name__
