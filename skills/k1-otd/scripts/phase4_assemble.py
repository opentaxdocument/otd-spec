
#!/usr/bin/env python
"""K-1 OTD Extraction Skill — Phase 4: OTD Assembly from JSON Fragments
Merges 5 extraction fragments into a compliant OTD YAML document + confidence manifest.
"""
import sys
import json
import uuid
import argparse
import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

try:
    from ruamel.yaml import YAML
    _yaml = YAML()
    _yaml.indent(mapping=2, sequence=4, offset=2)
    _yaml.default_flow_style = False
except ImportError:
    print("ERROR: pip install ruamel.yaml", file=sys.stderr)
    sys.exit(1)


def load(path):
    if not Path(path).exists():
        print(f"WARNING: missing fragment: {path}", file=sys.stderr)
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)



CLASSIFICATION_ALIASES = {
    "section_199a": "section_199a_detail",
    "irs_form_926": "form_926_transfer_to_foreign_corp",
    "state_k1_grid": "state_apportionment",
}


def canon_classification(value):
    """Map deprecated classification names onto their canonical catalog IDs."""
    if not isinstance(value, str):
        return value
    return CLASSIFICATION_ALIASES.get(value, value)


def scalar(sem_id, label, loc, box, value, unverified=None):
    node = {
        "type": "scalar",
        "semantic": {"id": sem_id, "label": label},
        "form": {"form_id": "k1-1065", "location": loc, "box": box},
        "value": value
    }
    if unverified:
        node["_unverified"] = unverified
    return node



# The SKILL.md Turn A contract declares null AND the literal "UNKNOWN" as
# "not yet observed" sentinels that must never appear as output values.
# field() honoured null but passed "UNKNOWN" through as a real value, so a
# boolean checkbox could emit the string "UNKNOWN" -- and the regression case
# named unknown_sentinel_item_k3_flagged passed for two waves while the
# flagging it named never happened. Taxonomy binding caught the gap.
#
# Deliberately contract-exact. "n/a" is NOT included: field()'s own docstring
# directs a source reporting "not applicable" to use a non-null sentinel to
# survive this check, so swallowing it would discard a reported fact.
_UNKNOWN_SENTINELS = {"unknown"}


def _is_unknown_sentinel(value):
    """True when a source value is a not-yet-observed placeholder."""
    return isinstance(value, str) and value.strip().lower() in _UNKNOWN_SENTINELS


def field(src, key, sem_id, label, loc, box):
    """Emit a scalar from a source mapping without fabricating defaults.

    A key the extraction pipeline never populated -- whether the key is
    absent entirely, or present with an explicit `null` (the template's
    "not yet extracted" sentinel) -- yields value: null plus an
    _unverified marker. An unknown checkbox is never reported as false,
    and an unextracted amount is never reported as zero. Omission and
    reported-null remain distinguishable from a genuinely observed null
    only in the sense that both routes to this function; a source that
    explicitly reports "not applicable" should use a non-null sentinel
    value, not bare null, to survive this check.
    """
    if (not isinstance(src, dict) or key not in src
            or src.get(key) is None or _is_unknown_sentinel(src.get(key))):
        return scalar(
            sem_id, label, loc, box, None,
            unverified=f"HUMAN REVIEW REQUIRED: {loc} was not extracted from the source document")
    return scalar(sem_id, label, loc, box, src.get(key))


def make_statement_node(stmt_data):
    """Build a canonical statement TaxNode from extracted footnote/statement data.

    Defensive against malformed (non-dict) input: rather than raising
    AttributeError deep in assembly, emits an explicit review-flagged
    placeholder statement so the document remains assemblable and the
    defect is visible to a human reviewer instead of crashing the pipeline.
    """
    if not isinstance(stmt_data, dict):
        return {
            "type": "statement",
            "semantic": {
                "id": "stmt_malformed_input",
                "label": "Malformed Statement Input",
                "classification": "unclassified_requires_review",
                "role": "investor_footnote"
            },
            "form": {"attachment": True},
            "content": {},
            "_unverified": (
                "HUMAN REVIEW REQUIRED: statement input was not a structured "
                f"object (received {type(stmt_data).__name__})")
        }
    classification = canon_classification(
        stmt_data.get("classification", "unclassified_requires_review"))
    return {
        "type": "statement",
        "semantic": {
            "id": f"stmt_{classification}",
            "label": classification.replace("_", " ").title(),
            "classification": classification,
            "role": stmt_data.get("role", "investor_footnote")
        },
        "form": {"attachment": True},
        "content": stmt_data.get("content", {})
    }


