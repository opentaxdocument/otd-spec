#!/usr/bin/env python
"""Exact numeric primitives shared by the bounded OTD reference tools.

YAML decimal tokens are constructed directly as Decimal, never via float.
Programmatic float callers remain supported through their decimal string
representation; precision already lost upstream cannot be recovered here.
"""
from decimal import Decimal, InvalidOperation, localcontext

from ruamel.yaml import YAML
from ruamel.yaml.constructor import SafeConstructor, RoundTripConstructor
from ruamel.yaml.representer import SafeRepresenter, RoundTripRepresenter


MAX_NUMERIC_DIGITS = 1000
MAX_NUMERIC_EXPONENT = 1000


def as_decimal(value):
    """Return a finite, bounded Decimal without silently rounding its value."""
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ValueError("expected a finite number, not %s" % type(value).__name__)
    number = value if isinstance(value, Decimal) else Decimal(str(value))
    if not number.is_finite():
        raise ValueError("non-finite numbers are not financial values")
    parts = number.as_tuple()
    if (
        len(parts.digits) > MAX_NUMERIC_DIGITS
        or abs(parts.exponent) > MAX_NUMERIC_EXPONENT
    ):
        raise ValueError("number exceeds the reference implementation's numeric limits")
    return number


def is_numeric(value):
    try:
        as_decimal(value)
        return True
    except (ValueError, InvalidOperation, OverflowError):
        return False


def _precision(numbers):
    return max(
        28,
        sum(len(number.as_tuple().digits) + abs(number.as_tuple().exponent)
            for number in numbers) + 8,
    )


def decimal_sum(values):
    numbers = [as_decimal(value) for value in values]
    with localcontext() as context:
        context.prec = _precision(numbers)
        return sum(numbers, Decimal(0))


def decimal_delta(left, right):
    numbers = [as_decimal(left), as_decimal(right)]
    with localcontext() as context:
        context.prec = _precision(numbers)
        return abs(numbers[0] - numbers[1])


def percentage_fraction(value):
    """Convert printed percentage points to a fraction with an exact scale shift."""
    number = as_decimal(value)
    sign, digits, exponent = number.as_tuple()
    return as_decimal(Decimal((sign, digits, exponent - 2)))


def currency_value(value):
    """Format an exact currency value to cents; never invent a rounding policy."""
    if value is None:
        return None
    number = as_decimal(value)
    with localcontext() as context:
        context.prec = _precision([number])
        cents = number.quantize(Decimal("0.01"))
    if cents != number:
        raise ValueError("currency has fractional cents; explicit rounding is required")
    return cents


class DecimalConstructor(SafeConstructor):
    pass


def _construct_decimal(constructor, node):
    token = constructor.construct_scalar(node).replace("_", "")
    special = {
        ".inf": "Infinity", "+.inf": "Infinity", "-.inf": "-Infinity",
        ".nan": "NaN",
    }
    # Preserve non-finite tokens at parsing time so validation, not a hidden
    # coercion, rejects them. No objects or executable YAML tags are enabled.
    return Decimal(special.get(token.lower(), token))


DecimalConstructor.add_constructor(
    "tag:yaml.org,2002:float", _construct_decimal
)


class DecimalRepresenter(SafeRepresenter):
    pass


def _represent_decimal(representer, value):
    number = as_decimal(value)
    token = format(number, "f")
    if "." not in token:
        token += ".0"
    return representer.represent_scalar("tag:yaml.org,2002:float", token)


DecimalRepresenter.add_representer(Decimal, _represent_decimal)


class DecimalRoundTripConstructor(RoundTripConstructor):
    pass


DecimalRoundTripConstructor.add_constructor(
    "tag:yaml.org,2002:float", _construct_decimal
)


class DecimalRoundTripRepresenter(RoundTripRepresenter):
    pass


DecimalRoundTripRepresenter.add_representer(Decimal, _represent_decimal)


