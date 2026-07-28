
#!/usr/bin/env python
"""K-1 OTD Extraction Skill — OTD Validation
Checks structural completeness, node type validity, unverified markers, and capital math.
"""
import sys
import json
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from constraint_engine import run_taxonomy_driven_validation  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

try:
    from ruamel.yaml import YAML
    _yaml = YAML()
except ImportError:
    print("ERROR: pip install ruamel.yaml", file=sys.stderr)
    sys.exit(1)

REQUIRED = {
    "otd":           ["version", "document_id", "created", "producer", "taxonomy"],
    "form_metadata": ["tax_year", "fiscal_year", "amended", "final", "filing_status"],
    "body":          ["form_id", "part_i", "part_ii", "part_iii"],
}
VALID_TYPES = {"scalar", "coded", "grid", "recordset", "statement", "reference"}
VALID_FILING_STATUS = {"original", "amended", "superseded", "void"}


def validate_filing_status(form_metadata, errors, warnings):
    """Enforce spec enum + amended-implies-supersedes_document_id rule."""
    if not form_metadata:
        return
    fs = form_metadata.get("filing_status")
    if fs is not None and fs not in VALID_FILING_STATUS:
        errors.append(
            f"form_metadata.filing_status = '{fs}' is not in spec enum "
            f"{sorted(VALID_FILING_STATUS)}"
        )
    if fs in ("amended", "superseded") and not form_metadata.get("supersedes_document_id"):
        warnings.append(
            f"⚠️ form_metadata.filing_status is '{fs}' but supersedes_document_id is null "
            "(spec recommends setting the prior document ID)"
        )
    if form_metadata.get("fiscal_year"):
        if not form_metadata.get("fiscal_year_begin"):
            warnings.append("⚠️ fiscal_year=true but fiscal_year_begin is null")
        if not form_metadata.get("fiscal_year_end"):
            warnings.append("⚠️ fiscal_year=true but fiscal_year_end is null")


def find_unverified(obj, path=""):
    found = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "_unverified":
                found.append(path)
            else:
                found.extend(find_unverified(v, f"{path}.{k}" if path else k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            found.extend(find_unverified(v, f"{path}[{i}]"))
    return found


def find_statements(obj):
    stmts = []
    if isinstance(obj, dict):
        if obj.get("type") == "statement":
            stmts.append(obj)
        for k, v in obj.items():
            stmts.extend(find_statements(v))
    elif isinstance(obj, list):
        for v in obj:
            stmts.extend(find_statements(v))
    return stmts

def check_types(obj, path, errors):
    if isinstance(obj, dict):
        t = obj.get("type")
        if t and t not in VALID_TYPES:
            errors.append(f"Invalid node type '{t}' at {path}")
        for k, v in obj.items():
            check_types(v, f"{path}.{k}" if path else k, errors)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            check_types(v, f"{path}[{i}]", errors)


def load_taxonomy(taxonomy_path):
    """Load the taxonomy YAML that governs conditional/statement rules.
    Falls back gracefully (empty rule set, not a crash) if unavailable so
    structural checks still run even when the taxonomy path cannot be
    resolved in a given invocation context."""
    try:
        with open(taxonomy_path, encoding="utf-8") as f:
            return _yaml.load(f) or {}
    except (FileNotFoundError, OSError):
        return {}


def validate(input_path, taxonomy_path=None):
    with open(input_path, encoding="utf-8") as f:
        doc = _yaml.load(f)

    errors, warnings = [], []
    # Node type validity
    check_types(doc.get("body", {}), "body", errors)

    # form_metadata enum + amendment-chain rules
    validate_filing_status(doc.get("form_metadata", {}), errors, warnings)

    # Taxonomy-driven constraint and statement-schema enforcement.
    # Reads constraints and statement_classification/statement_schema
    # declarations directly from the taxonomy YAML — a new rule added
    # there is enforced automatically, with no new Python required.

    if taxonomy_path is None:
        candidate = Path(input_path).resolve()
        for ancestor in [candidate.parent] + list(candidate.parents):
            probe = ancestor / "taxonomies" / "irs-k1-1065-2025.yaml"
            if probe.exists():
                taxonomy_path = str(probe)
                break

    # Script-relative fallback. A document being validated outside the repo
    # (an artifact directory, a customer file, a temp path) has no repo
    # ancestor, so the walk above finds nothing. The canonical mirror ships
    # beside this script; the assembler already resolves it the same way.
    if taxonomy_path is None:
        mirror = Path(__file__).resolve().parent.parent / "reference" / "irs-k1-1065-2025.yaml"
        if mirror.exists():
            taxonomy_path = str(mirror)

    # Fail closed. Previously an unresolved taxonomy degraded to a warning and
    # the document still exited 0 with "rules were not enforced" -- the same
    # fail-open shape as the range-rule `except: return True` removed in Wave 10.
    # A validator that cannot load its schema has not validated anything.
    if not taxonomy_path:
        errors.append(
            "Taxonomy could not be resolved; conditional, statement, binding and "
            "completeness rules were NOT enforced. Pass --taxonomy explicitly.")
    else:
        taxonomy = load_taxonomy(taxonomy_path)
        tax_errors, tax_warnings = run_taxonomy_driven_validation(
            doc.get("body", {}), taxonomy, doc.get("statements"))
        errors.extend(tax_errors)
        warnings.extend(tax_warnings)

    # Unverified markers
    uv = find_unverified(doc)
    for path in uv:
        warnings.append(f"⚠️ HUMAN REVIEW at: {path}")

    # Capital account integrity (completeness, alias ambiguity, and continuity)
    # is enforced generically by constraint_engine.validate_capital_account.
    # The previous inline check filtered its expected field list down to fields
    # already present -- so an incomplete capital account always passed -- and
    # summed both current-year aliases when both appeared, producing a false
    # continuity figure. Both defects are fixed in the engine.

    # Unclassified statements
    all_stmts = find_statements(doc)
    for stmt in all_stmts:
        if stmt.get("semantic", {}).get("classification") == "unclassified_requires_review":
            warnings.append(f"⚠️ Unclassified statement: {stmt.get('semantic', {}).get('id', 'unknown')}")

    result = {
        "passes": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "unverified_count": len(uv),
        "unverified_paths": uv[:20],
        "statement_count": len(all_stmts)
    }

    status = "PASS" if result["passes"] else "FAIL"
    print(f"\n=== OTD Validation: {status} ===")
    print(f"Errors: {len(errors)} | Warnings: {len(warnings)} | "
          f"Unverified: {len(uv)} | Statements: {result['statement_count']}")
    for e in errors:
        print(f"  ERROR: {e}")
    for w in warnings[:15]:
        print(f"  WARN:  {w}")
    if len(warnings) > 15:
        print(f"  ... +{len(warnings) - 15} more warnings")

    # Update confidence manifest if present
    conf_path = Path(input_path).parent / "output.confidence.json"
    if conf_path.exists():
        with open(conf_path, encoding="utf-8") as f:
            conf = json.load(f)
        conf["validation_passes"] = result["passes"]
        conf["validation_errors"] = errors
        with open(conf_path, "w", encoding="utf-8") as f:
            json.dump(conf, f, indent=2)

    return result


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="OTD structural validation")
    p.add_argument("--input", required=True, help="Path to output.otd.yaml")
    p.add_argument("--taxonomy", default=None, help="Path to governing taxonomy YAML (auto-detected if omitted)")
    args = p.parse_args()
    r = validate(args.input, taxonomy_path=args.taxonomy)
    sys.exit(0 if r["passes"] else 1)