def make_item_m(raw):
    """Item M: boolean scalar; attaches the IRS-required built-in gain/loss
    statement when true. Never fabricates a false default and never leaves
    the emitted value as a raw dict. A non-boolean sentinel (e.g. "UNKNOWN")
    is treated the same as an unresolved checkbox, not as a literal value."""
    loc, box = "Part II, Item M", "M"
    sem_id = "partner.contributed_property_built_in_gain_loss"
    label = "Contributed Property with Built-In Gain or Loss"
    if raw is None:
        return scalar(sem_id, label, loc, box, None,
            unverified="HUMAN REVIEW REQUIRED: Part II, Item M was not extracted from the source document")
    if isinstance(raw, dict):
        value = raw.get("value")
        if value is None or (isinstance(value, str) and not isinstance(value, bool)):
            return scalar(sem_id, label, loc, box, None,
                unverified="HUMAN REVIEW REQUIRED: Part II, Item M was not extracted from the source document")
        node = scalar(sem_id, label, loc, box, value)
        if value is True:
            if raw.get("statement"):
                node["statement"] = make_statement_node(raw["statement"])
            else:
                node["_unverified"] = (
                    "HUMAN REVIEW REQUIRED: Item M is Yes but no built-in gain/loss "
                    "statement (property description, contribution date, gain/loss) was extracted")
        return node
    if isinstance(raw, str):
        return scalar(sem_id, label, loc, box, None,
            unverified="HUMAN REVIEW REQUIRED: Part II, Item M was not extracted from the source document")
    value = bool(raw)
    node = scalar(sem_id, label, loc, box, value)
    if value:
        node["_unverified"] = "HUMAN REVIEW REQUIRED: Item M is Yes but statement detail was not extracted"
    return node


def normalize_capital_account(pii):
    """Normalize legacy capital-account aliases without double counting."""
    loc, box = "Part II, Item L", "L"
    sem_id = "partner.capital_account_analysis"
    label = "Capital account analysis"
    if not isinstance(pii, dict) or "capital_account" not in pii:
        return scalar(
            sem_id, label, loc, box, None,
            unverified="HUMAN REVIEW REQUIRED: Part II, Item L was not extracted from the source document")
    raw = pii.get("capital_account")
    if raw is None:
        return scalar(sem_id, label, loc, box, None,
                      unverified="HUMAN REVIEW REQUIRED: Part II, Item L was explicitly null")
    if not isinstance(raw, dict):
        return scalar(sem_id, label, loc, box, raw,
                      unverified="HUMAN REVIEW REQUIRED: Part II, Item L is not an object")
    value = dict(raw)
    if "current_year_increase_decrease" in value and "current_year_net" in value:
        return scalar(
            sem_id, label, loc, box, value,
            unverified="HUMAN REVIEW REQUIRED: Item L contains both current-year field aliases")
    if "current_year_increase_decrease" not in value and "current_year_net" in value:
        value["current_year_increase_decrease"] = value.pop("current_year_net")
    return scalar(sem_id, label, loc, box, value)


# ---------------------------------------------------------------------------
# Taxonomy-sourced coded semantics
# ---------------------------------------------------------------------------
#
# A binding dry run found 18 positional semantic ids in emitted output
# (other_income.a) where the taxonomy declares meaning-bearing ones
# (other_income.portfolio). make_coded_entry carried the same fabrication in
# its fallback -- latent here only because no fixture exercised a coded box.
#
# Production must not invent identity. Resolve it from the taxonomy the
# document already claims to be governed by, and flag it when resolution
# fails rather than presenting a synthesized id as declared fact.

_TAXONOMY_PATH = (Path(__file__).resolve().parent.parent
                  / "reference" / "irs-k1-1065-2025.yaml")
_TAXONOMY_CACHE = None
_CODE_SEMANTICS_CACHE = {}


def load_taxonomy():
    """Load the canonical taxonomy mirror shipped beside this skill."""
    global _TAXONOMY_CACHE
    if _TAXONOMY_CACHE is None:
        if not _TAXONOMY_PATH.exists():
            print(f"WARNING: taxonomy not found at {_TAXONOMY_PATH}",
                  file=sys.stderr)
            _TAXONOMY_CACHE = {}
        else:
            with open(_TAXONOMY_PATH, encoding="utf-8") as f:
                _TAXONOMY_CACHE = _yaml.load(f) or {}
    return _TAXONOMY_CACHE


