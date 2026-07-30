#!/usr/bin/env python
"""End-to-end contract for the bounded synthetic PDF-to-OTD demonstration."""

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
RUNNER = (
    REPO_ROOT / "examples" / "k1-1065-2025-synthetic" / "run_demo.py"
)
SOURCE = (
    REPO_ROOT / "examples" / "k1-1065-2025-synthetic"
    / "source" / "synthetic-k1.pdf"
)
DEMONSTRATION = (
    REPO_ROOT / "examples" / "k1-1065-2025-synthetic"
    / "demonstration"
)
BLANK = REPO_ROOT / "tests" / "fixtures" / "pdf" / "irs-k1-1065-2025-blank.pdf"
EXPECTED_SHA = "db6e9764726d0268e6288774b3ffc508faa829f9b5cee770e26262e26ed75fac"
PINNED_CREATED = "2026-07-30T00:00:00Z"

PUBLIC_ARTIFACTS = (
    "output.otd.yaml",
    "output.confidence.json",
    "evidence-receipt.json",
    "projection-manifest.json",
    "disposition-ledger.json",
    "reconciliation.json",
    "extraction-status.json",
    "run-manifest.json",
)
FRAGMENT_ARTIFACTS = (
    "face_page.json",
    "overflow_statements.json",
    "footnotes_a.json",
    "footnotes_b.json",
    "state_schedules.json",
)


