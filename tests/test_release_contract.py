#!/usr/bin/env python
"""Release-boundary regressions. All documents and companions are synthetic."""
import copy
import hashlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from decimal import Decimal
from pathlib import Path

from ruamel.yaml import YAML

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "skills/k1-otd/scripts"
TAXONOMY = REPO_ROOT / "taxonomies/irs-k1-1065-2025.yaml"
PROOF = REPO_ROOT / "proof/proof-emitted.otd.yaml"
sys.path.insert(0, str(SCRIPTS))

import validate_otd
from constraint_engine import run_constraints


def yaml_text(value):
    stream = io.StringIO()
    YAML().dump(value, stream)
    return stream.getvalue()


def part3(document):
    return document["body"]["part_iii"]


def statement(document):
    return next(
        entry["statement"]
        for entry in part3(document)["box_20"]["entries"]
        if entry.get("code") == "Z"
    )


class ReleaseContract(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="otd-release-contract-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.document = YAML().load(PROOF.read_text(encoding="utf-8-sig"))
        self.taxonomy = YAML().load(TAXONOMY.read_text(encoding="utf-8-sig"))

    def invoke(self, document=None, taxonomy=None, confidence=None, update=False):
        document = self.document if document is None else document
        path = self.root / "input.otd.yaml"
        path.write_text(yaml_text(document), encoding="utf-8")
        tax_path = TAXONOMY
        if taxonomy is not None:
            tax_path = self.root / "taxonomy.yaml"
            tax_path.write_text(yaml_text(taxonomy), encoding="utf-8")
        companion = self.root / "output.confidence.json"
        if confidence is not None:
            companion.write_text(confidence, encoding="utf-8")
        command = [
            sys.executable, "-B", str(SCRIPTS / "validate_otd.py"),
            "--input", str(path), "--taxonomy", str(tax_path),
        ]
        if update:
            command.extend(["--update-confidence", str(companion)])
        result = subprocess.run(
            command, cwd=REPO_ROOT, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=20,
        )
        return result, companion

    def expect(self, code, **kwargs):
        result, companion = self.invoke(**kwargs)
        self.assertEqual(
            result.returncode, code,
            "STDOUT:\n%s\nSTDERR:\n%s" % (result.stdout, result.stderr),
        )
        self.assertNotIn("Traceback", result.stdout + result.stderr)
        return result, companion

    def test_clean_control(self):
        self.expect(0)

    def test_coded_entries_must_be_sequence(self):
        part3(self.document)["box_11"]["entries"] = {}
        self.expect(1)

    def test_coded_value_must_be_explicit(self):
        part3(self.document)["box_11"]["entries"][0].pop("value")
        self.expect(1)

    def test_top_statements_must_be_sequence(self):
        self.document["statements"] = "not a statement sequence"
        self.expect(1)

    def test_top_statement_members_must_be_nodes(self):
        self.document["statements"] = [17]
        self.expect(1)

    def test_malformed_type_is_document_error(self):
        part3(self.document)["box_1"]["type"] = ["scalar"]
        self.expect(1)

    def test_malformed_semantic_is_document_error(self):
        statement(self.document)["semantic"] = ["invalid"]
        self.expect(1)

    def test_statement_declared_numeric_type_is_checked(self):
        statement(self.document)["content"]["qbi"] = "not a number"
        self.expect(1)

    def test_statement_declared_boolean_type_is_checked(self):
        statement(self.document)["content"]["sstb"] = "false"
        self.expect(1)

    def test_node_form_identity_is_bound(self):
        part3(self.document)["box_1"]["form"]["form_id"] = "not-k1"
        self.expect(1)

    def test_financial_nan_is_rejected(self):
        part3(self.document)["box_1"]["value"] = float("nan")
        self.expect(1)

    def test_financial_infinity_is_rejected(self):
        part3(self.document)["box_5"]["value"] = float("inf")
        self.expect(1)

    def test_empty_review_marker_is_rejected(self):
        part3(self.document)["box_1"].update(value=None, _unverified="")
        self.expect(1)

    def test_quarantined_null_control(self):
        part3(self.document)["box_1"].update(
            value=None, _unverified="Synthetic unobserved source field"
        )
        self.expect(0)

    def test_created_requires_timezone(self):
        self.document["otd"]["created"] = "2026-04-02T15:00:00"
        self.expect(1)

    def test_created_requires_time(self):
        self.document["otd"]["created"] = "2026-04-02"
        self.expect(1)

    def test_created_offset_control(self):
        self.document["otd"]["created"] = "2026-04-02T15:00:00+01:00"
        self.expect(0)

    def test_unknown_extension_is_preserved(self):
        self.document["body"]["extension_example"] = {
            "type": "future_extension", "payload": {"reported": 7}
        }
        self.expect(0)

    def test_cyclic_yaml_is_document_error(self):
        cycle = []
        cycle.append(cycle)
        self.document["extension_example"] = cycle
        self.expect(1)

    def test_range_syntax_is_preflighted(self):
        next(c for c in self.taxonomy["constraints"]
             if c.get("type") == "range")["rule"] = "value < ("
        self.expect(2, taxonomy=self.taxonomy)

    def test_range_syntax_cannot_hide_behind_quarantine(self):
        next(c for c in self.taxonomy["constraints"]
             if c.get("type") == "range")["rule"] = "value < ("
        part3(self.document)["box_6b"].update(
            value=None, _unverified="Synthetic unobserved value"
        )
        self.expect(2, taxonomy=self.taxonomy)

    def test_python_constructs_are_not_taxonomy_rules(self):
        # Harmless expressions only: no filesystem, network, or shell access.
        for expression in (
            "value >= 0 or ().__class__",
            "value >= 0 and (lambda: 1)()",
            "value >= 0 and [1][0]",
            "value >= 0 and 2 ** 16",
        ):
            with self.subTest(expression=expression):
                taxonomy = copy.deepcopy(self.taxonomy)
                next(c for c in taxonomy["constraints"]
                     if c.get("type") == "range")["rule"] = expression
                self.expect(2, taxonomy=taxonomy)

    def test_validation_does_not_write_adjacent_confidence(self):
        original = json.dumps({
            "document_id": "a-different-document",
            "validation_passes": False,
            "unrelated_evidence": "must remain untouched",
        }, indent=2)
        _, companion = self.expect(0, confidence=original)
        self.assertEqual(companion.read_text(encoding="utf-8"), original)

    def test_malformed_adjacent_confidence_does_not_affect_validation(self):
        original = "{ malformed JSON\n"
        _, companion = self.expect(0, confidence=original)
        self.assertEqual(companion.read_text(encoding="utf-8"), original)

    def test_explicit_confidence_update_binds_document_id(self):
        original = json.dumps({
            "document_id": self.document["otd"]["document_id"],
            "retained_field": "preserve me",
        })
        _, companion = self.expect(0, confidence=original, update=True)
        updated = json.loads(companion.read_text(encoding="utf-8"))
        self.assertIs(updated["validation_passes"], True)
        self.assertEqual(updated["validation_errors"], [])
        self.assertEqual(updated["retained_field"], "preserve me")
        self.assertEqual(
            updated["validated_document_sha256"],
            hashlib.sha256((self.root / "input.otd.yaml").read_bytes()).hexdigest(),
        )

    def test_confidence_update_preserves_unrelated_exact_numbers(self):
        original = (
            '{"document_id": ' + json.dumps(self.document["otd"]["document_id"])
            + ', "retained_amount": 9007199254740992.01}'
        )
        _, companion = self.expect(0, confidence=original, update=True)
        updated = json.loads(companion.read_text(encoding="utf-8"), parse_float=Decimal)
        self.assertEqual(updated["retained_amount"], Decimal("9007199254740992.01"))

    def test_explicit_confidence_update_rejects_other_document(self):
        original = '{"document_id": "different-document", "retained": true}'
        _, companion = self.expect(2, confidence=original, update=True)
        self.assertEqual(companion.read_text(encoding="utf-8"), original)

    def test_changed_input_cannot_receive_passing_confidence_receipt(self):
        path = self.root / "input.otd.yaml"
        original_input = yaml_text(self.document).encode("utf-8")
        path.write_bytes(original_input)
        companion = self.root / "output.confidence.json"
        original_companion = json.dumps({
            "document_id": self.document["otd"]["document_id"],
            "retained_field": "unchanged",
        }).encode("utf-8")
        companion.write_bytes(original_companion)
        original_validation = validate_otd.run_taxonomy_driven_validation

        def mutate_after_validation(*args, **kwargs):
            result = original_validation(*args, **kwargs)
            path.write_bytes(original_input + b"\n# synthetic concurrent edit\n")
            return result

        with mock.patch.object(
            validate_otd, "run_taxonomy_driven_validation",
            side_effect=mutate_after_validation,
        ):
            with self.assertRaisesRegex(
                validate_otd.ValidatorConfigurationError, "input changed"
            ):
                validate_otd.validate(path, TAXONOMY, confidence_path=companion)
        self.assertEqual(companion.read_bytes(), original_companion)
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_readonly_result_identifies_exact_input_bytes(self):
        path = self.root / "input.otd.yaml"
        original = yaml_text(self.document).encode("utf-8")
        path.write_bytes(original)
        result = validate_otd.validate(path, TAXONOMY)
        self.assertTrue(result["passes"])
        self.assertEqual(
            result["validated_document_sha256"],
            hashlib.sha256(original).hexdigest(),
        )
        self.assertEqual(path.read_bytes(), original)

    def test_exact_yaml_decimal_ingress(self):
        parsed = validate_otd._yaml.load("value: 9007199254740992.01\n")
        self.assertIsInstance(parsed["value"], Decimal)
        self.assertEqual(parsed["value"], Decimal("9007199254740992.01"))

    def test_one_cent_tolerance_is_inclusive(self):
        body = {"part_iii": {
            "box_4a": {"type": "scalar", "value": 0.1},
            "box_4b": {"type": "scalar", "value": 0.2},
            "box_4c": {"type": "scalar", "value": 0.29},
        }}
        rules = [{
            "id": "one-cent", "type": "sum", "target": "part_iii.box_4c",
            "operands": ["part_iii.box_4a", "part_iii.box_4b"],
            "tolerance": 0.01, "severity": "error",
        }]
        self.assertEqual(run_constraints(body, rules, self.taxonomy), [])
        body["part_iii"]["box_4c"]["value"] = 0.28
        self.assertTrue(run_constraints(body, rules, self.taxonomy))

    def test_exact_large_decimal_arithmetic(self):
        body = {"part_iii": {
            "box_4a": {"type": "scalar", "value": Decimal("9007199254740992.01")},
            "box_4b": {"type": "scalar", "value": Decimal("0.02")},
            "box_4c": {"type": "scalar", "value": Decimal("9007199254740992.05")},
        }}
        rules = [{
            "id": "large-exact", "type": "sum", "target": "part_iii.box_4c",
            "operands": ["part_iii.box_4a", "part_iii.box_4b"],
            "tolerance": Decimal("0.01"), "severity": "error",
        }]
        self.assertTrue(run_constraints(body, rules, self.taxonomy))


if __name__ == "__main__":
    unittest.main(verbosity=2)
