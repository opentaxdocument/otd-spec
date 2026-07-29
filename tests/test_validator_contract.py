#!/usr/bin/env python
"""Portable validator contract matrix with strict deferred-case accounting.

Blocking cases describe the currently authorized trust-boundary repair.
Deferred cases retain the desired contract but are strict expected failures:
an unexpected pass fails this harness so the case must be promoted rather
than silently disappearing from the debt ledger.
"""

import copy
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

try:
    from ruamel.yaml import YAML
except ImportError:
    print("ERROR: install ruamel.yaml", file=sys.stderr)
    sys.exit(2)


REPO_ROOT = Path(__file__).resolve().parent.parent
VALIDATOR = REPO_ROOT / "skills/k1-otd/scripts/validate_otd.py"
BASELINE_PATH = REPO_ROOT / "proof/proof-emitted.otd.yaml"
TAXONOMY_PATH = REPO_ROOT / "taxonomies/irs-k1-1065-2025.yaml"

EXIT_CONFORMANT = 0
EXIT_INVALID_DOCUMENT = 1
EXIT_VALIDATOR_ERROR = 2

_yaml = YAML()
_yaml.indent(mapping=2, sequence=4, offset=2)
_yaml.default_flow_style = False


def load_yaml(path):
    with path.open("r", encoding="utf-8") as stream:
        return _yaml.load(stream)


def save_yaml(path, value):
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        _yaml.dump(value, stream)


def constraint(taxonomy, constraint_id):
    for item in taxonomy.get("constraints") or []:
        if isinstance(item, dict) and item.get("id") == constraint_id:
            return item
    raise KeyError("constraint not found: " + constraint_id)


def first_statement(node):
    if isinstance(node, dict):
        if node.get("type") == "statement":
            return node
        for value in node.values():
            found = first_statement(value)
            if found is not None:
                return found
    elif isinstance(node, list):
        for value in node:
            found = first_statement(value)
            if found is not None:
                return found
    return None


def add_case(
    cases,
    name,
    rationale,
    desired_rc,
    mutate_doc=None,
    mutate_taxonomy=None,
    taxonomy_mode="copy",
    deferred_current_rc=None,
):
    cases.append(
        {
            "name": name,
            "rationale": rationale,
            "desired_rc": desired_rc,
            "mutate_doc": mutate_doc,
            "mutate_taxonomy": mutate_taxonomy,
            "taxonomy_mode": taxonomy_mode,
            "deferred_current_rc": deferred_current_rc,
        }
    )