def taxonomy_code_semantics(box_key):
    """{CODE: {"semantic_id": ..., "label": ...}} as declared for one box."""
    if box_key in _CODE_SEMANTICS_CACHE:
        return _CODE_SEMANTICS_CACHE[box_key]
    tax = load_taxonomy()
    root = tax.get("nodes", tax)
    part = root.get("part_iii")
    out = {}
    if isinstance(part, dict):
        children = part.get("children")
        if not isinstance(children, dict):
            children = part
        decl = children.get(box_key)
        codes = decl.get("codes") if isinstance(decl, dict) else None
        if isinstance(codes, dict):
            for code, cdecl in codes.items():
                if isinstance(cdecl, dict):
                    out[str(code).upper()] = {
                        "semantic_id": cdecl.get("semantic_id"),
                        "label": cdecl.get("label"),
                    }
    _CODE_SEMANTICS_CACHE[box_key] = out
    return out


def make_coded_entry(box_key, raw_entry, statement=None):
    """Convert one extraction entry into the canonical coded-node wire shape.

    Semantic identity is resolved from the taxonomy rather than synthesized
    from the code letter. A synthesized id is a last resort and is always
    accompanied by an _unverified marker.
    """
    raw = raw_entry if isinstance(raw_entry, dict) else {}
    code = str(raw.get("code", "")).upper()
    raw_semantic = raw.get("semantic") if isinstance(raw.get("semantic"), dict) else {}
    declared = taxonomy_code_semantics(box_key).get(code) or {}
    label = (raw.get("label")
             or raw_semantic.get("label")
             or declared.get("label")
             or f"Code {code or 'UNKNOWN'}")
    semantic_id = (
        raw.get("semantic_id")
        or raw_semantic.get("id")
        or declared.get("semantic_id")
    )
    unresolved_identity = not semantic_id
    if unresolved_identity:
        # Keep the node structurally valid so primitive checks still run,
        # but never present a fabricated identity as declared fact.
        semantic_id = f"part_iii.{box_key}.{code.lower() or 'unknown'}"
    value = raw["value"] if "value" in raw else raw.get("amount")
    semantic = {"id": semantic_id, "label": label}
    classification = canon_classification(
        raw.get("classification") or raw_semantic.get("classification")
    )
    if classification:
        semantic["classification"] = classification
    node = {"code": code, "semantic": semantic, "value": value}
    if statement is not None:
        node["statement"] = statement
    elif isinstance(raw.get("statement"), dict):
        node["statement"] = make_statement_node(raw["statement"])
    if raw.get("statement_ref") and statement is None:
        node["source_statement_ref"] = raw["statement_ref"]
    reasons = []
    if raw.get("requires_human_review") and raw.get("reason"):
        reasons.append(str(raw["reason"]))
    if "value" not in raw and "amount" not in raw:
        reasons.append(f"{box_key} Code {code or 'UNKNOWN'} has no extracted value")
    if unresolved_identity:
        reasons.append(
            f"{box_key} Code {code or 'UNKNOWN'} is not declared by the "
            f"taxonomy; semantic id was synthesized")
    if reasons:
        node["_unverified"] = "HUMAN REVIEW REQUIRED: " + "; ".join(reasons)
    return node


def count_unverified(obj, path=""):
    count, paths = 0, []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "_unverified":
                count += 1
                paths.append(path)
            else:
                c, p = count_unverified(v, f"{path}.{k}" if path else k)
                count += c
                paths.extend(p)
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            c, p = count_unverified(item, f"{path}[{i}]")
            count += c
            paths.extend(p)
    return count, paths


def count_leaves(obj):
    if isinstance(obj, dict):
        return sum(count_leaves(v) for v in obj.values())
    if isinstance(obj, list):
        return sum(count_leaves(v) for v in obj)
    return 1 if obj is not None else 0


STATEMENT_LIST_KEYS = ("footnotes", "statements", "items", "entries")


