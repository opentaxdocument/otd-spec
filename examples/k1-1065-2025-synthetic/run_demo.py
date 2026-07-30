#!/usr/bin/env python
"""Hermetic, bounded PDF-to-OTD demonstration for the synthetic 2025 K-1.

The run begins from PDF bytes and current source tools. It never reads the
example's checked-in evidence, expected values, diagnostics, or prior outputs.
Only the declared face-and-numeric-detail profile is published; every excluded
section or record is retained in the omission ledger.
"""

import argparse
import datetime
import hashlib
import importlib.metadata
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

try:
    import yaml
except ImportError as exc:  # pragma: no cover
    print("ERROR: missing dependency: %s" % exc, file=sys.stderr)
    raise


REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_ROOT = Path(__file__).resolve().parent
SCRIPTS = REPO_ROOT / "skills" / "k1-otd" / "scripts"
GRAMMAR = REPO_ROOT / "skills" / "k1-otd" / "grammars" / "k1-1065-2025.grammar.yaml"
TAXONOMY = REPO_ROOT / "taxonomies" / "irs-k1-1065-2025.yaml"
DEFAULT_PDF = EXAMPLE_ROOT / "source" / "synthetic-k1.pdf"
EXPECTED_SOURCE_SHA256 = "db6e9764726d0268e6288774b3ffc508faa829f9b5cee770e26262e26ed75fac"
PROFILE_ID = "k1-1065-2025-face-and-numeric-details/1.0"
DEMO_REQUIREMENTS = EXAMPLE_ROOT / "requirements-demo.txt"


class DemoError(RuntimeError):
    pass


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path, value):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print("WROTE %s" % target.resolve())


def canonical_sha256(value):
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def portable_text(value, source_pdf, work_root, output_root):
    text = str(value)
    replacements = (
        (source_pdf, "<SOURCE_PDF>"),
        (work_root, "<WORK_ROOT>"),
        (output_root, "<OUTPUT_ROOT>"),
        (REPO_ROOT, "<REPO_ROOT>"),
    )
    for path, token in replacements:
        resolved = Path(path).resolve()
        for candidate in {str(resolved), resolved.as_posix()}:
            text = text.replace(candidate, token)
    return text


def portable_argument(value, source_pdf, work_root, output_root):
    path = Path(value) if isinstance(value, (str, os.PathLike)) else None
    if path is not None:
        try:
            resolved = path.resolve()
        except OSError:
            resolved = None
        if resolved == source_pdf.resolve():
            return "<SOURCE_PDF>"
        try:
            return "<WORK_ROOT>/" + resolved.relative_to(work_root.resolve()).as_posix()
        except (ValueError, AttributeError):
            pass
        try:
            return "<OUTPUT_ROOT>/" + resolved.relative_to(output_root.resolve()).as_posix()
        except (ValueError, AttributeError):
            pass
        try:
            return resolved.relative_to(REPO_ROOT.resolve()).as_posix()
        except (ValueError, AttributeError):
            pass
    return str(value)


def git_state():
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    status = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    entries = [
        line.rstrip() for line in status.stdout.splitlines() if line.strip()
    ]
    return {
        "head": (
            revision.stdout.strip() if revision.returncode == 0 else "unknown"
        ),
        "dirty": bool(entries),
        "status": entries,
    }


def dependency_versions():
    versions = {
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
    }
    for distribution in ("PyYAML", "ruamel.yaml", "pdfplumber", "pypdf"):
        try:
            versions[distribution] = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            versions[distribution] = "not-installed"
    return versions


def source_closure():
    paths = [
        Path(__file__).resolve(),
        GRAMMAR,
        TAXONOMY,
        DEMO_REQUIREMENTS,
        *sorted(SCRIPTS.rglob("*.py")),
    ]
    return {
        path.resolve().relative_to(REPO_ROOT.resolve()).as_posix():
        sha256_file(path)
        for path in paths
    }