def build_cases():
    cases = []

    add_case(
        cases,
        "control_valid_baseline",
        "Known-good proof document validates against the canonical taxonomy.",
        EXIT_CONFORMANT,
    )
    add_case(
        cases,
        "missing_otd_envelope",
        "A missing otd envelope is an invalid document.",
        EXIT_INVALID_DOCUMENT,
        lambda document: document.pop("otd", None),
    )
    add_case(
        cases,
        "missing_form_metadata",
        "The K-1 document contract requires form_metadata.",
        EXIT_INVALID_DOCUMENT,
        lambda document: document.pop("form_metadata", None),
    )
    add_case(
        cases,
        "missing_body_form_id",
        "The document body must identify its physical form.",
        EXIT_INVALID_DOCUMENT,
        lambda document: document["body"].pop("form_id", None),
    )
    add_case(
        cases,
        "body_not_mapping",
        "A malformed body is an invalid document, not a validator crash.",
        EXIT_INVALID_DOCUMENT,
        lambda document: document.__setitem__("body", []),
    )
    add_case(
        cases,
        "part_i_not_mapping",
        "A declared K-1 part must be a structured mapping.",
        EXIT_INVALID_DOCUMENT,
        lambda document: document["body"].__setitem__("part_i", []),
    )
    add_case(
        cases,
        "filing_status_not_string",
        "Malformed metadata must be rejected without crashing enum validation.",
        EXIT_INVALID_DOCUMENT,
        lambda document: document["form_metadata"].__setitem__(
            "filing_status", []
        ),
    )
    add_case(
        cases,
        "created_not_iso_datetime",
        "The required creation timestamp must be an ISO 8601 datetime.",
        EXIT_INVALID_DOCUMENT,
        lambda document: document["otd"].__setitem__(
            "created", "not-a-datetime"
        ),
    )
    add_case(
        cases,
        "supersedes_document_id_wrong_type",
        "An amendment-chain identifier must be a string or null.",
        EXIT_INVALID_DOCUMENT,
        lambda document: document["form_metadata"].__setitem__(
            "supersedes_document_id", ["bad"]
        ),
    )
    add_case(
        cases,
        "explicit_missing_taxonomy_file",
        "A missing governing taxonomy is a validator configuration failure.",
        EXIT_VALIDATOR_ERROR,
        taxonomy_mode="missing",
    )
    add_case(
        cases,
        "empty_taxonomy_document",
        "An empty mapping cannot govern validation.",
        EXIT_VALIDATOR_ERROR,
        taxonomy_mode="empty",
    )
    add_case(
        cases,
        "malformed_taxonomy_document",
        "Malformed taxonomy YAML is a validator configuration failure.",
        EXIT_VALIDATOR_ERROR,
        taxonomy_mode="malformed",
    )
    add_case(
        cases,
        "taxonomy_missing_nodes",
        "A taxonomy without a node schema has validated no document structure.",
        EXIT_VALIDATOR_ERROR,
        mutate_taxonomy=lambda taxonomy: taxonomy.pop("nodes", None),
    )
    add_case(
        cases,
        "taxonomy_empty_nodes",
        "An empty node schema cannot certify document conformance.",
        EXIT_VALIDATOR_ERROR,
        mutate_taxonomy=lambda taxonomy: taxonomy.__setitem__("nodes", {}),
    )
    add_case(
        cases,
        "taxonomy_missing_constraints",
        "The K-1 validator must not silently discard its governing rules.",
        EXIT_VALIDATOR_ERROR,
        mutate_taxonomy=lambda taxonomy: taxonomy.pop("constraints", None),
    )

    def retain_taxonomy_identity_only(taxonomy):
        for key in list(taxonomy):
            if key != "taxonomy":
                taxonomy.pop(key)

    add_case(
        cases,
        "taxonomy_identity_only",
        "Identity metadata alone is not an operative governing taxonomy.",
        EXIT_VALIDATOR_ERROR,
        mutate_taxonomy=retain_taxonomy_identity_only,
    )

    add_case(
        cases,
        "sum_rule_typo_target",
        "A sum target outside the taxonomy must fail taxonomy compilation.",
        EXIT_VALIDATOR_ERROR,
        mutate_taxonomy=lambda taxonomy: constraint(
            taxonomy, "box_4c_equals_4a_plus_4b"
        ).__setitem__("target", "part_iii.box_4c_typo"),
    )
    add_case(
        cases,
        "range_rule_typo_target",
        "A range target outside the taxonomy must fail taxonomy compilation.",
        EXIT_VALIDATOR_ERROR,
        mutate_taxonomy=lambda taxonomy: constraint(
            taxonomy, "box_6b_lte_6a"
        ).__setitem__("target", "part_iii.box_6b_typo"),
    )
    add_case(
        cases,
        "conditional_rule_typo_predicate",
        "An undeclared condition path must fail taxonomy compilation.",
        EXIT_VALIDATOR_ERROR,
        mutate_taxonomy=lambda taxonomy: constraint(
            taxonomy, "item_k3_requires_box20_x_statement"
        ).__setitem__("condition", "part_ii.item_k3_typo.value == true"),
    )

    def break_sum_operand_document(document):
        part_iii = document["body"]["part_iii"]
        part_iii["box_4c"]["value"] = part_iii["box_4a"]["value"]

    def break_sum_operand_taxonomy(taxonomy):
        rule = constraint(taxonomy, "box_4c_equals_4a_plus_4b")
        rule["operands"][1] = "part_iii.box_4b_typo"

    add_case(
        cases,
        "sum_rule_typo_operand_accepts_bad_math",
        "An undeclared operand must fail compilation, not become zero.",
        EXIT_VALIDATOR_ERROR,
        break_sum_operand_document,
        break_sum_operand_taxonomy,
    )

    def unverified_false(document):
        node = document["body"]["part_ii"]["item_k2"]
        node["value"] = False
        node["_unverified"] = "checkbox state was not observed"

    add_case(
        cases,
        "unverified_false_fabrication",
        "An unobserved fact must be null rather than a plausible false value.",
        EXIT_INVALID_DOCUMENT,
        unverified_false,
    )
    add_case(
        cases,
        "enum_value_outside_taxonomy",
        "A closed taxonomy enum must reject an undeclared value.",
        EXIT_INVALID_DOCUMENT,
        lambda document: document["body"]["part_ii"]["item_g"].__setitem__(
            "value", "definitely_not_a_partner_type"
        ),
    )
    add_case(
        cases,
        "nested_object_wrong_field_type",
        "A declared percentage field must reject a string.",
        EXIT_INVALID_DOCUMENT,
        lambda document: document["body"]["part_ii"]["item_j"]["value"].__setitem__(
            "profit_beginning", "not-a-percentage"
        ),
    )

    def invalid_form_metadata(document):
        metadata = document["form_metadata"]
        metadata["tax_year"] = "twenty-twenty-five"
        metadata["fiscal_year"] = True
        metadata["fiscal_year_begin"] = "not-an-iso-date"
        metadata["fiscal_year_end"] = "still-not-a-date"

    add_case(
        cases,
        "invalid_form_metadata_types",
        "Basic legal and administrative metadata types are mandatory.",
        EXIT_INVALID_DOCUMENT,
        invalid_form_metadata,
    )
    add_case(
        cases,
        "known_node_missing_box_placement",
        "A declared node must bind to its taxonomy form placement.",
        EXIT_INVALID_DOCUMENT,
        lambda document: document["body"]["part_iii"]["box_1"]["form"].pop(
            "box", None
        ),
    )
    add_case(
        cases,
        "document_taxonomy_identity_mismatch",
        "The document taxonomy claim must match the supplied taxonomy.",
        EXIT_INVALID_DOCUMENT,
        lambda document: document["otd"]["taxonomy"].__setitem__(
            "id", "irs-k1-1065-2019"
        ),
    )
    add_case(
        cases,
        "form_year_taxonomy_mismatch",
        "A 2019 document cannot validate against the 2025 taxonomy.",
        EXIT_INVALID_DOCUMENT,
        lambda document: document["form_metadata"].__setitem__("tax_year", 2019),
    )

    def add_unknown_extension(document):
        document["body"]["future_extension"] = {
            "type": "future_taxnode",
            "semantic": {"id": "future.extension", "label": "Future Extension"},
            "form": {"form_id": "future-form", "location": "Extension"},
            "payload": {"preserve": True},
        }

    add_case(
        cases,
        "unknown_extension_type_forward_compatibility",
        "Undeclared extensions must be preserved and reported, not rejected.",
        EXIT_CONFORMANT,
        add_unknown_extension,
    )

    def add_unknown_statement_classification(document):
        document.setdefault("statements", []).append(
            {
                "type": "statement",
                "semantic": {
                    "id": "stmt_unknown_classification",
                    "label": "Unknown Classification",
                    "classification": "not_in_the_declared_catalog",
                    "role": "investor_footnote",
                },
                "form": {"attachment": True},
                "content": {"detail": "synthetic probe"},
            }
        )

    add_case(
        cases,
        "unknown_statement_classification",
        "Statement classifications must bind to the canonical catalog or custom.",
        EXIT_INVALID_DOCUMENT,
        add_unknown_statement_classification,
    )

    def add_unqualified_custom_statement(document):
        document.setdefault("statements", []).append(
            {
                "type": "statement",
                "semantic": {
                    "id": "stmt_unqualified_custom",
                    "label": "Unqualified Custom Statement",
                    "classification": "custom",
                    "role": "investor_footnote",
                },
                "form": {"attachment": True},
                "content": {"detail": "missing custom classification"},
            }
        )

    add_case(
        cases,
        "custom_statement_missing_custom_classification",
        "Custom statements require a non-empty content.custom_classification.",
        EXIT_INVALID_DOCUMENT,
        add_unqualified_custom_statement,
    )

    def add_unmarked_review_statement(document):
        document.setdefault("statements", []).append(
            {
                "type": "statement",
                "semantic": {
                    "id": "stmt_unmarked_review",
                    "label": "Unmarked Review Statement",
                    "classification": "unclassified_requires_review",
                    "role": "investor_footnote",
                },
                "form": {"attachment": True},
                "content": {"detail": "missing review marker"},
            }
        )

    add_case(
        cases,
        "unclassified_statement_missing_unverified",
        "Review-required statements must carry a non-empty _unverified marker.",
        EXIT_INVALID_DOCUMENT,
        add_unmarked_review_statement,
    )

    def remove_statement_form(document):
        statement = first_statement(document)
        if statement is None:
            raise RuntimeError("baseline has no statement")
        statement.pop("form", None)

    add_case(
        cases,
        "statement_missing_form_attachment",
        "Statement nodes require form.attachment.",
        EXIT_INVALID_DOCUMENT,
        remove_statement_form,
    )

    def unchecked_with_target(document):
        node = document["body"]["part_iii"]["box_16"]
        node["checked"] = False
        node["target"] = {
            "document_type": "otd",
            "taxonomy_id": "irs-k3-1065-2025",
            "document_id": "contradictory-target",
            "node_path": "/",
        }

    add_case(
        cases,
        "unchecked_box16_with_reference_target",
        "Unchecked Box 16 cannot assert a furnished K-3 target.",
        EXIT_INVALID_DOCUMENT,
        unchecked_with_target,
    )

    return cases


