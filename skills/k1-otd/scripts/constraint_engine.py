#!/usr/bin/env python
"""K-1 OTD Extraction Skill — Taxonomy-Driven Constraint Engine

Reads validation rules directly from the taxonomy YAML (the `constraints:`
list, plus node-level `statement_classification` / `statement_schema`
declarations anywhere in the node-schema tree) and enforces them
generically against an assembled OTD document body.

Design intent: a new conditional-attachment or arithmetic rule added to the
taxonomy YAML is enforced automatically the next time this engine runs, with
no new hand-coded Python `if` block required. Two prior adversarial reviews
found that per-field hardcoded checks kept leaving a "presence-only" gap
(field exists, but content/classification was never validated) each time a
new field was patched individually. This module exists to close that
pattern structurally rather than field-by-field.
"""
from __future__ import annotations
import re

_BOX_RE = re.compile(r"^box_\d+[a-z]?$")
_ITEM_RE = re.compile(r"^item_[a-z0-9]+$")


# ---------------------------------------------------------------------------
# Path resolution against the assembled document body
# ---------------------------------------------------------------------------

def resolve_path(body, path):
    """Resolve a dotted taxonomy-style path against an assembled OTD body.

    Supported idioms (all present in the shipped taxonomy):
      part_iii.box_4c                       -> scalar node -> .value
      part_ii.item_j.*                      -> wildcard over scalar .value dict
      part_ii.item_l.beginning              -> field inside scalar .value dict
      part_iii.box_20.X.statement           -> coded entry (lookup by code)
      part_iii.box_20.ZZ.semantic.classification -> nested field on entry
      part_iii.box_16.checked               -> direct field on a node

    Returns (found: bool, value, is_wildcard: bool).
    """
    parts = path.split(".")
    if not parts:
        return False, None, False
    node = body.get(parts[0])
    if node is None:
        return False, None, False
    idx = 1
    while idx < len(parts):
        part = parts[idx]
        if part == "*":
            val = node.get("value") if isinstance(node, dict) else None
            return True, val, True
        if not isinstance(node, dict):
            return False, None, False
        if "entries" in node and isinstance(node.get("entries"), list):
            entry = next(
                (e for e in node["entries"]
                 if isinstance(e, dict) and str(e.get("code", "")).upper() == part.upper()),
                None,
            )
            if entry is None:
                return False, None, False
            node = entry
            idx += 1
            continue
        if part in node:
            node = node[part]
            idx += 1
            continue
        val = node.get("value")
        if isinstance(val, dict) and part in val:
            node = val[part]
            idx += 1
            continue
        return False, None, False
    return True, node, False


_COMPARATORS = {
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
    ">=": lambda a, b: (a is not None and b is not None and a >= b),
    "<=": lambda a, b: (a is not None and b is not None and a <= b),
    ">":  lambda a, b: (a is not None and b is not None and a > b),
    "<":  lambda a, b: (a is not None and b is not None and a < b),
}


def _literal(token):
    token = token.strip()
    low = token.lower()
    if low == "true":
        return True
    if low == "false":
        return False
    if low in ("null", "none"):
        return None
    if (token.startswith("'") and token.endswith("'")) or (token.startswith('"') and token.endswith('"')):
        return token[1:-1]
    try:
        return float(token) if "." in token else int(token)
    except ValueError:
        return token


def evaluate_condition(body, condition):
    """Evaluate a simple `path OP literal` condition string against body.
    Returns True/False, or None if the path cannot be resolved (inapplicable,
    not silently satisfied)."""
    for op in ("==", "!=", ">=", "<=", ">", "<"):
        if op in condition:
            lhs, rhs = condition.split(op, 1)
            found, value, _ = resolve_path(body, lhs.strip())
            if not found:
                return None
            return _COMPARATORS[op](value, _literal(rhs))
    return None


class RangeRuleError(Exception):
    """A range rule could not be evaluated.

    Never swallowed. A prior adversarial review found that
    `_eval_range_rule()` returned True on any exception, so a corrupted
    expression silently certified a violating document. An unevaluable
    rule is now a validation failure in its own right.
    """



class RangeRuleSkip(Exception):
    """A range rule references a path that is absent from the document.

    otd-parser-spec.md 4.7 separates absent from null: an absent operand
    skips a range constraint, while a null operand is arithmetically 0.00.
    Collapsing the two into a literal `None` injected NoneType into the
    comparison and raised a spurious evaluation error on every document
    where the referenced box was simply never extracted.

    Skipping here does not weaken the contract. Presence of a declared node
    is already a hard error under validate_physical_completeness(); this
    algebra only does arithmetic.
    """


