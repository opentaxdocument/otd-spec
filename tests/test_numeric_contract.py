#!/usr/bin/env python
"""Exact-number contracts for the bounded reference pipeline; synthetic data only."""
import copy
import importlib.util
import io
import subprocess
import sys
import tempfile
import unittest
from decimal import Decimal, localcontext
from pathlib import Path

import simplejson
from ruamel.yaml import YAML

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "skills/k1-otd/scripts"
sys.path.insert(0, str(SCRIPTS))

import assemble_otd
import build_line_item_details
import face_reader
import state_grid_parsers
from otd_values import (
    as_decimal, currency_value, decimal_delta, decimal_sum, decimal_yaml,
    is_numeric, normalize_k1_numbers, percentage_fraction,
)
from reconcile_line_item_details import reconcile

PROOF_SPEC = importlib.util.spec_from_file_location(
    "numeric_contract_proof", REPO_ROOT / "proof/otd_round_trip_proof.py"
)
proof = importlib.util.module_from_spec(PROOF_SPEC)
PROOF_SPEC.loader.exec_module(proof)


class NumericContract(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="otd-numeric-contract-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def assemble_amount(self, amount):
        # Reuse only the synthetic fixture's fragment shape. Do not claim
        # source-PDF evidence for these deliberately altered numeric facts.
        template = (
            REPO_ROOT / "examples/k1-1065-2025-synthetic"
            / "demonstration/fragments/face_page.json"
        )
        original = simplejson.loads(template.read_text(encoding="utf-8-sig"),
                                    use_decimal=True)
        face = {key: value for key, value in original.items()
                if not key.startswith("_")}
        face["part_i"]["partnership_name"] = "SYNTHETIC PRECISION PARTNERSHIP"
        face["part_ii"]["partner_name"] = "SYNTHETIC PRECISION PARTNER"
        face["part_iii_face"]["box_1"] = amount
        face["part_ii"]["share_percentages"]["profit_beginning"] = Decimal(
            "0.123456789012345678901234567890"
        )
        fragments = self.root / "fragments"
        fragments.mkdir()
        for name, value in {
            "face_page.json": face,
            "overflow_statements.json": {"part_iii_overflow": {}},
            "footnotes_a.json": [],
            "footnotes_b.json": [],
            "state_schedules.json": {},
        }.items():
            (fragments / name).write_text(
                simplejson.dumps(value, use_decimal=True, allow_nan=False),
                encoding="utf-8",
            )
        output = self.root / "output.otd.yaml"
        result = subprocess.run(
            [sys.executable, "-B", str(SCRIPTS / "assemble_otd.py"),
             "--fragments", str(fragments), "--out", str(output),
             "--created", "2026-07-30T00:00:00Z",
             "--document-id", "synthetic-numeric-contract"],
            cwd=REPO_ROOT, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=30,
        )
        return result, output

    def test_assembler_cli_preserves_exact_currency_and_percentage(self):
        result, output = self.assemble_amount(Decimal("9007199254740992.01"))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        document = decimal_yaml().load(output.read_text(encoding="utf-8"))
        self.assertEqual(document["body"]["part_iii"]["box_1"]["value"],
                         Decimal("9007199254740992.01"))
        self.assertEqual(
            document["body"]["part_ii"]["item_j"]["value"]["profit_beginning"],
            Decimal("0.123456789012345678901234567890"),
        )
        self.assertIn(b"value: 9007199254740992.01", output.read_bytes())
        self.assertNotIn(b"\r\n", output.read_bytes())

    def test_assembler_cli_refuses_silent_fractional_cent_rounding(self):
        result, output = self.assemble_amount(Decimal("0.001"))
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("fractional cents", result.stdout + result.stderr)
        self.assertFalse(output.exists())

    def test_yaml_safe_and_roundtrip_preserve_large_decimal(self):
        for round_trip in (False, True):
            with self.subTest(round_trip=round_trip):
                codec = decimal_yaml(round_trip=round_trip)
                value = codec.load("amount: 9007199254740992.01\n")
                self.assertIsInstance(value["amount"], Decimal)
                self.assertEqual(value["amount"], Decimal("9007199254740992.01"))
                stream = io.StringIO()
                codec.dump(value, stream)
                self.assertIn("amount: 9007199254740992.01", stream.getvalue())

    def test_roundtrip_codec_preserves_comments_and_order(self):
        text = "# retained context\nz: 0.10  # cents\na: 0.20\n"
        codec = decimal_yaml(round_trip=True)
        value = codec.load(text)
        stream = io.StringIO()
        codec.dump(value, stream)
        output = stream.getvalue()
        self.assertIn("# retained context", output)
        self.assertIn("# cents", output)
        self.assertLess(output.index("z:"), output.index("a:"))
        self.assertEqual(value["z"], Decimal("0.10"))

    def test_codec_registration_does_not_change_other_yaml_loaders(self):
        decimal_yaml()
        decimal_yaml(round_trip=True)
        self.assertIsInstance(YAML(typ="safe").load("amount: 0.1\n")["amount"], float)

    def test_arithmetic_is_independent_of_callers_decimal_precision(self):
        with localcontext() as context:
            context.prec = 6
            left = Decimal("1234567890123456789012345678901234567890.01")
            total = decimal_sum((left, Decimal("0.02")))
            self.assertEqual(total, Decimal("1234567890123456789012345678901234567890.03"))
            self.assertEqual(decimal_delta(total, left), Decimal("0.02"))
            self.assertEqual(currency_value(left), left)

    def test_currency_has_two_places_without_silent_rounding(self):
        self.assertEqual(str(currency_value(10)), "10.00")
        self.assertEqual(str(currency_value(Decimal("0.1"))), "0.10")
        self.assertEqual(str(currency_value(Decimal("-0.00"))), "-0.00")
        with self.assertRaisesRegex(ValueError, "fractional cents"):
            currency_value(Decimal("0.001"))

    def test_percentages_keep_full_precision(self):
        with localcontext() as context:
            context.prec = 6
            self.assertEqual(
                percentage_fraction(Decimal("12.3456789012345678901234567890123456789")),
                Decimal("0.123456789012345678901234567890123456789"),
            )

    def test_booleans_nonfinite_and_excessive_numbers_are_not_money(self):
        for value in (True, False, Decimal("NaN"), Decimal("Infinity"), Decimal("1E1001")):
            with self.subTest(value=str(value)):
                self.assertFalse(is_numeric(value))
                with self.assertRaises(ValueError):
                    as_decimal(value)

    def test_printed_face_amount_stays_numeric_through_json_and_assembly_load(self):
        amount = face_reader.parse_numeric("9,007,199,254,740,992.01")
        self.assertEqual(amount, Decimal("9007199254740992.01"))
        payload = simplejson.dumps({"value": amount}, use_decimal=True, allow_nan=False)
        self.assertIn('"value": 9007199254740992.01', payload)
        path = self.root / "fragment.json"
        path.write_text(payload, encoding="utf-8")
        value = assemble_otd.load(path)
        self.assertIsInstance(value["value"], Decimal)
        self.assertEqual(value["value"], amount)

    def test_parenthesized_face_and_state_values_remain_exact(self):
        expected = Decimal("-123456789012345678901234567890.01")
        for parser in (
            face_reader.parse_numeric,
            face_reader._parse_overlay_number,
            state_grid_parsers._parse_number,
        ):
            with self.subTest(parser=parser.__name__):
                self.assertEqual(parser("(123456789012345678901234567890.01)"), expected)

    def test_detail_parser_preserves_signed_amounts_and_large_totals(self):
        blocks = build_line_item_details.parse_page(
            "LINE 01 - ORDINARY INCOME DETAIL\n"
            "DESCRIPTION VALUE\n"
            "NEGATIVE ITEM -0.01\n"
            "POSITIVE ITEM +9007199254740992.02\n"
            "TOTAL 9007199254740992.01\n"
        )
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]["total"], Decimal("9007199254740992.01"))
        self.assertEqual(
            [row["value"] for row in blocks[0]["components"]],
            [Decimal("-0.01"), Decimal("9007199254740992.02")],
        )

    def test_nonfinite_printed_amount_is_not_a_fact(self):
        for parser in (face_reader.parse_numeric, face_reader._parse_overlay_number):
            for token in ("NaN", "Infinity", "-Infinity", "1E1001"):
                with self.subTest(parser=parser.__name__, token=token):
                    self.assertIsNone(parser(token))

    def test_proof_emission_query_and_roundtrip_preserve_exact_values(self):
        source = copy.deepcopy(proof.SOURCE_DATA)
        source["income"]["box_1"] = Decimal("9007199254740992.01")
        source["partner"]["share_percentages"]["profit_beginning"] = Decimal(
            "0.123456789012345678901234567890"
        )
        before = copy.deepcopy(source)
        emitter = proof.OTDEmitter()
        emitted = emitter.emit_to_file(source, self.root / "emitted.otd.yaml")
        raw = decimal_yaml(round_trip=True).load(emitted)
        document = proof.TaxDocument(raw)
        self.assertEqual(
            document.get_value("ordinary_business_income"), Decimal("9007199254740992.01")
        )
        self.assertEqual(
            document.get_value("partner.share_percentages")["profit_beginning"],
            Decimal("0.123456789012345678901234567890"),
        )
        self.assertIn("value: 9007199254740992.01", emitted)
        self.assertEqual(source, before)
        passed, detail = proof.round_trip_test(emitted, self.root / "reemitted.otd.yaml")
        self.assertTrue(passed, detail)
        first = emitter.emit(source)
        second = emitter.emit(source)
        self.assertEqual(first, second)

    def test_normalization_does_not_round_unknown_extension_numbers(self):
        emitter = proof.OTDEmitter()
        document = emitter.emit(proof.SOURCE_DATA)
        extension = {"value": Decimal("0.123456789012345678901234567890")}
        document["body"]["part_iii"]["future_extension"] = copy.deepcopy(extension)
        normalize_k1_numbers(document, emitter.taxonomy)
        self.assertEqual(document["body"]["part_iii"]["future_extension"], extension)

    def test_reconciliation_one_cent_boundary_and_large_counterexample(self):
        document = {"body": {"part_iii": {"box_1": {"value": Decimal("0.29")}}}}
        details = [{"box_key": "box_1", "total": Decimal("0.30")}]
        result = reconcile(document, details)
        self.assertEqual(result[0]["status"], "match")
        self.assertEqual(result[0]["delta"], Decimal("0.01"))
        document["body"]["part_iii"]["box_1"]["value"] = Decimal("9007199254740992.01")
        details[0]["total"] = Decimal("9007199254740992.03")
        result = reconcile(document, details)
        self.assertEqual(result[0]["status"], "mismatch")
        self.assertEqual(result[0]["delta"], Decimal("0.02"))

    def test_reconciliation_rejects_nonfinite_and_boolean_values(self):
        details = [{"box_key": "box_1", "total": Decimal("1.00")}]
        for value in (True, Decimal("NaN"), Decimal("Infinity")):
            document = {"body": {"part_iii": {"box_1": {"value": value}}}}
            with self.subTest(value=str(value)):
                with self.assertRaisesRegex(ValueError, "finite numbers"):
                    reconcile(document, details)


if __name__ == "__main__":
    unittest.main(verbosity=2)