def copy_public(source, destination):
    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Run the bounded synthetic 2025 K-1 PDF-to-OTD demonstration"
    )
    parser.add_argument("--pdf", default=str(DEFAULT_PDF))
    parser.add_argument("--out", required=True)
    parser.add_argument(
        "--work-dir",
        default=None,
        help="Private intermediate root; defaults to an external temporary directory",
    )
    parser.add_argument(
        "--created",
        default=None,
        help="Pinned ISO UTC creation time for reproducible output",
    )
    parser.add_argument(
        "--require-clean",
        action="store_true",
        help="Fail unless the repository working tree is clean",
    )
    parser.add_argument(
        "--fault-before-publish",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args(argv)

    source_pdf = Path(args.pdf).resolve()
    output_root = Path(args.out).resolve()

    if not source_pdf.is_file():
        raise DemoError("source PDF not found: %s" % source_pdf)
    source_sha = sha256_file(source_pdf)
    if source_sha != EXPECTED_SOURCE_SHA256:
        raise DemoError(
            "this fixture-scoped demonstration requires synthetic source SHA-256 "
            "%s, received %s" % (EXPECTED_SOURCE_SHA256, source_sha)
        )

    if output_root.exists():
        raise DemoError("output directory must be absent: %s" % output_root)

    repository = git_state()
    if args.require_clean and repository["dirty"]:
        raise DemoError("--require-clean rejected a dirty repository")

    auto_work = args.work_dir is None
    work_root = (
        Path(tempfile.mkdtemp(prefix="otd-k1-demo-")).resolve()
        if auto_work
        else Path(args.work_dir).resolve()
    )
    if work_root == output_root or output_root in work_root.parents:
        raise DemoError("private work root must not be inside public output")
    if work_root.exists() and any(work_root.iterdir()):
        raise DemoError("work directory must be absent or empty: %s" % work_root)
    output_root.parent.mkdir(parents=True, exist_ok=True)
    work_root.mkdir(parents=True, exist_ok=True)
    processing_pdf = work_root / "source.pdf"
    shutil.copyfile(source_pdf, processing_pdf)
    if sha256_file(processing_pdf) != source_sha:
        raise DemoError("private processing copy does not match source PDF")

    created = args.created
    if created is None:
        created = datetime.datetime.now(datetime.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
    if not isinstance(created, str) or not created.endswith("Z"):
        raise DemoError("--created must be an ISO UTC timestamp ending in Z")

    document_id = str(uuid.uuid5(
        uuid.NAMESPACE_URL,
        "https://opentaxdocument.org/demo/%s/%s" % (PROFILE_ID, source_sha),
    ))
    initial_source_closure = source_closure()
    dependencies = dependency_versions()

    logs = work_root / "logs"
    evidence = work_root / "evidence"
    fragments = work_root / "fragments"
    reports = work_root / "reports"
    staging = work_root / "staging"
    for directory in (logs, evidence, fragments, reports, staging):
        directory.mkdir(parents=True, exist_ok=True)

    stages = []
    publish_root = None
    published_bundle = False

    def run_stage(name, script, arguments):
        command = [sys.executable, "-B", str(script), *map(str, arguments)]
        result = subprocess.run(
            command,
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        portable_stdout = portable_text(
            result.stdout, source_pdf, work_root, output_root
        )
        portable_stderr = portable_text(
            result.stderr, source_pdf, work_root, output_root
        )
        log_path = logs / ("%s.log" % name)
        log_path.write_text(
            "COMMAND: %s\nEXIT CODE: %d\n\nSTDOUT:\n%s\n\nSTDERR:\n%s\n"
            % (
                " ".join(
                    portable_argument(item, source_pdf, work_root, output_root)
                    for item in command
                ),
                result.returncode,
                portable_stdout,
                portable_stderr,
            ),
            encoding="utf-8",
            newline="\n",
        )
        stages.append({
            "name": name,
            "tool": Path(script).resolve().relative_to(REPO_ROOT).as_posix(),
            "tool_sha256": sha256_file(script),
            "arguments": [
                portable_argument(item, source_pdf, work_root, output_root)
                for item in arguments
            ],
            "exit_code": result.returncode,
            "stdout_sha256": hashlib.sha256(
                portable_stdout.encode("utf-8")
            ).hexdigest(),
            "stderr_sha256": hashlib.sha256(
                portable_stderr.encode("utf-8")
            ).hexdigest(),
            "log_sha256": sha256_file(log_path),
        })
        if result.returncode != 0:
            raise DemoError(
                "stage %s failed with RC %d; see %s"
                % (name, result.returncode, log_path)
            )
        if (
            sha256_file(source_pdf) != source_sha
            or sha256_file(processing_pdf) != source_sha
        ):
            raise DemoError("source PDF changed during stage %s" % name)

    try:
        run_stage(
            "grammar-validation",
            SCRIPTS / "grammar_validator.py",
            [GRAMMAR],
        )
        run_stage(
            "pdf-text-extraction",
            SCRIPTS / "extract_pdf_text.py",
            ["--pdf", processing_pdf, "--out", work_root],
        )

        page_index_path = work_root / "text_blocks" / "page_index.json"
        page_index = json.loads(page_index_path.read_text(encoding="utf-8"))
        if page_index.get("sha256") != source_sha:
            raise DemoError("page index source hash disagrees with PDF")
        if page_index.get("total_pages") != 27:
            raise DemoError("synthetic source must contain exactly 27 pages")

        fit_dir = evidence / "template-fit"
        run_stage(
            "template-fit",
            SCRIPTS / "template_match.py",
            ["--pdf", processing_pdf, "--grammar", GRAMMAR, "--outdir", fit_dir],
        )
        fit_report_path = fit_dir / "template_fit_report.json"
        fit_reports = json.loads(fit_report_path.read_text(encoding="utf-8"))
        if (
            not isinstance(fit_reports, list)
            or len(fit_reports) != 1
            or fit_reports[0].get("evaluable") is not True
            or fit_reports[0].get("overall_verdict") != "match"
            or fit_reports[0].get("ambiguous_tie") is not False
            or not fit_reports[0].get("recommended_grammar_id")
        ):
            raise DemoError("template-fit gate did not produce one unique match")

        page_manifest_path = evidence / "page-manifest.json"
        section_manifest_path = evidence / "section-manifest.json"
        run_stage(
            "section-manifests",
            SCRIPTS / "build_section_manifests.py",
            [
                "--index", page_index_path,
                "--text-dir", work_root / "text_blocks",
                "--out", page_manifest_path,
                "--section-out", section_manifest_path,
            ],
        )

        face_evidence_path = evidence / "face-page.json"
        run_stage(
            "face-evidence",
            SCRIPTS / "face_reader.py",
            [
                processing_pdf,
                GRAMMAR,
                "--out", face_evidence_path,
                "--extraction-timestamp", created,
            ],
        )
        face = json.loads(face_evidence_path.read_text(encoding="utf-8"))
        if face.get("source_pdf_sha256") != source_sha:
            raise DemoError("face evidence source hash disagrees with PDF")

        state_attempt_path = evidence / "state-grid-attempt.json"
        run_stage(
            "state-grid-attempt",
            SCRIPTS / "extract_state_grids.py",
            [
                "--input-dir", work_root / "text_blocks",
                "--manifest", page_manifest_path,
                "--out", state_attempt_path,
            ],
        )

        line_details_path = evidence / "line-item-details.json"
        run_stage(
            "line-item-details",
            SCRIPTS / "build_line_item_details.py",
            [
                "--pages", work_root / "text_blocks",
                "--grammar", GRAMMAR,
                "--out", line_details_path,
            ],
        )

        evidence_receipt_path = reports / "evidence-receipt.json"
        receipt_artifacts = {
            "face_evidence": face_evidence_path,
            "line_details": line_details_path,
            "page_manifest": page_manifest_path,
            "section_manifest": section_manifest_path,
            "state_attempt": state_attempt_path,
        }
        write_json(evidence_receipt_path, {
            "schema_version": "otd-evidence-receipt/1.0",
            "profile_id": PROFILE_ID,
            "source_sha256": source_sha,
            "artifacts": {
                name: sha256_file(path)
                for name, path in receipt_artifacts.items()
            },
        })
        evidence_receipt_sha = sha256_file(evidence_receipt_path)

        projection_manifest_path = reports / "projection-manifest.json"
        disposition_ledger_path = reports / "disposition-ledger.json"
        run_stage(
            "evidence-projection",
            SCRIPTS / "project_extraction_evidence.py",
            [
                "--face-evidence", face_evidence_path,
                "--line-details", line_details_path,
                "--page-manifest", page_manifest_path,
                "--section-manifest", section_manifest_path,
                "--state-attempt", state_attempt_path,
                "--evidence-receipt", evidence_receipt_path,
                "--taxonomy", TAXONOMY,
                "--source-sha256", source_sha,
                "--out-dir", fragments,
                "--projection-manifest", projection_manifest_path,
                "--disposition-ledger", disposition_ledger_path,
            ],
        )

        for fragment_name in (
            "face_page.json",
            "overflow_statements.json",
            "footnotes_a.json",
            "footnotes_b.json",
            "state_schedules.json",
        ):
            fragment = json.loads(
                (fragments / fragment_name).read_text(encoding="utf-8")
            )
            if fragment.get("_source_pdf_sha256") != source_sha:
                raise DemoError(
                    "%s is not bound to the source PDF" % fragment_name
                )

        staged_otd = staging / "output.otd.yaml"
        run_stage(
            "assembly",
            SCRIPTS / "assemble_otd.py",
            [
                "--fragments", fragments,
                "--out", staged_otd,
                "--sha256", source_sha,
                "--document-id", document_id,
                "--created", created,
                "--profile-id", PROFILE_ID,
                "--evidence-receipt", evidence_receipt_path,
                "--projection-manifest", projection_manifest_path,
                "--disposition-ledger", disposition_ledger_path,
            ],
        )
        run_stage(
            "validation",
            SCRIPTS / "validate_otd.py",
            ["--input", staged_otd, "--taxonomy", TAXONOMY],
        )

        reconciliation_path = reports / "reconciliation.json"
        run_stage(
            "reconciliation",
            SCRIPTS / "reconcile_line_item_details.py",
            [
                "--otd", staged_otd,
                "--details", line_details_path,
                "--out", reconciliation_path,
                "--posture", "hard_error",
            ],
        )

        with staged_otd.open(encoding="utf-8") as stream:
            document = yaml.safe_load(stream)
        if document["otd"]["source_document"]["sha256"] != source_sha:
            raise DemoError("assembled OTD source hash disagrees with PDF")

        confidence_path = staging / "output.confidence.json"
        confidence = json.loads(confidence_path.read_text(encoding="utf-8"))
        if confidence.get("validation_passes") is not True:
            raise DemoError("validator did not mark confidence manifest as passing")

        disposition = json.loads(disposition_ledger_path.read_text(encoding="utf-8"))
        projection = json.loads(
            projection_manifest_path.read_text(encoding="utf-8")
        )
        reconciliation = json.loads(
            reconciliation_path.read_text(encoding="utf-8")
        )
        if reconciliation.get("counts", {}).get("mismatch", 0) != 0:
            raise DemoError("face/detail reconciliation contains mismatches")

        omitted_detail_keys = {
            (entry.get("box_key"), entry.get("code"))
            for entry in disposition.get("entries", [])
            if entry.get("kind") == "line_item_detail"
            and str(entry.get("disposition", "")).startswith("excluded")
        }
        unexpected_missing = [
            entry for entry in reconciliation.get("results", [])
            if entry.get("status") == "face_missing"
            and (entry.get("box_key"), entry.get("code")) not in omitted_detail_keys
        ]
        if unexpected_missing:
            raise DemoError(
                "reconciliation has unledgered face-missing records: %r"
                % unexpected_missing
            )

        confidence.update({
            "profile_id": PROFILE_ID,
            "source_pdf_sha256": source_sha,
            "omission_count": disposition["counts"]["omission_entries"],
            "full_source_document_extraction": False,
            "unsupported_sections_inferred_or_defaulted": False,
        })
        write_json(confidence_path, confidence)

        publish_root = output_root.parent / (
            ".%s.publish-%s" % (output_root.name, uuid.uuid4().hex)
        )
        publish_root.mkdir(parents=True)
        public_files = {
            "output.otd.yaml": staged_otd,
            "output.confidence.json": confidence_path,
            "evidence-receipt.json": evidence_receipt_path,
            "projection-manifest.json": projection_manifest_path,
            "disposition-ledger.json": disposition_ledger_path,
            "reconciliation.json": reconciliation_path,
        }
        for relative_name, source in public_files.items():
            copy_public(source, publish_root / relative_name)

        for fragment_name in (
            "face_page.json",
            "overflow_statements.json",
            "footnotes_a.json",
            "footnotes_b.json",
            "state_schedules.json",
        ):
            copy_public(
                fragments / fragment_name,
                publish_root / "fragments" / fragment_name,
            )

        status = {
            "schema_version": "otd-bounded-demo-status/1.0",
            "profile_id": PROFILE_ID,
            "outcome": "successful_bounded_pdf_to_otd",
            "source": {
                "pdf_name": processing_pdf.name,
                "sha256": source_sha,
                "page_count": 27,
                "synthetic_and_fictitious": True,
            },
            "claims": {
                "started_from_pdf_bytes": True,
                "face_evidence_projected": True,
                "otd_assembled": True,
                "otd_conformance_validated": True,
                "supported_profile_face_detail_reconciled": (
                    reconciliation.get("counts", {}).get("mismatch", 0) == 0
                ),
                "all_source_detail_records_reconciled": False,
                "full_source_document_extraction": False,
                "unsupported_sections_inferred_or_defaulted": False,
                "historical_fragments_or_expected_values_used": False,
            },
            "counts": {
                "face_statuses": face.get("status_counts"),
                "projected_face_fields": projection["counts"]["projected_face_fields"],
                "source_detail_records": projection["counts"]["source_detail_records"],
                "projected_detail_records": projection["counts"]["projected_detail_records"],
                "disposition_ledger_entries": disposition["counts"]["entries"],
                "omission_entries": disposition["counts"]["omission_entries"],
                "unresolved_sections": disposition["counts"]["unresolved_sections"],
                "reconciliation": reconciliation.get("counts", {}),
            },
        }
        status_path = publish_root / "extraction-status.json"
        write_json(status_path, status)

        artifacts = {}
        for path in sorted(publish_root.rglob("*")):
            if path.is_file() and path.name != "run-manifest.json":
                artifacts[path.relative_to(publish_root).as_posix()] = sha256_file(path)

        final_source_closure = source_closure()
        if final_source_closure != initial_source_closure:
            raise DemoError("executed source closure changed during the run")

        run_manifest = {
            "schema_version": "otd-bounded-demo-run/1.0",
            "profile_id": PROFILE_ID,
            "repository": {
                "source_closure_sha256": canonical_sha256(initial_source_closure),
                "clean_tree_required": bool(args.require_clean),
            },
            "orchestrator_sha256": sha256_file(Path(__file__)),
            "grammar_sha256": sha256_file(GRAMMAR),
            "taxonomy_sha256": sha256_file(TAXONOMY),
            "source_closure": initial_source_closure,
            "source_closure_sha256": canonical_sha256(initial_source_closure),
            "dependencies": dependencies,
            "source": {
                "pdf_name": processing_pdf.name,
                "sha256": source_sha,
                "page_count": 27,
            },
            "determinism": {
                "document_id": document_id,
                "created": created,
            },
            "publication": {
                "atomic_bundle_rename": True,
                "private_work_root": True,
            },
            "stages": stages,
            "artifacts": artifacts,
            "claim": (
                "Starting from the identified PDF bytes, this command generated "
                "and validated an OTD document for the declared supported profile "
                "while explicitly ledgering all excluded source evidence."
            ),
        }
        write_json(publish_root / "run-manifest.json", run_manifest)

        expected_paths = set(public_files) | {
            "fragments/" + name for name in (
                "face_page.json",
                "overflow_statements.json",
                "footnotes_a.json",
                "footnotes_b.json",
                "state_schedules.json",
            )
        } | {"extraction-status.json", "run-manifest.json"}
        actual_paths = {
            path.relative_to(publish_root).as_posix()
            for path in publish_root.rglob("*") if path.is_file()
        }
        if actual_paths != expected_paths:
            raise DemoError(
                "public bundle inventory mismatch: expected %r, received %r"
                % (sorted(expected_paths), sorted(actual_paths))
            )
        if args.fault_before_publish:
            raise DemoError("intentional fault before atomic publication")

        os.replace(publish_root, output_root)
        publish_root = None
        published_bundle = True
        for relative_name in sorted(actual_paths):
            print("WROTE %s" % (output_root / relative_name))
        print("RESULT: PASS")
        return 0

    except Exception as exc:
        if publish_root is not None and publish_root.exists():
            shutil.rmtree(publish_root, ignore_errors=True)
        failure = {
            "schema_version": "otd-bounded-demo-failure/1.0",
            "source_sha256": source_sha,
            "profile_id": PROFILE_ID,
            "error": str(exc),
            "stages": stages,
            "published_bundle": published_bundle or output_root.exists(),
        }
        if auto_work:
            print("RUN FAILURE: %s" % json.dumps(failure), file=sys.stderr)
        else:
            write_json(work_root / "run-failure.json", failure)
        raise
    finally:
        if auto_work and work_root.exists():
            shutil.rmtree(work_root, ignore_errors=True)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except DemoError as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        sys.exit(2)