def as_list(obj, source="fragment"):
    """Extract the statement list from a fragment. STRICT BY DESIGN.

    The prior implementation returned the FIRST non-empty list found among a
    dict's values, regardless of key. Any incidental list-valued metadata key
    therefore BECAME the statement collection. A fragment carrying
    `_source_pages_not_processed: [2, 3, 4, 5, ...]` produced 22 phantom
    statement nodes -- attachments the source document never contained -- and
    inflated the emitted document by 216 fields. On a tax document that is
    node fabrication, which is precisely what this format exists to prevent.

    Rules now:
      * a bare list is accepted as-is
      * a dict must declare its list under one of STATEMENT_LIST_KEYS
      * a dict with NO recognised key but a non-empty list somewhere is
        REJECTED, not guessed at -- that is the exact defect above
      * an empty or list-free dict yields no statements (a fragment may
        legitimately contain only metadata)
    """
    if isinstance(obj, list):
        return obj
    if not isinstance(obj, dict):
        return []
    for key in STATEMENT_LIST_KEYS:
        v = obj.get(key)
        if isinstance(v, list):
            return v
        if v is not None:
            raise SystemExit(
                "ERROR: %s key %r must be a list, got %s"
                % (source, key, type(v).__name__))
    stray = [k for k, v in obj.items()
             if isinstance(v, list) and v and not k.startswith("_")]
    if stray:
        raise SystemExit(
            "ERROR: %s contains list-valued key(s) %s but declares no "
            "statement list under any of %s.\n"
            "       Refusing to guess which list holds statements: guessing "
            "here previously fabricated 22 statement nodes that had no "
            "counterpart in the source document.\n"
            "       Declare the list explicitly, e.g. {\"footnotes\": [...]}."
            % (source, stray, list(STATEMENT_LIST_KEYS)))
    return []



# Part III scalar box definitions: (key, semantic_id, label, location, box)
SCALAR_BOXES = [
    ("box_1",  "ordinary_business_income",      "Ordinary Business Income (Loss)",      "Part III, Box 1",  1),
    ("box_2",  "net_rental_real_estate_income", "Net Rental Real Estate Income (Loss)", "Part III, Box 2",  2),
    ("box_3",  "other_net_rental_income",       "Other Net Rental Income (Loss)",       "Part III, Box 3",  3),
    ("box_4a", "guaranteed_payments_services",  "Guaranteed Payments for Services",     "Part III, Box 4a", "4a"),
    ("box_4b", "guaranteed_payments_capital",   "Guaranteed Payments for Capital",      "Part III, Box 4b", "4b"),
    ("box_4c", "guaranteed_payments_total",     "Total Guaranteed Payments",            "Part III, Box 4c", "4c"),
    ("box_5",  "interest_income",               "Interest Income",                      "Part III, Box 5",  5),
    ("box_6a", "ordinary_dividends",            "Ordinary Dividends",                   "Part III, Box 6a", "6a"),
    ("box_6b", "qualified_dividends",           "Qualified Dividends",                  "Part III, Box 6b", "6b"),
    ("box_6c", "dividend_equivalents",          "Dividend Equivalents",                 "Part III, Box 6c", "6c"),
    ("box_7",  "royalties",                     "Royalties",                            "Part III, Box 7",  7),
    ("box_8",  "net_short_term_capital_gain",                      "Net Short-Term Capital Gain (Loss)",   "Part III, Box 8",  8),
    ("box_9a", "net_long_term_capital_gain",                      "Net Long-Term Capital Gain (Loss)",    "Part III, Box 9a", "9a"),
    ("box_9b", "collectibles_gain",             "Collectibles (28%) Gain (Loss)",       "Part III, Box 9b", "9b"),
    ("box_9c", "unrecaptured_section_1250_gain",                    "Unrecaptured Section 1250 Gain",       "Part III, Box 9c", "9c"),
    ("box_10", "net_section_1231_gain",                      "Net Section 1231 Gain (Loss)",         "Part III, Box 10", 10),
    ("box_12", "section_179_deduction",                       "Section 179 Deduction",                "Part III, Box 12", 12),
    ("box_21", "foreign_taxes_paid_accrued",    "Foreign Taxes Paid or Accrued",        "Part III, Box 21", 21),
    ("box_22", "at_risk_activities",            "More Than One At-Risk Activity",       "Part III, Box 22", 22),
    ("box_23", "passive_activities",            "More Than One Passive Activity",       "Part III, Box 23", 23),
]

