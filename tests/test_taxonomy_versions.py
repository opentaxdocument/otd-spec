#!/usr/bin/env python
"""Exact-version compatibility for the label-only K-1 taxonomy correction."""
import copy
import hashlib
import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills/k1-otd/scripts"
CURRENT = ROOT / "taxonomies/irs-k1-1065-2025.yaml"
ARCHIVE = ROOT / "taxonomies/archive/irs-k1-1065-2025-2025.1.0.yaml"
sys.path.insert(0, str(SCRIPTS))

from otd_values import decimal_yaml
from validate_otd import resolve_taxonomy_path, ValidatorConfigurationError


class TaxonomyVersions(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="otd-taxonomy-versions-")
        self.addCleanup(self.temp.cleanup)
        self.input = Path(self.temp.name) / "external.otd.yaml"
        self.document = decimal_yaml(round_trip=True).load(
            (ROOT / "proof/proof-emitted.otd.yaml").read_text(encoding="utf-8-sig")
        )

    def invoke(self, version, explicit=None):
        self.document["otd"]["taxonomy"]["version"] = version
        stream = io.StringIO()
        decimal_yaml(round_trip=True).dump(self.document, stream)
        self.input.write_text(stream.getvalue(), encoding="utf-8")
        command = [sys.executable, "-B", str(SCRIPTS / "validate_otd.py"),
                   "--input", str(self.input)]
        if explicit is not None:
            command.extend(["--taxonomy", str(explicit)])
        return subprocess.run(
            command, cwd=ROOT, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=30,
        )

    def test_published_version_is_preserved_byte_for_byte(self):
        self.assertEqual(
            hashlib.sha256(ARCHIVE.read_bytes()).hexdigest(),
            "ed8b25ad44cc7128370a507d2a8595b7b809ca2a84dc10287795363fe7b7fed8",
        )

    def test_patch_changes_only_labels_and_version_history(self):
        old = decimal_yaml().load(ARCHIVE.read_text(encoding="utf-8"))
        new = decimal_yaml().load(CURRENT.read_text(encoding="utf-8"))
        self.assertEqual(old["taxonomy"]["version"], "2025.1.0")
        self.assertEqual(new["taxonomy"]["version"], "2025.1.1")
        expected = {
            ("box_15", "C"): "Low-income housing credit (section 42(j)(5)) from post-2007 buildings",
            ("box_15", "D"): "Low-income housing credit (other) from post-2007 buildings",
            ("box_20", "F"): "Recapture of low-income housing credit for section 42(j)(5) partnerships",
            ("box_20", "G"): "Recapture of low-income housing credit for other partnerships",
        }
        comparison = copy.deepcopy(old)
        for (box, code), label in expected.items():
            self.assertEqual(new["nodes"]["part_iii"]["children"][box]["codes"][code]["label"], label)
            comparison["nodes"]["part_iii"]["children"][box]["codes"][code]["label"] = label
        comparison["taxonomy"]["version"] = new["taxonomy"]["version"]
        comparison["change_log"] = new["change_log"]
        self.assertEqual(comparison, new)

    def test_older_external_document_resolves_its_archived_taxonomy(self):
        resolved = resolve_taxonomy_path(
            self.input, taxonomy_claim={"id": "irs-k1-1065-2025", "version": "2025.1.0"}
        )
        self.assertEqual(resolved.resolve(), ARCHIVE.resolve())
        result = self.invoke("2025.1.0")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_current_external_document_resolves_current_taxonomy(self):
        resolved = resolve_taxonomy_path(
            self.input, taxonomy_claim={"id": "irs-k1-1065-2025", "version": "2025.1.1"}
        )
        self.assertEqual(resolved.resolve(), CURRENT.resolve())
        result = self.invoke("2025.1.1")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_unknown_version_never_substitutes_current_version(self):
        result = self.invoke("2025.99.0")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("No bundled taxonomy", result.stderr)

    def test_explicit_mismatched_version_rejects_document(self):
        result = self.invoke("2025.1.0", CURRENT)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("version", result.stdout)

    def test_document_identity_cannot_select_arbitrary_paths(self):
        with self.assertRaises(ValidatorConfigurationError):
            resolve_taxonomy_path(
                self.input,
                taxonomy_claim={"id": "../../outside", "version": "../secret"},
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