def decimal_yaml(round_trip=False):
    """Create a private safe YAML codec; do not modify global YAML behavior."""
    codec = YAML(typ="rt" if round_trip else "safe", pure=True)
    codec.Constructor = DecimalRoundTripConstructor if round_trip else DecimalConstructor
    codec.Representer = DecimalRoundTripRepresenter if round_trip else DecimalRepresenter
    codec.default_flow_style = False
    codec.sort_base_mapping_type_on_output = False
    codec.indent(mapping=2, sequence=4, offset=2)
    codec.width = 4096
    return codec


def normalize_k1_numbers(document, taxonomy):
    """Normalize declared K-1 amounts without guessing unknown field semantics.

    Physical K-1 decimal values are monetary; percentage declarations retain
    their full precision. Attached schemas may also contain quantities/rates,
    so those decimals are exact but are quantized only with explicit currency
    metadata. Unknown fields are preserved unchanged, not inferred as money.
    This is a bounded K-1 emission helper, not a general taxonomy interpreter.
    """
    identity = taxonomy.get("taxonomy", {})
    body = document.get("body", {})
    if identity.get("form_id") != "k1-1065" or body.get("form_id") != "k1-1065":
        raise ValueError("numeric emission helper supports declared k1-1065 documents only")

    def payload(value, declaration, monetary=False):
        kind = declaration.get("value_type", declaration.get("type"))
        if value is None:
            return value
        if kind in ("decimal", "percentage"):
            if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
                money = kind == "decimal" and (monetary or declaration.get("currency"))
                return currency_value(value) if money else as_decimal(value)
            return value
        fields = declaration.get("fields")
        if isinstance(value, dict) and isinstance(fields, dict):
            for key, field in fields.items():
                if key in value and isinstance(field, dict):
                    value[key] = payload(value[key], field, monetary)
        return value

    def attached(statement, declaration):
        if not isinstance(statement, dict):
            return
        schema = declaration.get("statement_schema")
        if not isinstance(schema, dict):
            return
        fields = schema.get("record_schema", schema.get("fields", {}))
        if isinstance(fields, list):
            fields = {
                field["field"]: field for field in fields
                if isinstance(field, dict) and isinstance(field.get("field"), str)
            }
        if not isinstance(fields, dict):
            return
        content = statement.get("content")
        if isinstance(content, dict) and isinstance(content.get("records"), list):
            records = content["records"]
        else:
            records = content if isinstance(content, list) else [content]
        for record in records:
            if isinstance(record, dict):
                for key, field in fields.items():
                    if key in record and isinstance(field, dict):
                        record[key] = payload(record[key], field)

    for part, group in taxonomy.get("nodes", {}).items():
        if not isinstance(group, dict) or not isinstance(body.get(part), dict):
            continue
        for key, declaration in group.get("children", {}).items():
            node = body[part].get(key)
            if not isinstance(declaration, dict) or not isinstance(node, dict):
                continue
            if declaration.get("type") == "scalar" and "value" in node:
                node["value"] = payload(node["value"], declaration, monetary=True)
            codes = declaration.get("codes")
            if isinstance(codes, dict) and isinstance(node.get("entries"), list):
                for entry in node["entries"]:
                    if not isinstance(entry, dict):
                        continue
                    code = codes.get(str(entry.get("code", "")).upper())
                    if not isinstance(code, dict):
                        continue
                    if "value" in entry:
                        entry["value"] = payload(entry["value"], code, monetary=True)
                    attached(entry.get("statement"), code)
            attached(node.get("statement"), declaration)
    return document


def tree_problem(value, max_depth=100, max_nodes=200000):
    """Reject cycles and excessive expanded trees before recursive validators.

    Shared acyclic aliases are permitted. The node budget counts their expanded
    occurrences because the validators traverse the expanded document.
    """
    active = set()
    visited = 0

    def visit(node, depth):
        nonlocal visited
        visited += 1
        if visited > max_nodes:
            return "document exceeds the expanded node limit"
        if depth > max_depth:
            return "document exceeds the nesting depth limit"
        if not isinstance(node, (dict, list)):
            return None
        identity = id(node)
        if identity in active:
            return "cyclic YAML aliases are not a document tree"
        active.add(identity)
        try:
            children = node.values() if isinstance(node, dict) else node
            for child in children:
                problem = visit(child, depth + 1)
                if problem:
                    return problem
        finally:
            active.remove(identity)
        return None

    return visit(value, 0)