def _unwrap_value(val):
    """Return the underlying scalar whether a path resolved to a literal or
    to a whole TaxNode.

    Taxonomy authors write both `part_iii.box_4c` and
    `part_iii.box_4c.value` for the same quantity. The bare form resolved
    to the node dictionary, failed the numeric guard, and caused the whole
    constraint to be skipped -- which is why the shipped
    `box_4c_equals_4a_plus_4b` sum rule had never executed on any document.
    """
    if isinstance(val, dict) and "value" in val:
        return val.get("value")
    return val


# Containers that hold a TaxNode's parts rather than naming an entity.
# A required_field target like `part_iii.box_20.ZZ.semantic.classification`
# qualifies the ZZ entry, not the `semantic` sub-dictionary, so these are
# stripped when locating the node the constraint actually applies to.
_STRUCTURAL_KEYS = {"semantic", "form", "value"}


def _numeric(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _eval_range_rule(rule, value, body):
    """Evaluate a restricted range rule, e.g. 'value >= 0 and value <= 1'
    or 'value <= part_iii.box_6a.value'.

    Substitution order matters. Dotted path references are resolved FIRST,
    because substituting the bare `value` token first also rewrites the
    `value` suffix inside a right-hand-side path -- turning
    `value <= part_iii.box_6a.value` into `500.0 <= part_iii.box_6a.500.0`,
    which cannot be parsed. Paired with a bare `except: return True`, that
    let `box_6b_lte_6a` certify documents where qualified dividends
    exceeded ordinary dividends.

    The path itself is now resolved whole. Previously `.value` was stripped
    before resolution, which returned the enclosing TaxNode dictionary
    rather than the number -- a second, independently fatal defect in the
    same three lines.
    """
    def _sub(match):
        path = match.group(0)
        found, val, _ = resolve_path(body, path)
        if not found:
            # 4.7: an absent operand skips the constraint rather than
            # failing it. Previously this emitted the literal "None", so
            # `float <= None` raised and reported a rule-evaluation error
            # on any document where the referenced box was not extracted.
            raise RangeRuleSkip(path)
        val = _unwrap_value(val)
        if val is None:
            # 4.7: a null operand is arithmetically 0.00.
            val = 0.0
        if not _numeric(val):
            raise RangeRuleError(f"{rule!r}: {path} is not numeric ({val!r})")
        return repr(val)

    expr = re.sub(r"[A-Za-z_][A-Za-z0-9_.]*\.value", _sub, rule)
    expr = re.sub(r"\bvalue\b", repr(value), expr)
    try:
        return bool(eval(expr, {"__builtins__": {}}, {}))
    except Exception as exc:
        raise RangeRuleError(f"{rule!r}: {exc}") from exc


# ---------------------------------------------------------------------------
# Execute the taxonomy's declared `constraints:` list
# ---------------------------------------------------------------------------

def run_constraints(body, constraints):
    """Execute every constraint declared in the taxonomy's `constraints:`
    list against the assembled document body. Returns a list of
    (severity, constraint_id, message) tuples."""
    findings = []
    for c in constraints or []:
        cid = c.get("id", "unknown_constraint")
        ctype = c.get("type")
        severity = c.get("severity", "error")

        if ctype == "sum":
            target = c.get("target")
            operands = c.get("operands", [])
            tolerance = c.get("tolerance", 0.0)
            t_found, t_val, _ = resolve_path(body, target)
            t_val = _unwrap_value(t_val) if t_found else None
            # otd-parser-spec.md 4.7: a null target is arithmetically 0.00.
            # Absence of a declared node is already a hard error under
            # validate_physical_completeness(), so the algebra here does not
            # also police presence -- it only does arithmetic.
            if t_found and t_val is None:
                t_val = 0.0
            if not t_found or not _numeric(t_val):
                continue
            op_values = []
            for op_path in operands:
                found, val, _ = resolve_path(body, op_path)
                val = _unwrap_value(val) if found else None
                # 4.7: absent and null operands each contribute 0.00, so a
                # partial K-1 does not raise spurious arithmetic failures.
                op_values.append(val if _numeric(val) else 0.0)
            computed = sum(op_values)
            delta = abs(computed - t_val)
            if delta > tolerance:
                findings.append((severity, cid,
                    f"{cid}: {target} ({t_val}) does not equal sum of operands "
                    f"({computed}); delta ${delta:,.2f} exceeds tolerance {tolerance}"))

        elif ctype == "range":
            target = c.get("target")
            rule = c.get("rule", "")
            if target.endswith(".*"):
                base_path = target[:-2]
                found, val, _ = resolve_path(body, target)
                if not found or not isinstance(val, dict):
                    continue
                for key, sub_val in val.items():
                    if sub_val is None:
                        sub_val = 0.0
                    if not _numeric(sub_val):
                        continue
                    try:
                        ok = _eval_range_rule(rule, sub_val, body)
                    except RangeRuleSkip:
                        continue
                    except RangeRuleError as exc:
                        findings.append(("error", cid,
                            f"{cid}: range rule could not be evaluated for "
                            f"{base_path}.{key}: {exc}"))
                        continue
                    if not ok:
                        findings.append((severity, cid,
                            f"{cid}: {base_path}.{key} = {sub_val} violates rule '{rule}'"))
            else:
                found, val, _ = resolve_path(body, target)
                val = _unwrap_value(val) if found else None
                # 4.7: an absent target skips a range constraint entirely; a
                # null target is evaluated as 0.00 and may legitimately fail.
                if not found:
                    continue
                if val is None:
                    val = 0.0
                if not _numeric(val):
                    continue
                try:
                    ok = _eval_range_rule(rule, val, body)
                except RangeRuleSkip:
                    continue
                except RangeRuleError as exc:
                    findings.append(("error", cid,
                        f"{cid}: range rule could not be evaluated: {exc}"))
                    continue
                if not ok:
                    findings.append((severity, cid, f"{cid}: {target} = {val} violates rule '{rule}'"))

        elif ctype == "required_field":
            target = c.get("target")
            found, val, _ = resolve_path(body, target)
            val = _unwrap_value(val) if found else None
            # The requirement applies only when the entity it qualifies is
            # present. Resolving the literal parent path was wrong: for
            # `part_iii.box_20.ZZ.semantic.classification` the parent is
            # `...ZZ.semantic`, so an entry that omitted `semantic` entirely
            # made the parent unresolvable and the classification requirement
            # was silently skipped -- precisely the entry most likely to be
            # malformed. Strip TaxNode structural containers to reach the
            # entity itself. A taxonomy may also name the anchor explicitly.
            anchor = c.get("applies_when_present")
            if not anchor:
                segs = target.split(".")[:-1]
                while segs and segs[-1] in _STRUCTURAL_KEYS:
                    segs = segs[:-1]
                anchor = ".".join(segs)
            anchor_found = bool(anchor) and resolve_path(body, anchor)[0]
            if anchor_found and (not found or val in (None, "")):
                findings.append((severity, cid, f"{cid}: required field {target} is missing"))

        elif ctype == "conditional_required":
            condition = c.get("condition", "")
            if evaluate_condition(body, condition) is not True:
                continue
            target = c.get("target")
            found, val, _ = resolve_path(body, target)
            if not found or val in (None, {}, []):
                findings.append((severity, cid,
                    f"{cid}: condition '{condition}' is true but required {target} is missing"))

    return findings


# ---------------------------------------------------------------------------
# Statement-classification / statement-schema enforcement
# ---------------------------------------------------------------------------

def extract_statement_requirements(taxonomy):
    """Walk the taxonomy's node-schema tree and collect every declared
    statement requirement (item-level `statement_classification` with
    `statement_required_when`, or code-level `requires_statement`).

    Output path derivation uses the box_NN/codes/CODE and item_X naming
    convention already used throughout the shipped taxonomy, so this
    generalizes to any future field using the same convention without
    code changes."""
    root = taxonomy.get("nodes", taxonomy)
    requirements = []

    def derive_output_path(part_root, key_chain):
        box, code = None, None
        for i, seg in enumerate(key_chain):
            if _BOX_RE.match(str(seg)):
                box = seg
            if seg == "codes" and i + 1 < len(key_chain):
                code = key_chain[i + 1]
        if box and code:
            return f"{part_root}.{box}.{code}"
        for seg in reversed(key_chain):
            if _ITEM_RE.match(str(seg)):
                return f"{part_root}.{seg}"
        return None

    def walk(node, part_root, key_chain):
        if isinstance(node, dict):
            if "statement_classification" in node:
                out_path = derive_output_path(part_root, key_chain)
                if out_path:
                    requirements.append({
                        "output_path": out_path,
                        "classification": node.get("statement_classification"),
                        "required_when": node.get("statement_required_when"),
                        "requires_statement": bool(node.get("requires_statement")),
                        "may_attach_statement": bool(node.get("may_attach_statement")),
                        "schema": (node.get("statement_schema") or {}).get("record_schema"),
                    })
            for k, v in node.items():
                walk(v, part_root, key_chain + [k])
        elif isinstance(node, list):
            for item in node:
                walk(item, part_root, key_chain)

    for part_root in ("part_i", "part_ii", "part_iii"):
        part_node = root.get(part_root)
        if part_node is not None:
            walk(part_node, part_root, [])
    return requirements


def validate_statement_requirements(body, requirements):
    """Structurally validate every attached statement against the taxonomy
    requirement that governs it: presence, role, classification, and (when
    declared) required record_schema fields. This is the generic mechanism
    that replaces field-by-field hardcoded presence checks."""
    findings = []
    for req in requirements:
        out_path = req["output_path"]
        found, target_val, _ = resolve_path(body, out_path)
        if not found or not isinstance(target_val, dict):
            continue

        stmt = target_val.get("statement")
        required_when = req.get("required_when")
        node_val = target_val.get("value")
        if required_when:
            must_have_statement = (node_val is True) if required_when.strip() == "value == true" \
                else bool(evaluate_condition(body, required_when))
        else:
            must_have_statement = req.get("requires_statement", False)

        if must_have_statement and not stmt:
            findings.append(("error", f"statement_required:{out_path}",
                f"{out_path} requires an attached statement (classification "
                f"'{req['classification']}') but none was found"))
            continue
        if not stmt:
            continue

        if not isinstance(stmt, dict):
            findings.append(("error", f"statement_shape:{out_path}",
                f"{out_path} statement is not a structured object"))
            continue

        semantic = stmt.get("semantic") or {}
        if semantic.get("role") != "investor_footnote":
            findings.append(("error", f"statement_role:{out_path}",
                f"{out_path} statement is missing semantic.role: investor_footnote"))

        expected = req.get("classification")
        actual = semantic.get("classification")
        if expected and actual != expected:
            findings.append(("error", f"statement_classification:{out_path}",
                f"{out_path} statement classification '{actual}' does not match "
                f"taxonomy-required '{expected}'"))

        content = stmt.get("content")
        if must_have_statement and (not isinstance(content, dict) or not content):
            findings.append(("error", f"statement_content:{out_path}",
                f"{out_path} statement content is empty; taxonomy requires attached detail"))
        elif req.get("schema") and isinstance(content, dict):
            for field_def in req["schema"]:
                fname = field_def.get("field")
                nullable = field_def.get("nullable", False)
                if fname not in content:
                    # `nullable: true` means the field may be omitted OR null.
                    # Only non-nullable fields are mandatory keys.
                    if not nullable:
                        findings.append(("error", f"statement_field:{out_path}.{fname}",
                            f"{out_path} statement content is missing required field '{fname}'"))
                elif content.get(fname) is None and not nullable:
                    findings.append(("error", f"statement_field:{out_path}.{fname}",
                        f"{out_path} statement content field '{fname}' is null but taxonomy "
                        f"marks it non-nullable"))
    return findings


# ---------------------------------------------------------------------------
# Primitive node contracts
# ---------------------------------------------------------------------------

_VALID_TYPES = {"scalar", "coded", "grid", "recordset", "statement", "reference"}

# Statement nodes carry a semantic role so downstream consumers can tell an
# investor-facing footnote from any future machine-only attachment class.
# Declared as a set so new roles are added here rather than by loosening the check.
_VALID_STATEMENT_ROLES = {"investor_footnote"}

# Type-specific payload requirement: at least one listed key must be present.
# Declared centrally so a malformed node is caught wherever it appears rather
# than only at hand-enumerated paths.
_PAYLOAD_KEYS = {
    "scalar": ("value",),
    "coded": ("entries",),
    "grid": ("rows", "columns", "value"),
    "recordset": ("records", "value"),
    "statement": ("content",),
    # Box 16 legitimately carries a target (checked), a notification
    # (unchecked), or an _unverified marker (state not extracted).
    "reference": ("target", "notification", "_unverified"),
}


def validate_primitive_contracts(body, root_path="body"):
    """Every typed TaxNode must carry semantic identity, form placement, and a
    type-appropriate payload.

    A prior adversarial review found that a node containing only
    `type: scalar` -- no semantic, no form, no value -- validated cleanly.
    This walks the whole body and enforces the primitive contract
    structurally, so malformed nodes are rejected at any depth.
    """
    findings = []

    def walk(node, path):
        if isinstance(node, dict):
            ntype = node.get("type")
            if ntype in _VALID_TYPES:
                semantic = node.get("semantic")
                if not isinstance(semantic, dict) or not semantic.get("id"):
                    findings.append(("error", f"primitive_semantic:{path}",
                        f"{path} ({ntype}) is missing semantic.id"))
                # Statements carry form.attachment rather than form placement
                # on the printed grid; every other primitive must be locatable.
                if ntype != "statement" and not isinstance(node.get("form"), dict):
                    findings.append(("error", f"primitive_form:{path}",
                        f"{path} ({ntype}) is missing form placement"))
                keys = _PAYLOAD_KEYS.get(ntype, ())
                if keys and not any(k in node for k in keys):
                    findings.append(("error", f"primitive_payload:{path}",
                        f"{path} ({ntype}) is missing a type-appropriate payload "
                        f"(one of: {', '.join(keys)})"))
                if ntype == "statement":
                    role = semantic.get("role") if isinstance(semantic, dict) else None
                    if not role:
                        findings.append(("error", f"primitive_role:{path}",
                            f"{path} (statement) is missing semantic.role"))
                    elif role not in _VALID_STATEMENT_ROLES:
                        findings.append(("error", f"primitive_role:{path}",
                            f"{path} (statement) has unrecognized semantic.role "
                            f"'{role}' (expected one of: "
                            f"{', '.join(sorted(_VALID_STATEMENT_ROLES))})"))
            for k, v in node.items():
                if k in ("semantic", "form"):
                    continue
                walk(v, f"{path}.{k}" if path else k)
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")

    walk(body, root_path)
    return findings


# ---------------------------------------------------------------------------
# Physical form completeness
# ---------------------------------------------------------------------------

def expected_physical_keys(taxonomy):
    """Collect the physical item_*/box_* keys the taxonomy declares for each
    part, so completeness is derived from the taxonomy itself rather than a
    hardcoded list that silently drifts when the form changes."""
    root = taxonomy.get("nodes", taxonomy)
    expected = {}
    for part_root in ("part_i", "part_ii", "part_iii"):
        part_node = root.get(part_root)
        if not isinstance(part_node, dict):
            continue
        children = part_node.get("children")
        if not isinstance(children, dict):
            children = part_node
        keys = [k for k in children
                if _BOX_RE.match(str(k)) or _ITEM_RE.match(str(k))]
        if keys:
            expected[part_root] = keys
    return expected


def validate_physical_completeness(body, taxonomy):
    """Every physical field the taxonomy declares must be represented in the
    assembled document -- even when its value is null with an _unverified
    marker. On a tax form, "omitted" and "reported as absent" are different
    facts, and only the latter is auditable."""
    findings = []
    for part_root, keys in expected_physical_keys(taxonomy).items():
        part = body.get(part_root)
        if not isinstance(part, dict):
            findings.append(("error", f"physical_part:{part_root}",
                f"{part_root} is missing or is not a structured section"))
            continue
        for key in keys:
            if key not in part:
                findings.append(("error", f"physical_field:{part_root}.{key}",
                    f"{part_root}.{key} is declared by the taxonomy but is "
                    f"absent from the assembled document"))
    return findings


# ---------------------------------------------------------------------------
# Capital account integrity (Item L)
# ---------------------------------------------------------------------------

_CAPITAL_COMPONENTS = (
    "beginning",
    "contributions",
    "current_year_increase_decrease",
    "other_increase_decrease",
    "withdrawals",
    "ending",
)


def validate_capital_account(body):
    """Item L must be complete and unambiguous, or explicitly quarantined.

    A prior adversarial review found two silent-acceptance paths: a capital
    account missing components validated cleanly because the checker filtered
    its expected field list down to fields already present, and a document
    carrying both `current_year_increase_decrease` and the legacy
    `current_year_net` alias had both summed, producing a false continuity
    figure that only warned. Both now fail unless the node is explicitly
    flagged for human review.
    """
    findings = []
    part_ii = body.get("part_ii")
    if not isinstance(part_ii, dict):
        return findings
    node = part_ii.get("item_l") or part_ii.get("capital_account")
    if not isinstance(node, dict):
        return findings

    quarantined = "_unverified" in node
    value = node.get("value")

    if not isinstance(value, dict):
        if not quarantined:
            findings.append(("error", "capital_shape",
                "part_ii.item_l has no structured capital account value and is "
                "not flagged for human review"))
        return findings

    # Ambiguity is never acceptable: two competing current-year fields mean the
    # true current-year change cannot be determined from the document alone.
    if "current_year_increase_decrease" in value and "current_year_net" in value:
        findings.append(("error", "capital_duplicate_alias",
            "part_ii.item_l contains both 'current_year_increase_decrease' and "
            "the legacy alias 'current_year_net'; the current-year change is "
            "ambiguous and cannot be reconciled"))

    normalized = dict(value)
    if ("current_year_increase_decrease" not in normalized
            and "current_year_net" in normalized):
        normalized["current_year_increase_decrease"] = normalized["current_year_net"]

    missing = [f for f in _CAPITAL_COMPONENTS if f not in normalized]
    if missing and not quarantined:
        findings.append(("error", "capital_incomplete",
            f"part_ii.item_l is missing capital account component(s): "
            f"{', '.join(missing)}"))

    if not missing:
        components = [normalized.get(f) for f in _CAPITAL_COMPONENTS[:-1]]
        ending = normalized.get("ending")
        if all(_numeric(c) for c in components) and _numeric(ending):
            delta = abs(sum(components) - ending)
            if delta > 1.0:
                findings.append(("warning", "capital_continuity",
                    f"part_ii.item_l ending balance differs from computed "
                    f"continuity by ${delta:,.2f}"))
        elif not quarantined:
            findings.append(("error", "capital_arithmetic",
                "part_ii.item_l components are present but not numerically "
                "comparable; continuity cannot be verified"))
    return findings


# ---------------------------------------------------------------------------
# Coded entry integrity
# ---------------------------------------------------------------------------

def validate_coded_entry_uniqueness(body):
    """Within a coded box, a code may appear at most once.

    `resolve_path()` locates a coded entry with `next()`, inspecting only the
    first match. A prior adversarial review showed that a second entry for
    the same code -- carrying no statement and no classification -- sitting
    behind a compliant first entry was invisible to every downstream check.

    The fix is not to teach the resolver to see past the first match. A box
    reports one amount per code, so a duplicate is malformed on the face of
    the form. Rejecting it removes the blind spot rather than navigating
    around it, and closes the same gap for any future check that resolves a
    coded path.
    """
    findings = []

    def walk(node, path):
        if isinstance(node, dict):
            entries = node.get("entries")
            if node.get("type") == "coded" and isinstance(entries, list):
                seen = {}
                for i, entry in enumerate(entries):
                    if not isinstance(entry, dict):
                        findings.append(("error", f"coded_entry_shape:{path}",
                            f"{path}.entries[{i}] is not a structured coded entry"))
                        continue
                    code = str(entry.get("code", "")).strip().upper()
                    if not code:
                        findings.append(("error", f"coded_entry_code:{path}",
                            f"{path}.entries[{i}] carries no code and cannot be "
                            f"located by any constraint"))
                        continue
                    if code in seen:
                        findings.append(("error", f"coded_duplicate:{path}.{code}",
                            f"{path} contains more than one entry for code {code} "
                            f"(indices {seen[code]} and {i}); only the first would "
                            f"be evaluated by path resolution"))
                    else:
                        seen[code] = i
            for key, val in node.items():
                if key in ("semantic", "form"):
                    continue
                walk(val, f"{path}.{key}" if path else key)
        elif isinstance(node, list):
            for i, val in enumerate(node):
                walk(val, f"{path}[{i}]")

    walk(body, "body")
    return findings


# ---------------------------------------------------------------------------
# Taxonomy-schema binding -- otd-parser-spec.md 4.7 steps 1 and 2
# ---------------------------------------------------------------------------
#
# "Structural validation: every node in the document maps to a taxonomy entry"
# and "Type validation: node values conform to their declared types" are
# published requirements that were never implemented. The prior checks
# verified that a node was SHAPED like a TaxNode and that a declared key was
# PRESENT, but never that the node agreed with what the taxonomy says it must
# be.
#
# A fourth adversarial review demonstrated six mutations to a known field --
# removing `type`, replacing the node with a bare {value: ...}, switching a
# declared scalar to coded, relocating it on the form, storing a string where
# a decimal is declared, and substituting an attacker-supplied semantic id --
# every one of which the shipping validator accepted with exit 0.
#
# Two vocabularies must be translated. The taxonomy is flat; the document is
# nested:
#
#     taxonomy                     document
#     -------------------------    --------------------------
#     type: scalar                 type: scalar
#     semantic_id: foo             semantic: {id: foo}
#     form_location: "Part III"    form: {location: "Part III"}
#     box: "4a"                    form: {box: "4a"}
#     value_type: decimal          value: <payload>
#
# Coded boxes add a second layer: taxonomy `codes: {A: {...}}` against
# document `entries: [{code: A, semantic: {...}, value: ...}]`.

_BINDING_VALUE_TYPES = {
    "decimal": (int, float),
    "integer": (int,),
    "percentage": (int, float),
    "string": (str,),
    "enum": (str,),
    "date": (str,),
    "boolean": (bool,),
}


def _bind_norm(value):
    return None if value is None else str(value).strip()


def _payload_conforms(value_type, value):
    """A null payload always conforms.

    The no-fabrication policy emits null plus an _unverified marker for
    anything not observed in the source. That is a reported fact about the
    document, not a type violation, and rejecting it would fail every honest
    partially-extracted K-1.
    """
    if value is None:
        return True
    expected = _BINDING_VALUE_TYPES.get(value_type)
    if expected is None:
        return True          # structured or unmapped declaration
    if value_type in ("decimal", "integer", "percentage"):
        # bool is a subclass of int in Python; True must not satisfy decimal.
        return isinstance(value, expected) and not isinstance(value, bool)
    return isinstance(value, expected)


def declared_physical_fields(taxonomy):
    """part -> {key: full taxonomy declaration}.

    expected_physical_keys() returns only key names, which is all the
    completeness check needs. Binding needs the declaration itself, so the
    two share a traversal shape but not an implementation.
    """
    root = taxonomy.get("nodes", taxonomy)
    out = {}
    for part in ("part_i", "part_ii", "part_iii"):
        node = root.get(part)
        if not isinstance(node, dict):
            continue
        children = node.get("children")
        if not isinstance(children, dict):
            children = node
        fields = {}
        for key, decl in children.items():
            k = str(key)
            if (_BOX_RE.match(k) or _ITEM_RE.match(k)) and isinstance(decl, dict):
                fields[k] = decl
        if fields:
            out[part] = fields
    return out


def _bind_coded_entries(path, decl, node):
    """Bind each coded entry to the code the taxonomy declares for that box."""
    findings = []
    codes = decl.get("codes")
    entries = node.get("entries")
    if not isinstance(codes, dict) or not isinstance(entries, list):
        return findings
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            findings.append(("error", f"binding_code_shape:{path}",
                f"{path}.entries[{i}] is not a structured coded entry"))
            continue
        code = _bind_norm(entry.get("code"))
        if not code:
            continue          # uniqueness check owns the missing-code finding
        cdecl = codes.get(code) or codes.get(code.upper())
        if not isinstance(cdecl, dict):
            findings.append(("error", f"binding_code:{path}.{code}",
                f"{path} reports code '{code}', which the taxonomy does not "
                f"declare for this box"))
            continue
        want_sem = _bind_norm(cdecl.get("semantic_id"))
        semantic = entry.get("semantic")
        got_sem = (_bind_norm(semantic.get("id"))
                   if isinstance(semantic, dict) else None)
        if want_sem and got_sem != want_sem:
            findings.append(("error", f"binding_code_semantic:{path}.{code}",
                f"{path} code {code} carries semantic.id '{got_sem}' but the "
                f"taxonomy declares '{want_sem}'"))
        want_vt = _bind_norm(cdecl.get("value_type"))
        if (want_vt and "value" in entry
                and not _payload_conforms(want_vt, entry.get("value"))):
            findings.append(("error", f"binding_code_value:{path}.{code}",
                f"{path} code {code} declares value_type '{want_vt}' but "
                f"carries {type(entry.get('value')).__name__}"))
    return findings


def validate_taxonomy_binding(body, taxonomy):
    """Bind every declared physical node to its taxonomy entry.

    Absence is deliberately NOT reported here. validate_physical_completeness()
    owns that finding, and duplicating it would report every missing field
    twice under two different constraint ids.

    A node that declares no `type` is an error rather than a skip. The prior
    primitive walk gated on `if ntype in _VALID_TYPES`, so deleting the key
    removed the node from validation entirely -- which also let a duplicate
    coded entry hide by stripping `type: coded` from its enclosing box.
    """
    findings = []
    for part, fields in declared_physical_fields(taxonomy).items():
        section = body.get(part)
        if not isinstance(section, dict):
            continue          # completeness owns the missing-part finding
        for key, decl in fields.items():
            if key not in section:
                continue      # completeness owns the missing-field finding
            path = f"{part}.{key}"
            node = section.get(key)
            if not isinstance(node, dict):
                findings.append(("error", f"binding_shape:{path}",
                    f"{path} is declared by the taxonomy but is a "
                    f"{type(node).__name__}, not a TaxNode"))
                continue

            want_type = _bind_norm(decl.get("type"))
            got_type = _bind_norm(node.get("type"))
            if got_type is None:
                findings.append(("error", f"binding_type:{path}",
                    f"{path} declares no type; the taxonomy declares "
                    f"'{want_type}'"))
            elif want_type and got_type != want_type:
                findings.append(("error", f"binding_type:{path}",
                    f"{path} is '{got_type}' but the taxonomy declares "
                    f"'{want_type}'"))

            want_sem = _bind_norm(decl.get("semantic_id"))
            semantic = node.get("semantic")
            got_sem = (_bind_norm(semantic.get("id"))
                       if isinstance(semantic, dict) else None)
            if want_sem and got_sem != want_sem:
                findings.append(("error", f"binding_semantic:{path}",
                    f"{path} carries semantic.id '{got_sem}' but the taxonomy "
                    f"declares '{want_sem}'"))

            form = node.get("form")
            want_loc = _bind_norm(decl.get("form_location"))
            got_loc = (_bind_norm(form.get("location"))
                       if isinstance(form, dict) else None)
            if want_loc and got_loc != want_loc:
                findings.append(("error", f"binding_form:{path}",
                    f"{path} is placed at '{got_loc}' but the taxonomy "
                    f"declares '{want_loc}'"))

            want_box = _bind_norm(decl.get("box"))
            got_box = (_bind_norm(form.get("box"))
                       if isinstance(form, dict) else None)
            if want_box and got_box and got_box != want_box:
                findings.append(("error", f"binding_form:{path}",
                    f"{path} reports box '{got_box}' but the taxonomy "
                    f"declares '{want_box}'"))

            want_vt = _bind_norm(decl.get("value_type"))
            if (want_vt and "value" in node
                    and not _payload_conforms(want_vt, node.get("value"))):
                findings.append(("error", f"binding_value_type:{path}",
                    f"{path} declares value_type '{want_vt}' but carries "
                    f"{type(node.get('value')).__name__} "
                    f"({node.get('value')!r})"))

            findings.extend(_bind_coded_entries(path, decl, node))
    return findings


def run_taxonomy_driven_validation(body, taxonomy, statements=None):
    """Single entry point: load constraints + statement requirements from
    the taxonomy and execute both against the assembled document body.
    Returns (errors: list[str], warnings: list[str])."""
    errors, warnings = [], []
    constraint_findings = run_constraints(body, taxonomy.get("constraints"))
    for severity, cid, msg in constraint_findings:
        (errors if severity == "error" else warnings).append(msg)

    requirements = extract_statement_requirements(taxonomy)
    stmt_findings = validate_statement_requirements(body, requirements)
    for severity, _, msg in stmt_findings:
        (errors if severity == "error" else warnings).append(msg)

    for severity, _, msg in validate_primitive_contracts(body):
        (errors if severity == "error" else warnings).append(msg)

    for severity, _, msg in validate_physical_completeness(body, taxonomy):
        (errors if severity == "error" else warnings).append(msg)

    # Taxonomy binding runs AFTER completeness by design. Completeness owns
    # absence; binding owns the identity and type of what is present. Reversing
    # the order would report a missing field twice under two constraint ids.
    for severity, _, msg in validate_taxonomy_binding(body, taxonomy):
        (errors if severity == "error" else warnings).append(msg)

    for severity, _, msg in validate_capital_account(body):
        (errors if severity == "error" else warnings).append(msg)

    # Duplicate codes within a coded box are rejected outright: path
    # resolution inspects only the first entry for a code, so a second entry
    # would be invisible to every check above.
    for severity, _, msg in validate_coded_entry_uniqueness(body):
        (errors if severity == "error" else warnings).append(msg)

    # Root-level statements live outside `body` but are still TaxNodes and
    # must satisfy the same primitive contract as any in-body node.
    if statements:
        for severity, _, msg in validate_primitive_contracts(statements, "statements"):
            (errors if severity == "error" else warnings).append(msg)

    return errors, warnings