# Canonical Part III emission order, matching the printed form.
PART_III_ORDER = [
    "box_1", "box_2", "box_3", "box_4a", "box_4b", "box_4c", "box_5",
    "box_6a", "box_6b", "box_6c", "box_7", "box_8", "box_9a", "box_9b",
    "box_9c", "box_10", "box_11", "box_12", "box_13", "box_14", "box_15",
    "box_16", "box_17", "box_18", "box_19", "box_20", "box_21", "box_22",
    "box_23",
]
CODED_BOXES = {
    "box_11": "Other Income (Loss)",
    "box_13": "Other Deductions",
    "box_14": "Self-Employment Earnings (Loss)",
    "box_15": "Credits",
    "box_17": "AMT Items",
    "box_18": "Tax-Exempt Income and Nondeductible Expenses",
    "box_19": "Distributions",
    "box_20": "Other Information",
}


def main():
    p = argparse.ArgumentParser(description="K-1 OTD Phase 4 — assemble fragments into OTD YAML")
    p.add_argument("--fragments", required=True, help="Path to fragments/ directory")
    p.add_argument("--out", required=True, help="Output OTD YAML path")
    p.add_argument("--sha256", default="not_computed", help="Source PDF SHA-256")
    args = p.parse_args()

    fd = Path(args.fragments)
    face   = load(fd / "face_page.json")
    ov_raw = load(fd / "overflow_statements.json")
    fn_a   = load(fd / "footnotes_a.json")
    fn_b   = load(fd / "footnotes_b.json")
    states = load(fd / "state_schedules.json")

    pi  = face.get("part_i", {}) or {}
    pii = face.get("part_ii", {}) or {}
    fm  = face.get("form_metadata", {}) or {}
    fb  = face.get("part_iii_face", {}) or {}
    ov  = (ov_raw.get("part_iii_overflow", {}) or {}) if isinstance(ov_raw, dict) else {}
    excluded_overflow = (
        ov_raw.get("_excluded_from_overflow_entries", [])
        if isinstance(ov_raw, dict) else []
    )
    if excluded_overflow is None:
        excluded_overflow = []
    if not isinstance(excluded_overflow, list):
        raise SystemExit(
            "ERROR: _excluded_from_overflow_entries must be a list, not %s"
            % type(excluded_overflow).__name__)
    if any(not isinstance(entry, dict) for entry in excluded_overflow):
        raise SystemExit(
            "ERROR: every _excluded_from_overflow_entries member must be an object")

    # ── Statements ────────────────────────────────────────────────────────
    statements_root = []
    statement_map = {}


    for i, raw_fn in enumerate(as_list(fn_a, "footnotes_a.json")
                               + as_list(fn_b, "footnotes_b.json"), 1):
        # A non-dict member is NOT an empty statement. Coercing it to {} is
        # how integers from a page-number list became 22 statement nodes with
        # blank labels, empty content, and classification
        # 'unclassified_requires_review' -- each one asserting an attachment
        # the source never contained. Fail loudly instead.
        if not isinstance(raw_fn, dict):
            raise SystemExit(
                "ERROR: statement #%d is %s (%r), not an object.\n"
                "       A statement must be an object describing a real "
                "attachment. Coercing a non-object into an empty statement "
                "fabricates an attachment that does not exist in the source "
                "document." % (i, type(raw_fn).__name__, raw_fn))
        fn = raw_fn
        node = {
            "type": "statement",
            "semantic": {
                "id": fn.get("id", f"stmt-{i:03d}"),
                "label": fn.get("label", ""),
                "classification": canon_classification(
                    fn.get("classification", "unclassified_requires_review")),
                "role": fn.get("role", "investor_footnote")
            },
            "form": {"attachment": True, "attachment_sequence": i},
            "cross_references": fn.get("cross_references", []),
            "content": fn.get("content", fn.get("structured", {})),
            "source_text": fn.get("source_text", ""),
            "confidence": fn.get("confidence", 0.5),
            "extraction_method": fn.get("extraction_method", "ai_structured")
        }
        for flag in ("_unverified", "_escalate"):
            if flag in fn:
                node[flag] = fn[flag]

        refs = fn.get("cross_references", [])
        if refs:
            for ref in refs:
                statement_map.setdefault(ref.lower(), []).append(node)
        else:
            statements_root.append(node)

    # Part III scalars. Absent source keys are flagged, never defaulted.
    part_iii = {}
    for box_key, sem_id, label, loc, box in SCALAR_BOXES:
        part_iii[box_key] = field(fb, box_key, sem_id, label, loc, box)


    for box_key, label in CODED_BOXES.items():
        box_num = box_key.replace("box_", "")
        face_list = as_list(fb.get(box_key, []))
        ov_list = as_list(ov.get(box_key, []))
        excluded_list = [
            dict(entry) for entry in excluded_overflow
            if str(entry.get("box", "")) in {box_key, box_num}
        ]

        merged_dict = {}
        for raw_entry in face_list + ov_list + excluded_list:
            if not isinstance(raw_entry, dict):
                continue
            code = str(raw_entry.get("code", "")).upper()
            raw_value = raw_entry["value"] if "value" in raw_entry else raw_entry.get("amount")
            dedupe_value = json.dumps(raw_value, sort_keys=True, default=str)
            key = (code, dedupe_value)
            if key not in merged_dict or len(raw_entry) > len(merged_dict[key]):
                merged_dict[key] = dict(raw_entry)

        entries = []
        for raw_entry in merged_dict.values():
            code = str(raw_entry.get("code", "")).upper()
            ref_str = f"{box_key}_{code}".lower()
            statement = None
            if ref_str in statement_map and statement_map[ref_str]:
                statement = statement_map[ref_str].pop(0)
            entries.append(make_coded_entry(box_key, raw_entry, statement))

        part_iii[box_key] = {
            "type": "coded",
            "semantic": {
                "id": {
                    "box_11": "other_income",
                    "box_13": "other_deductions",
                    "box_14": "self_employment",
                    "box_15": "credits",
                    "box_17": "amt_items",
                    "box_18": "tax_exempt_nondeductible",
                    "box_19": "distributions",
                    "box_20": "other_information",
                }.get(box_key, f"part_iii.{box_key}"),
                "label": label,
            },
            "form": {
                "form_id": "k1-1065",
                "location": f"Part III, Box {box_num}",
                "box": int(box_num) if box_num.isdigit() else box_num,
            },
            "entries": entries,
        }

    for remaining_list in statement_map.values():
        statements_root.extend(remaining_list)

    # ── Document ─────────────────────────────────────────────────────────
    run_id = str(uuid.uuid4())
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Normalize filing_status against spec enum (original | amended | superseded | void)
    raw_status = fm.get("filing_status")
    if isinstance(raw_status, str):
        raw_status = raw_status.lower()
    if raw_status in ("original", "amended", "superseded", "void"):
        filing_status = raw_status
    elif fm.get("amended"):
        filing_status = "amended"
    else:
        filing_status = "amended" if fm.get("amended") is True else None

    # Detect masked PII fields and emit redaction declaration
    redacted_fields = []
    if pi.get("_partnership_ein_masked") or (isinstance(pi.get("partnership_ein"), str) and "*" in (pi.get("partnership_ein") or "")):
        redacted_fields.append("body.part_i.item_a")
    if pii.get("_partner_tin_masked") or (isinstance(pii.get("partner_tin"), str) and "*" in (pii.get("partner_tin") or "")):
        redacted_fields.append("body.part_ii.item_e")


    # Box 16 — International transactions; branches on the extracted checkbox
    # state. IRS: checked -> attached K-3 reference; unchecked -> required
    # non-furnishing notification; unknown -> explicit human-review flag.
    # Prefer deterministic face reader evidence; fall back to manual fragment.
    evidence_path = fd / "face_reader.evidence.json"
    box_16_checked = fb.get("box_16_checked")
    if evidence_path.exists():
        try:
            ev = json.loads(evidence_path.read_text(encoding="utf-8-sig"))
            b16_ev = (ev.get("fields") or {}).get("box_16", {})
            b16_status = b16_ev.get("status")
            if b16_status == "present":
                box_16_checked = True
            elif b16_status == "verified_absent":
                box_16_checked = False
            elif b16_status is not None:
                box_16_checked = None
        except (json.JSONDecodeError, OSError):
            pass  # fall back to fragment value
    b16 = {
        "type": "reference",
        "semantic": {"id": "international_transactions", "label": "International Transactions"},
        "form": {"form_id": "k1-1065", "location": "Part III, Box 16", "box": 16},
        "checked": box_16_checked,
    }
    if box_16_checked is True:
        b16["target"] = {
            "document_type": "otd",
            "taxonomy_id": "irs-k3-1065-2025",
            "node_path": "/"
        }
    elif box_16_checked is False:
        b16["notification"] = make_statement_node({
            "classification": "k3_not_attached_notification",
            "content": {
                "notification_text": (
                    "The partner will not receive Schedule K-3 unless the partner requests the schedule.")
            }
        })
    else:
        b16["_unverified"] = "HUMAN REVIEW REQUIRED: Box 16 checkbox state was not extracted"
    # Insert box_16 between box_15 and box_17 in part_iii (preserve form order)
    ordered_part_iii = {}
    for k in part_iii.keys():
        ordered_part_iii[k] = part_iii[k]
        if k == "box_15":
            ordered_part_iii["box_16"] = b16
    if "box_16" not in ordered_part_iii:
        ordered_part_iii["box_16"] = b16
    part_iii = ordered_part_iii

    # Item K3 -> Box 20 Code X linkage (IRS requires payment-obligation detail
    # under Box 20 Code X when Item K3 is checked)
    if pii.get("item_k3") is True:
        entries_20 = part_iii.get("box_20", {}).get("entries", [])
        has_x_statement = any(e.get("code") == "X" and e.get("statement") for e in entries_20)
        if not has_x_statement:
            part_iii["box_20"]["_unverified"] = (
                "HUMAN REVIEW REQUIRED: Item K3 is checked but Box 20 Code X "
                "payment-obligation detail was not found")

    part_iii = {key: part_iii[key] for key in PART_III_ORDER if key in part_iii}

    doc = {
        "otd": {
            "version": "0.1",
            "document_id": run_id,
            "created": now,
            "producer": {"name": "SecondWind K-1 OTD Extractor", "version": "1.1.0"},
            "taxonomy": {
                "id": "irs-k1-1065-2025",
                "version": "2025.1.0",
                "source": "IRS Instructions for Schedule K-1 (Form 1065), 2025"
            },
            "source_document": {
                "sha256": args.sha256,
                "extraction_method": "ai_structured",
                "extraction_date": now
            }
        },
        "form_metadata": {
            "tax_year": fm.get("tax_year"),
            "fiscal_year": fm.get("fiscal_year", False),
            "fiscal_year_begin": fm.get("fiscal_year_begin"),
            "fiscal_year_end": fm.get("fiscal_year_end"),
            "amended": fm.get("amended", False),
            "final": fm.get("final", False),
            "supersedes_document_id": fm.get("supersedes_document_id"),
            "filing_status": filing_status,
            "form_revision_date": fm.get("form_revision_date", "2025")
        },
        "body": {
            "form_id": "k1-1065",
            "tax_year": fm.get("tax_year"),
            "part_i": {
                "item_a": field(pi, "partnership_ein", "partnership.ein", "Partnership's EIN", "Part I, Item A", "A"),
                "item_b": field(pi, "partnership_name", "partnership.name_address", "Partnership's name, address", "Part I, Item B", "B"),
                "item_c": field(pi, "irs_center", "partnership.irs_center", "IRS Center", "Part I, Item C", "C"),
                "item_d": field(pi, "publicly_traded", "partnership.publicly_traded", "Publicly Traded Partnership", "Part I, Item D", "D")
            },

            "part_ii": {
                "item_e": field(pii, "partner_tin", "partner.identifying_number", "Partner's identifying number", "Part II, Item E", "E"),
                "item_f": field(pii, "partner_name", "partner.name_address", "Partner's name, address", "Part II, Item F", "F"),
                "item_g": field(pii, "general_or_limited", "partner.general_or_limited", "General or Limited", "Part II, Item G", "G"),
                "item_h1": field(pii, "domestic_or_foreign", "partner.domestic_or_foreign", "Domestic or Foreign Partner", "Part II, Item H1", "H1"),
                "item_h2": field(pii, "disregarded_entity_info", "partner.disregarded_entity_info", "Disregarded Entity Info", "Part II, Item H2", "H2"),
                "item_i1": field(pii, "entity_type", "partner.entity_type", "Entity type", "Part II, Item I1", "I1"),
                "item_i2": field(pii, "retirement_plan", "partner.retirement_plan", "Retirement Plan Partner", "Part II, Item I2", "I2"),
                "item_j": field(pii, "share_percentages", "partner.share_percentages", "Share percentages", "Part II, Item J", "J"),
                "item_k1": field(pii, "liabilities", "partner.share_of_liabilities", "Liabilities", "Part II, Item K1", "K1"),
                "item_k2": field(pii, "item_k2", "partner.liabilities_from_lower_tier_partnerships", "Lower-Tier Liabilities", "Part II, Item K2", "K2"),
                "item_k3": field(pii, "item_k3", "partner.liabilities_subject_to_guarantees_or_payment_obligations", "Payment Obligation Liabilities", "Part II, Item K3", "K3"),
                "item_l": normalize_capital_account(pii),
                "item_m": make_item_m(pii.get("item_m")),
                "item_n": field(pii, "item_n", "partner.net_unrecognized_section_704c_gain_loss", "Net Unrecognized 704(c) Gain/Loss", "Part II, Item N", "N")
            },
            "part_iii": part_iii
        },
        "statements": statements_root
    }

    if redacted_fields:
        doc["redaction"] = {
            "policy": "partial",
            "fields_redacted": redacted_fields
        }

    # State schedules as spec-conformant statement nodes (not custom extensions)
    if states and isinstance(states, dict):
        state_grids = states.get("state_grids", []) or []
        state_tax_summary = states.get("state_tax_summary", []) or []
        activity_schedule = states.get("activity_schedule", {}) or {}

        next_seq = len(doc["statements"]) + 1

        # Per-grid statement (one per state_grid type — ECI, UBTI, etc.)
        for grid in state_grids:
            doc["statements"].append({
                "type": "statement",
                "semantic": {
                    "id": f"state_grid_{grid.get('grid_type', 'unknown')}",
                    "label": f"State Grid — {grid.get('grid_type', 'unknown').upper()}",
                    "classification": "state_apportionment",
                    "role": "investor_footnote"
                },
                "form": {"attachment": True, "attachment_sequence": next_seq},
                "cross_references": [],
                "content": {
                    "grid_type": grid.get("grid_type"),
                    "page": grid.get("page"),
                    "jurisdictions": grid.get("jurisdictions", [])
                },
                "source_text": grid.get("source_text", ""),
                "confidence": 0.85,
                "extraction_method": "ai_structured"
            })
            next_seq += 1

        # Aggregate state tax summary as a single statement
        if state_tax_summary:
            doc["statements"].append({
                "type": "statement",
                "semantic": {
                    "id": "state_tax_summary",
                    "label": "State Tax Summary (Source Income, Withholding, Composite)",
                    "classification": "state_apportionment",
                    "role": "investor_footnote"
                },
                "form": {"attachment": True, "attachment_sequence": next_seq},
                "cross_references": [],
                "content": {"jurisdictions": state_tax_summary},
                "source_text": "",
                "confidence": 0.85,
                "extraction_method": "ai_structured"
            })
            next_seq += 1

        # Activity schedule retained as supplemental until OTD adopts a canonical class
        if activity_schedule.get("activities"):
            doc["statements"].append({
                "type": "statement",
                "semantic": {
                    "id": "activity_schedule",
                    "label": "Schedule of Activities",
                    "classification": "custom",
                    "role": "investor_footnote"
                },
                "form": {"attachment": True, "attachment_sequence": next_seq},
                "cross_references": [],
                "content": {
                    "custom_classification": "activity_schedule",
                    "activities": activity_schedule.get("activities", [])
                },
                "source_text": "",
                "confidence": 0.80,
                "extraction_method": "ai_structured"
            })

    # ── Write OTD YAML ────────────────────────────────────────────────────
    # ── Write OTD YAML ────────────────────────────────────────────────────
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        _yaml.dump(doc, f)
    print(f"WROTE {out}")

    # ── Confidence manifest ───────────────────────────────────────────────
    uv_count, uv_paths = count_unverified(doc)
    total = count_leaves(doc)
    conf = out.parent / "output.confidence.json"
    with open(conf, "w", encoding="utf-8") as f:
        json.dump({
            "document_id": run_id,
            "extraction_date": now,
            "total_fields": total,
            "unverified_count": uv_count,
            "unverified_fields": uv_paths[:50],
            "confidence_overall": round(max(0.0, 1.0 - uv_count / max(1, total)), 3),
            "adversary_recommendation": "PENDING"
        }, f, indent=2)
    print(f"WROTE {conf}")
    print(f"\nAssembly complete: {total} fields | {uv_count} unverified markers")


if __name__ == "__main__":
    main()
