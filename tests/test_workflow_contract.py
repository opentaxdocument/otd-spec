#!/usr/bin/env python
"""Portable extraction workflow contract tests.

Exercises the current capability-named tools against the repository-local
blank K-1 fixture. Generated data lives in a temporary directory.
"""

import importlib.util
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "skills" / "k1-otd" / "scripts"
EXTRACTOR = SCRIPTS / "extract_pdf_text.py"
MANIFEST_BUILDER = SCRIPTS / "build_section_manifests.py"
ASSEMBLER = SCRIPTS / "assemble_otd.py"
BLANK_PDF = REPO_ROOT / "tests" / "fixtures" / "pdf" / "irs-k1-1065-2025-blank.pdf"
WINDOWS_ABSOLUTE_PATH = re.compile(r"\b[A-Za-z]:[\\/]")


class ContractFailure(RuntimeError):
    pass


def run_tool(*arguments):
    return subprocess.run(
        [sys.executable, "-B", *map(str, arguments)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def require(condition, message):
    if not condition:
        raise ContractFailure(message)


def require_success(result, label):
    require(
        result.returncode == 0,
        "%s failed with RC %d\nSTDOUT:\n%s\nSTDERR:\n%s"
        % (label, result.returncode, result.stdout, result.stderr),
    )


def reject_absolute_paths(value, location="$"):
    if isinstance(value, dict):
        for key, child in value.items():
            reject_absolute_paths(child, "%s.%s" % (location, key))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            reject_absolute_paths(child, "%s[%d]" % (location, index))
    elif isinstance(value, str) and WINDOWS_ABSOLUTE_PATH.search(value):
        raise ContractFailure(
            "machine-local absolute path at %s: %r" % (location, value)
        )


def main():
    require(EXTRACTOR.is_file(), "current PDF extractor is missing")
    require(MANIFEST_BUILDER.is_file(), "current manifest builder is missing")
    require(BLANK_PDF.is_file(), "repository-local blank PDF fixture is missing")

    for obsolete in (
        "phase1_extract_text.py",
        "phase2_classify.py",
        "phase4_assemble.py",
    ):
        require(not (SCRIPTS / obsolete).exists(), "obsolete script remains: " + obsolete)

    module_spec = importlib.util.spec_from_file_location(
        "assemble_otd_contract", ASSEMBLER
    )
    require(module_spec is not None and module_spec.loader is not None,
            "assembler module could not be loaded")
    assembler = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(assembler)
    aliases = {
        "section_199a": "section_199a_detail",
        "irs_form_926": "form_926_transfer_to_foreign_corp",
        "state_k1_grid": "state_apportionment",
    }
    for legacy, canonical in aliases.items():
        require(
            assembler.canon_classification(legacy) == canonical,
            "classification alias changed: %s" % legacy,
        )
        require(
            assembler.canon_classification(canonical) == canonical,
            "canonical classification was rewritten: %s" % canonical,
        )

    with tempfile.TemporaryDirectory(prefix="otd-workflow-contract-") as temp:
        root = Path(temp)
        extraction_root = root / "extraction"

        extracted = run_tool(
            EXTRACTOR,
            "--pdf",
            BLANK_PDF,
            "--out",
            extraction_root,
        )
        require_success(extracted, "extract_pdf_text.py")

        text_dir = extraction_root / "text_blocks"
        index_path = text_dir / "page_index.json"
        require(index_path.is_file(), "page_index.json was not emitted")
        page_index = json.loads(index_path.read_text(encoding="utf-8"))

        require("pdf_path" not in page_index, "page index leaked pdf_path")
        require("type_hint" not in page_index, "page index retained top-level type_hint")
        require(page_index.get("pdf_name") == BLANK_PDF.name, "portable source name changed")
        require(isinstance(page_index.get("sha256"), str), "page index SHA-256 missing")
        require(
            isinstance(page_index.get("total_pages"), int)
            and page_index["total_pages"] > 0,
            "page index total_pages must be positive",
        )
        require(isinstance(page_index.get("pages"), list), "page list missing")
        require(
            page_index["total_pages"] == len(page_index["pages"]),
            "page count disagrees with emitted page evidence",
        )
        require(
            all("type_hint" not in page for page in page_index["pages"]),
            "page-level type_hint remains",
        )
        require(
            all(
                {"page", "file", "char_count"} <= set(page)
                for page in page_index["pages"]
            ),
            "page evidence shape changed",
        )
        reject_absolute_paths(page_index)

        missing_text = run_tool(
            MANIFEST_BUILDER,
            "--index",
            index_path,
            "--out",
            root / "missing-text-page-manifest.json",
        )
        require(
            missing_text.returncode == 2,
            "manifest builder must return RC 2 when --text-dir is omitted",
        )

        legacy_flag = run_tool(
            MANIFEST_BUILDER,
            "--index",
            index_path,
            "--text-dir",
            text_dir,
            "--out",
            root / "legacy-page-manifest.json",
            "--legacy-heuristics",
        )
        require(
            legacy_flag.returncode == 2,
            "removed --legacy-heuristics flag was unexpectedly accepted",
        )

        page_manifest_path = root / "page_manifest.json"
        section_manifest_path = root / "section_manifest.json"
        built = run_tool(
            MANIFEST_BUILDER,
            "--index",
            index_path,
            "--text-dir",
            text_dir,
            "--out",
            page_manifest_path,
            "--section-out",
            section_manifest_path,
        )
        require_success(built, "build_section_manifests.py")

        page_manifest = json.loads(page_manifest_path.read_text(encoding="utf-8"))
        section_manifest = json.loads(
            section_manifest_path.read_text(encoding="utf-8")
        )

        require(page_manifest.get("method") == "section_level", "page method changed")
        require(
            page_manifest.get("total_pages") == page_index["total_pages"],
            "page manifest total disagrees with page index",
        )
        require(isinstance(page_manifest.get("sections"), list), "page sections missing")
        require(
            isinstance(page_manifest.get("unresolved_sections"), list),
            "unresolved section ledger missing",
        )

        require(
            section_manifest.get("total_pages") == page_index["total_pages"],
            "section manifest total disagrees with page index",
        )
        require(
            section_manifest.get("total_sections")
            == len(section_manifest.get("sections", [])),
            "section count disagrees with emitted sections",
        )
        require(
            Path(section_manifest.get("signatures", "")).name
            == section_manifest.get("signatures"),
            "signature provenance is not portable",
        )

        reject_absolute_paths(page_manifest)
        reject_absolute_paths(section_manifest)

        raw_fragments = root / "raw-face-evidence"
        raw_fragments.mkdir()
        (raw_fragments / "face_page.json").write_text(
            json.dumps({"status_counts": {}, "fields": {}}),
            encoding="utf-8",
        )
        for name, value in {
            "overflow_statements.json": [],
            "footnotes_a.json": [],
            "footnotes_b.json": [],
            "state_schedules.json": {},
        }.items():
            (raw_fragments / name).write_text(
                json.dumps(value),
                encoding="utf-8",
            )
        raw_assembly = run_tool(
            ASSEMBLER,
            "--fragments",
            raw_fragments,
            "--out",
            root / "must-not-exist.otd.yaml",
        )
        require(
            raw_assembly.returncode == 2,
            "raw face-reader evidence was accepted as an assembler fragment",
        )

    print("PASS: portable page-index contract")
    print("PASS: logical section builder requires page text")
    print("PASS: legacy heuristic flag rejected")
    print("PASS: page/section manifest shapes preserved")
    print("PASS: generated JSON contains no machine-local paths")
    print("PASS: published classification aliases canonicalize deterministically")
    print("PASS: raw face-reader evidence is rejected at assembly boundary")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ContractFailure as exc:
        print("FAIL: %s" % exc, file=sys.stderr)
        sys.exit(1)