class ContractFailure(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise ContractFailure(message)


def run_demo(pdf, out, work):
    return subprocess.run(
        [
            sys.executable,
            "-B",
            str(RUNNER),
            "--pdf",
            str(pdf),
            "--out",
            str(out),
            "--work-dir",
            str(work),
            "--created",
            PINNED_CREATED,
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_yaml(path):
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    require(RUNNER.is_file(), "demonstration runner is missing")
    require(SOURCE.is_file(), "synthetic PDF fixture is missing")
    require(BLANK.is_file(), "blank IRS PDF fixture is missing")
    require(digest(SOURCE) == EXPECTED_SHA, "synthetic PDF hash changed")

    with tempfile.TemporaryDirectory(prefix="otd-synthetic-e2e-") as temp:
        root = Path(temp)
        input_a = root / "input-a" / "renamed-source.pdf"
        input_b = root / "input-b" / "another-name.pdf"
        input_a.parent.mkdir()
        input_b.parent.mkdir()
        shutil.copyfile(SOURCE, input_a)
        shutil.copyfile(SOURCE, input_b)

        out_a, work_a = root / "out-a", root / "work-a"
        out_b, work_b = root / "out-b", root / "work-b"
        snapshot_out, snapshot_work = root / "out-snapshot", root / "work-snapshot"
        first = run_demo(input_a, out_a, work_a)
        second = run_demo(input_b, out_b, work_b)
        snapshot = run_demo(SOURCE, snapshot_out, snapshot_work)
        require(
            first.returncode == 0,
            "first run failed\nSTDOUT:\n%s\nSTDERR:\n%s"
            % (first.stdout, first.stderr),
        )
        require(
            second.returncode == 0,
            "second run failed\nSTDOUT:\n%s\nSTDERR:\n%s"
            % (second.stdout, second.stderr),
        )
        require(
            snapshot.returncode == 0,
            "canonical snapshot run failed\nSTDOUT:\n%s\nSTDERR:\n%s"
            % (snapshot.stdout, snapshot.stderr),
        )

        for out in (out_a, out_b, snapshot_out):
            for name in PUBLIC_ARTIFACTS:
                require((out / name).is_file(), "missing public artifact: " + name)

        require(
            (out_a / "output.otd.yaml").read_bytes()
            == (out_b / "output.otd.yaml").read_bytes(),
            "pinned reruns did not emit byte-identical OTD",
        )
        for fragment_name in FRAGMENT_ARTIFACTS:
            require(
                (out_a / "fragments" / fragment_name).read_bytes()
                == (out_b / "fragments" / fragment_name).read_bytes(),
                "pinned reruns changed fragment " + fragment_name,
            )

        expected_snapshot_paths = set(PUBLIC_ARTIFACTS) | {
            "fragments/" + name for name in FRAGMENT_ARTIFACTS
        }
        actual_snapshot_paths = {
            path.relative_to(snapshot_out).as_posix()
            for path in snapshot_out.rglob("*") if path.is_file()
        }
        require(
            actual_snapshot_paths == expected_snapshot_paths,
            "canonical run published an unexpected artifact set",
        )
        for relative_name in sorted(expected_snapshot_paths):
            require(
                (snapshot_out / relative_name).read_bytes()
                == (DEMONSTRATION / relative_name).read_bytes(),
                "committed snapshot differs for " + relative_name,
            )

        document = load_yaml(out_a / "output.otd.yaml")
        coverage = document["otd"].get("coverage", {})
        require(
            coverage.get("profile_id")
            == "k1-1065-2025-face-and-numeric-details/1.0",
            "standalone OTD does not identify its bounded profile",
        )
        require(
            coverage.get("full_source_document") is False,
            "standalone OTD claims full-document coverage",
        )
        companion_paths = {
            item.get("path"): item.get("sha256")
            for item in coverage.get("companion_artifacts", {}).values()
        }
        for companion_name in (
            "evidence-receipt.json",
            "projection-manifest.json",
            "disposition-ledger.json",
        ):
            require(
                companion_paths.get(companion_name)
                == digest(out_a / companion_name),
                "standalone OTD companion hash mismatch: " + companion_name,
            )
        require(
            document["otd"]["source_document"]["sha256"] == EXPECTED_SHA,
            "OTD source hash mismatch",
        )
        require(document["body"]["part_i"]["item_a"]["value"] == "**-**65189",
                "partnership EIN was not projected from PDF evidence")
        require(document["body"]["part_iii"]["box_5"]["value"] == 434000.0,
                "Box 5 was not projected from PDF evidence")
        require(document["body"]["part_iii"]["box_16"]["checked"] is False,
                "Box 16 verified absence changed")
        require(document["body"]["part_ii"]["item_m"]["value"] is False,
                "Item M choice was not normalized to boolean false")

        shares = document["body"]["part_ii"]["item_j"]["value"]
        require(
            all(
                shares[key] == 1.0
                for key in (
                    "profit_beginning", "profit_ending",
                    "loss_beginning", "loss_ending",
                    "capital_beginning", "capital_ending",
                )
            ),
            "percentage points were not normalized to fractions",
        )
        box_4c = document["body"]["part_iii"]["box_4c"]
        require(
            box_4c["value"] is None
            and "observed blank" in box_4c.get("_unverified", ""),
            "printed-blank Box 4c was asserted as a source fact",
        )
        item_l = document["body"]["part_ii"]["item_l"]
        require(item_l["value"] is None and item_l.get("_unverified"),
                "partial Item L was asserted instead of quarantined as null")

        box_11 = {
            entry["code"]: entry["value"]
            for entry in document["body"]["part_iii"]["box_11"]["entries"]
        }
        require(box_11["A"] == 600700.0, "face-coded Box 11A missing")
        require(box_11["C"] == 218800.0, "safe detail Box 11C missing")
        require(box_11["S"] == 738700.0, "safe detail Box 11S missing")
        require("*" not in box_11, "statement pointer became a coded fact")
        require("ZZ" not in box_11, "unclassified Box 11ZZ was fabricated")

        box_13 = {
            entry["code"]: entry["value"]
            for entry in document["body"]["part_iii"]["box_13"]["entries"]
        }
        require(box_13["AE"] == -290600.0, "safe detail Box 13AE missing")
        require("ZZ" not in box_13, "unclassified Box 13ZZ was fabricated")

        box_20 = {
            entry["code"]: entry["value"]
            for entry in document["body"]["part_iii"]["box_20"]["entries"]
        }
        require("C" not in box_20,
                "Box 20C was emitted without its required parsed statement")

        projection = load_json(out_a / "projection-manifest.json")
        disposition = load_json(out_a / "disposition-ledger.json")
        status = load_json(out_a / "extraction-status.json")
        confidence = load_json(out_a / "output.confidence.json")
        reconciliation = load_json(out_a / "reconciliation.json")
        run_manifest = load_json(out_a / "run-manifest.json")

        require(projection["counts"]["source_face_fields"] == 53,
                "source face-field count changed")
        require(projection["counts"]["projected_face_fields"] == 52,
                "projected face-field count changed")
        require(projection["counts"]["omitted_face_fields"] == 1,
                "omitted face-field count changed")
        require(
            projection["counts"]["derived_facts"] == 1
            and projection["derived_facts"][0].get("output_path")
                == "part_iii_face.box_4c"
            and projection["derived_facts"][0].get("result") == 1000.0,
            "Box 4c derivation evidence is missing",
        )

        projected_keys = {
            item["source_field"]
            for item in projection["projected_face_fields"]
        }
        omitted_keys = {
            item["source_field"]
            for item in disposition["entries"]
            if item.get("kind") == "face_field"
        }
        require(not (projected_keys & omitted_keys),
                "face field appears in both projection and omission sets")
        require(len(projected_keys | omitted_keys) == 53,
                "face projection/omission partition is incomplete")

        require(disposition["counts"]["unresolved_sections"] == 4,
                "known unresolved-section count changed")
        require(any(
            item.get("id") == "state-grid-escalation"
            and item.get("disposition") == "excluded_no_values_fabricated"
            for item in disposition["entries"]
        ), "state-grid exclusion is missing")
        require(any(
            item.get("box_key") == "box_13"
            and item.get("code") == "ZZ"
            and item.get("disposition") == "excluded_requires_classification"
            for item in disposition["entries"]
        ), "unclassified Box 13ZZ exclusion is missing")
        require(any(
            item.get("source_field") == "box_20"
            and item.get("code") == "C"
            and item.get("disposition") == "excluded_requires_statement"
            for item in disposition["entries"]
        ), "statement-dependent Box 20C exclusion is missing")
        require(
            disposition["counts"]["omission_entries"]
            < disposition["counts"]["entries"],
            "projected or reconciliation records are still counted as omissions",
        )
        require(
            disposition["counts"]["partially_consumed_sections"] > 0,
            "partially consumed detail sections are not reported",
        )
        require(
            confidence["omission_count"]
            == disposition["counts"]["omission_entries"],
            "confidence omission count is not the actual omission count",
        )

        require(reconciliation["counts"].get("mismatch", 0) == 0,
                "face/detail reconciliation contains a mismatch")
        require(reconciliation["counts"].get("match") == 15,
                "supported-profile reconciliation match count changed")
        require(reconciliation["counts"].get("not_applicable") == 3,
                "no-printed-total reconciliation count changed")
        require(reconciliation["counts"].get("face_missing") == 3,
                "explicitly omitted reconciliation count changed")
        face_missing_keys = {
            (item.get("box_key"), item.get("code"))
            for item in reconciliation["results"]
            if item.get("status") == "face_missing"
        }
        require(
            face_missing_keys
            == {("box_13", "ZZ"), ("box_20", "P"), ("box_20", "T")},
            "unexpected detail records are absent from the supported profile",
        )
        ledgered_detail_keys = {
            (item.get("box_key"), item.get("code"))
            for item in disposition["entries"]
            if item.get("kind") == "line_item_detail"
            and str(item.get("disposition", "")).startswith("excluded")
        }
        require(face_missing_keys <= ledgered_detail_keys,
                "face-missing reconciliation records are not fully ledgered")
        require(confidence.get("validation_passes") is True,
                "production validator did not pass")
        require(confidence.get("validation_errors") == [],
                "production validator reported errors")
        require(status["claims"]["started_from_pdf_bytes"] is True,
                "status does not claim a PDF-byte start")
        require(
            status["claims"]["supported_profile_face_detail_reconciled"] is True,
            "supported-profile reconciliation did not pass",
        )
        require(
            status["claims"]["all_source_detail_records_reconciled"] is False,
            "bounded demo claimed every source detail record was reconciled",
        )
        require(status["claims"]["full_source_document_extraction"] is False,
                "bounded demo claimed full-document extraction")
        require(
            status["claims"]["unsupported_sections_inferred_or_defaulted"] is False,
            "bounded demo inferred unsupported content",
        )
        require(
            status["claims"]["historical_fragments_or_expected_values_used"] is False,
            "bounded demo consumed historical artifacts",
        )
        require(run_manifest.get("orchestrator_sha256") == digest(RUNNER),
                "run manifest does not identify the orchestrator bytes")
        require(len(run_manifest.get("grammar_sha256", "")) == 64,
                "run manifest does not identify the grammar bytes")
        require(len(run_manifest.get("taxonomy_sha256", "")) == 64,
                "run manifest does not identify the taxonomy bytes")
        require(all(len(stage.get("tool_sha256", "")) == 64
                    for stage in run_manifest["stages"]),
                "one or more executed tools lack a recorded source hash")

        poisoned = root / "misleading" / "synthetic-k1.pdf"
        poisoned.parent.mkdir()
        shutil.copyfile(BLANK, poisoned)
        rejected = run_demo(poisoned, root / "out-blank", root / "work-blank")
        require(rejected.returncode != 0,
                "wrong PDF bytes were accepted because the filename matched")
        require(
            not (root / "out-blank" / "output.otd.yaml").exists(),
            "wrong PDF bytes published an OTD document",
        )

    print("PASS: starts from copied PDF bytes, independent of filename")
    print("PASS: source hash is bound through fragments and final OTD")
    print("PASS: 53 face fields partition into 52 projected and 1 omitted")
    print("PASS: unsupported statements, sections, and state grids stay explicit")
    print("PASS: production assembler, validator, and reconciliation gates pass")
    print("PASS: pinned reruns emit byte-identical OTD and fragments")
    print("PASS: complete committed public snapshot matches a canonical fresh run")
    print("PASS: misleading filename cannot bypass source-byte identity")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ContractFailure as exc:
        print("FAIL: %s" % exc, file=sys.stderr)
        sys.exit(1)
