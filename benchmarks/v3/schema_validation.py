"""Small standalone validator for the JSON-Schema subset used by this harness."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


class SchemaValidationError(ValueError):
    """Raised when an instance does not satisfy the checked JSON Schema."""


def _type_matches(value: Any, expected: str) -> bool:
    return {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        "null": value is None,
    }[expected]


def validate_schema(
    instance: Any,
    schema_path: Path,
    *,
    root_schema: dict[str, Any] | None = None,
    location: str = "$",
) -> None:
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    _validate(
        instance,
        schema,
        schema_path.parent,
        root_schema or schema,
        location,
    )


def _resolve(
    reference: str,
    directory: Path,
    root_schema: dict[str, Any],
) -> tuple[dict[str, Any], Path, dict[str, Any]]:
    if reference.startswith("#/"):
        target: Any = root_schema
        for part in reference[2:].split("/"):
            target = target[part.replace("~1", "/").replace("~0", "~")]
        return target, directory, root_schema
    path = (directory / reference).resolve()
    target = json.loads(path.read_text(encoding="utf-8"))
    return target, path.parent, target


def _validate(
    value: Any,
    schema: dict[str, Any],
    directory: Path,
    root_schema: dict[str, Any],
    location: str,
) -> None:
    for subschema in schema.get("allOf", []):
        _validate(value, subschema, directory, root_schema, location)
    if "if" in schema and "then" in schema:
        try:
            _validate(value, schema["if"], directory, root_schema, location)
        except SchemaValidationError:
            pass
        else:
            _validate(value, schema["then"], directory, root_schema, location)
    if "$ref" in schema:
        target, target_directory, target_root = _resolve(
            schema["$ref"], directory, root_schema
        )
        _validate(value, target, target_directory, target_root, location)
        return
    if "oneOf" in schema:
        matches = 0
        for option in schema["oneOf"]:
            try:
                _validate(value, option, directory, root_schema, location)
            except SchemaValidationError:
                continue
            matches += 1
        if matches != 1:
            raise SchemaValidationError(f"{location} does not match exactly one schema")
        return
    if "const" in schema and value != schema["const"]:
        raise SchemaValidationError(f"{location} does not equal its constant")
    if "enum" in schema and value not in schema["enum"]:
        raise SchemaValidationError(f"{location} is not in its enum")
    expected_types = schema.get("type")
    if expected_types is not None:
        allowed = (
            [expected_types] if isinstance(expected_types, str) else expected_types
        )
        if not any(_type_matches(value, expected) for expected in allowed):
            raise SchemaValidationError(f"{location} has the wrong type")
    if isinstance(value, str):
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            raise SchemaValidationError(f"{location} does not match its pattern")
        if len(value) < schema.get("minLength", 0):
            raise SchemaValidationError(f"{location} is too short")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            raise SchemaValidationError(f"{location} is below its minimum")
        if "maximum" in schema and value > schema["maximum"]:
            raise SchemaValidationError(f"{location} exceeds its maximum")
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            raise SchemaValidationError(f"{location} has too few items")
        if schema.get("uniqueItems") and len(
            {json.dumps(item, sort_keys=True) for item in value}
        ) != len(value):
            raise SchemaValidationError(f"{location} contains duplicate items")
        if "items" in schema:
            for index, item in enumerate(value):
                _validate(
                    item,
                    schema["items"],
                    directory,
                    root_schema,
                    f"{location}[{index}]",
                )
    if isinstance(value, dict):
        missing = set(schema.get("required", [])) - set(value)
        if missing:
            raise SchemaValidationError(f"{location} is missing {sorted(missing)}")
        properties = schema.get("properties", {})
        patterns = schema.get("patternProperties", {})
        matched: set[str] = set()
        for key, item in value.items():
            if key in properties:
                matched.add(key)
                _validate(
                    item,
                    properties[key],
                    directory,
                    root_schema,
                    f"{location}.{key}",
                )
            for pattern, subschema in patterns.items():
                if re.search(pattern, key):
                    matched.add(key)
                    _validate(
                        item,
                        subschema,
                        directory,
                        root_schema,
                        f"{location}.{key}",
                    )
        if schema.get("additionalProperties") is False:
            extras = set(value) - matched
            if extras:
                raise SchemaValidationError(
                    f"{location} contains unknown properties {sorted(extras)}"
                )
        elif isinstance(schema.get("additionalProperties"), dict):
            for key in set(value) - matched:
                _validate(
                    value[key],
                    schema["additionalProperties"],
                    directory,
                    root_schema,
                    f"{location}.{key}",
                )