def run_case(spec, baseline, canonical_taxonomy, root):
    document = copy.deepcopy(baseline)
    taxonomy = copy.deepcopy(canonical_taxonomy)

    if spec["mutate_doc"] is not None:
        spec["mutate_doc"](document)
    if spec["mutate_taxonomy"] is not None:
        spec["mutate_taxonomy"](taxonomy)

    document_path = root / "case.otd.yaml"
    save_yaml(document_path, document)

    taxonomy_mode = spec["taxonomy_mode"]
    if taxonomy_mode == "missing":
        taxonomy_path = root / "intentionally-missing-taxonomy.yaml"
    elif taxonomy_mode == "empty":
        taxonomy_path = root / "empty-taxonomy.yaml"
        save_yaml(taxonomy_path, {})
    elif taxonomy_mode == "malformed":
        taxonomy_path = root / "malformed-taxonomy.yaml"
        taxonomy_path.write_text(
            "taxonomy: [unterminated\n", encoding="utf-8", newline="\n"
        )
    else:
        taxonomy_path = root / "taxonomy.yaml"
        save_yaml(taxonomy_path, taxonomy)

    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONUTF8"] = "1"
    environment["PYTHONIOENCODING"] = "utf-8"

    completed = subprocess.run(
        [
            sys.executable,
            "-B",
            str(VALIDATOR),
            "--input",
            str(document_path),
            "--taxonomy",
            str(taxonomy_path),
        ],
        cwd=str(root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        env=environment,
        check=False,
    )
    diagnostics = [
        line.strip()
        for line in (completed.stdout + "\n" + completed.stderr).splitlines()
        if "ERROR:" in line or "WARN:" in line
    ]
    return completed.returncode, diagnostics[:5]


def main():
    baseline = load_yaml(BASELINE_PATH)
    canonical_taxonomy = load_yaml(TAXONOMY_PATH)
    cases = build_cases()
    failures = []
    blocking_count = 0
    deferred_count = 0

    print("=== OTD Validator Contract Matrix ===")
    with tempfile.TemporaryDirectory() as temp_directory:
        temp_root = Path(temp_directory)
        for spec in cases:
            case_root = temp_root / spec["name"]
            case_root.mkdir()
            actual_rc, diagnostics = run_case(
                spec, baseline, canonical_taxonomy, case_root
            )
            deferred_rc = spec["deferred_current_rc"]

            if deferred_rc is None:
                blocking_count += 1
                if actual_rc == spec["desired_rc"]:
                    print(
                        f"  PASS   [{spec['name']}] rc={actual_rc} "
                        f"— {spec['rationale']}"
                    )
                else:
                    failures.append(spec["name"])
                    print(
                        f"  FAIL   [{spec['name']}] rc={actual_rc}, "
                        f"expected={spec['desired_rc']}"
                    )
            else:
                deferred_count += 1
                if actual_rc == deferred_rc:
                    print(
                        f"  XFAIL  [{spec['name']}] rc={actual_rc} "
                        f"— deferred contract debt retained"
                    )
                elif actual_rc == spec["desired_rc"]:
                    failures.append(spec["name"])
                    print(
                        f"  XPASS  [{spec['name']}] desired behavior now passes; "
                        "promote this case out of deferred status"
                    )
                else:
                    failures.append(spec["name"])
                    print(
                        f"  FAIL   [{spec['name']}] rc={actual_rc}, "
                        f"expected deferred={deferred_rc} or desired={spec['desired_rc']}"
                    )

            if failures and failures[-1] == spec["name"] and diagnostics:
                for diagnostic in diagnostics:
                    print(f"         {diagnostic}")

    print(
        f"\n{len(cases)} cases: {blocking_count} blocking, "
        f"{deferred_count} strict deferred."
    )
    if failures:
        print("=== FAILURES ===")
        for name in failures:
            print(f"  {name}")
        return 1

    print("ALL 33 CONTRACT CASES PASSED; NO DEFERRED CASES REMAIN.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
