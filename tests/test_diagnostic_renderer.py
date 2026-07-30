#!/usr/bin/env python
"""Focused contract test for render_extraction_diagnostics.py."""
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image
import pdfplumber


REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "skills/k1-otd/scripts/render_extraction_diagnostics.py"
PDF = REPO / "tests/fixtures/pdf/irs-k1-1065-2025-blank.pdf"
WINDOWS_PATH = re.compile(r"\b[A-Za-z]:[\\/]")


def write_json(path, value):
    path.write_text(
        json.dumps(value, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def main():
    with tempfile.TemporaryDirectory(prefix="otd-diagnostic-test-") as tmp:
        root = Path(tmp)
        output = root / "diagnostics"
        text_dir = root / "text_blocks"
        text_dir.mkdir()

        with pdfplumber.open(str(PDF)) as document:
            first_line = document.pages[0].extract_text_lines(
                layout=False, return_chars=False)[0]["text"]

        (text_dir / "page_01.txt").write_text(
            first_line + "\n", encoding="utf-8", newline="\n")

        face = root / "face.json"
        page_manifest = root / "page-manifest.json"
        section_manifest = root / "section-manifest.json"
        escalation = root / "state-attempt.json"

        write_json(face, {
            "fields": {
                "test.present": {
                    "semantic_id": "test.present",
                    "raw_text": first_line,
                    "normalized_value": first_line,
                    "status": "present",
                    "page": 1,
                    "bbox": [20, 20, 180, 42],
                    "method": "test_exact_box",
                },
                "test.unresolved": {
                    "semantic_id": "test.unresolved",
                    "raw_text": None,
                    "normalized_value": None,
                    "status": "unresolved",
                    "page": 1,
                    "bbox": None,
                    "method": "test_missing_geometry",
                },
            }
        })
        write_json(page_manifest, {
            "sections": [{
                "name": "face_page",
                "pages": [1],
                "type": "face_page",
                "worker": "A",
            }]
        })
        write_json(section_manifest, {
            "sections": [{
                "page": 1,
                "continued_pages": [],
                "index": 1,
                "section": "face_page",
                "role": "face_page",
                "score": 1.0,
                "line_start": 0,
                "line_end": 0,
                "row_count": 1,
                "dominant_shape": "PROSE",
                "reasons": ["test fixture"],
            }]
        })
        write_json(escalation, {
            "_escalate": True,
            "_escalation_reason": "test escalation without page geometry",
        })

        command = [
            sys.executable,
            "-B",
            str(SCRIPT),
            "--pdf", str(PDF),
            "--out", str(output),
            "--face-evidence", str(face),
            "--page-manifest", str(page_manifest),
            "--section-manifest", str(section_manifest),
            "--text-dir", str(text_dir),
            "--evidence", str(escalation),
            "--resolution", "72",
            "--max-pages", "1",
        ]
        result = subprocess.run(
            command,
            cwd=str(REPO),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
        require(
            result.returncode == 0,
            "renderer failed RC %d\nSTDOUT:\n%s\nSTDERR:\n%s"
            % (result.returncode, result.stdout, result.stderr),
        )

        index_path = output / "diagnostic-index.json"
        image_path = output / "page_001.png"
        require(index_path.exists(), "diagnostic index was not written")
        require(image_path.exists(), "annotated page image was not written")

        payload = index_path.read_text(encoding="utf-8")
        require(not WINDOWS_PATH.search(payload),
                "diagnostic index leaked a machine-local path")
        index = json.loads(payload)

        require(index["schema_version"] == "otd-diagnostic/1.0",
                "diagnostic schema version changed")
        require(index["source_pdf"] == PDF.name,
                "source identity must be filename-only")
        require(index["summary"]["rendered_page_count"] == 1,
                "max-pages contract changed")
        require(index["summary"]["boxed_item_count"] >= 3,
                "expected exact, section-derived, and page boxes")
        require(index["summary"]["sidebar_item_count"] >= 2,
                "unboxable evidence must remain visible in sidebar")
        require(
            index["color_legend"]["Present / matched / resolved"]
            == "#16a34a",
            "present color changed",
        )
        require(
            index["color_legend"]["Invalid / exception / hard error"]
            == "#dc2626",
            "error color changed",
        )
        require(all(
            item["placement"] in ("box", "sidebar")
            for item in index["items"]
        ), "every diagnostic item must have a visible placement")

        with Image.open(image_path) as image:
            require(image.width > 612 and image.height >= 792,
                    "rendered image does not include page plus sidebar")
            require(image.mode == "RGB", "diagnostic image must be RGB")

    print("PASS: diagnostic renderer boxes and labels evidence")
    print("PASS: missing geometry remains visible in sidebar")
    print("PASS: status/error color contract preserved")
    print("PASS: diagnostic index is portable and complete")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print("FAIL: %s" % exc, file=sys.stderr)
        sys.exit(1)
