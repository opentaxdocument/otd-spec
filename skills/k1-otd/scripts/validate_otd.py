#!/usr/bin/env python
"""K-1 OTD validation with explicit document and taxonomy trust boundaries."""

import argparse
import hashlib
import os
import re
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path

EXIT_CONFORMANT = 0
EXIT_INVALID_DOCUMENT = 1
EXIT_VALIDATOR_ERROR = 2

sys.path.insert(0, str(Path(__file__).resolve().parent))

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

try:
    import simplejson as json
    from otd_values import decimal_yaml, tree_problem
    from constraint_engine import (
        TaxonomyCompilationError,
        compile_taxonomy,
        run_taxonomy_driven_validation,
    )
    _yaml = decimal_yaml()
except ImportError:
    print("ERROR: validator dependency unavailable: install ruamel.yaml and simplejson", file=sys.stderr)
    sys.exit(EXIT_VALIDATOR_ERROR)


REQUIRED = {
    "otd": ["version", "document_id", "created", "producer", "taxonomy"],
    "form_metadata": ["tax_year", "fiscal_year", "amended", "final", "filing_status"],
    "body": ["form_id", "tax_year", "part_i", "part_ii", "part_iii"],
}
VALID_TYPES = {"scalar", "coded", "grid", "recordset", "statement", "reference"}
VALID_FILING_STATUS = {"original", "amended", "superseded", "void"}
REQUIRED_TAXONOMY_IDENTITY = ("id", "version", "form_id", "tax_year")


class ValidatorConfigurationError(RuntimeError):
    """The validator could not establish or execute its governing contract."""


def validate_filing_status(form_metadata, errors, warnings):
    """Enforce the filing-status enum and amendment-chain recommendations."""
    if not isinstance(form_metadata, dict):
        return

    filing_status = form_metadata.get("filing_status")
    if isinstance(filing_status, str):
        if filing_status not in VALID_FILING_STATUS:
            errors.append(
                f"form_metadata.filing_status = '{filing_status}' is not in spec enum "
                f"{sorted(VALID_FILING_STATUS)}"
            )

    if (
        filing_status in ("amended", "superseded")
        and not form_metadata.get("supersedes_document_id")
    ):
        warnings.append(
            f"⚠️ form_metadata.filing_status is '{filing_status}' but "
            "supersedes_document_id is null (spec recommends setting the prior document ID)"
        )

    if form_metadata.get("fiscal_year") is True:
        if not form_metadata.get("fiscal_year_begin"):
            warnings.append("⚠️ fiscal_year=true but fiscal_year_begin is null")
        if not form_metadata.get("fiscal_year_end"):
            warnings.append("⚠️ fiscal_year=true but fiscal_year_end is null")


def find_unverified(obj, path=""):
    found = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key == "_unverified":
                found.append(path)
            else:
                found.extend(
                    find_unverified(value, f"{path}.{key}" if path else key)
                )
    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            found.extend(find_unverified(value, f"{path}[{index}]"))
    return found


def find_statements(obj):
    statements = []
    if isinstance(obj, dict):
        if obj.get("type") == "statement":
            statements.append(obj)
        for value in obj.values():
            statements.extend(find_statements(value))
    elif isinstance(obj, list):
        for value in obj:
            statements.extend(find_statements(value))
    return statements


def check_types(obj, path, warnings):
    """Preserve unknown extension node types and report them informationally."""
    if isinstance(obj, dict):
        node_type = obj.get("type")
        if isinstance(node_type, str) and node_type not in VALID_TYPES:
            warnings.append(
                f"Preserved undeclared extension node type {node_type!r} at {path}"
            )
        for key, value in obj.items():
            check_types(value, f"{path}.{key}" if path else key, warnings)
    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            check_types(value, f"{path}[{index}]", warnings)


def _is_integer(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _require_nonempty_string(mapping, key, path, errors):
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{path}.{key} must be a non-empty string")


def _validate_optional_iso_date(mapping, key, path, errors):
    value = mapping.get(key)
    if value is None or type(value) is date:
        return
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        errors.append(f"{path}.{key} must be an ISO date string or null")
        return
    try:
        date.fromisoformat(value)
    except ValueError:
        errors.append(f"{path}.{key} must be an ISO date string or null")


def _validate_iso_datetime(mapping, key, path, errors):
    value = mapping.get(key)
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            errors.append(f"{path}.{key} must include a timezone")
        return
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{path}.{key} must be an ISO 8601 datetime")
        return

    normalized = value.strip()
    if not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(?:\.\d+)?"
        r"(?:[Zz]|[+-]\d{2}:\d{2})", normalized
    ):
        errors.append(f"{path}.{key} must be an ISO 8601 datetime with timezone")
        return
    if normalized.endswith(("Z", "z")):
        normalized = normalized[:-1] + "+00:00"
    try:
        datetime.fromisoformat(normalized)
    except ValueError:
        errors.append(f"{path}.{key} must be an ISO 8601 datetime")


def _validate_optional_nonempty_string(mapping, key, path, errors):
    value = mapping.get(key)
    if value is None:
        return
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{path}.{key} must be a non-empty string or null")


def validate_document_contract(doc, errors):
    """Validate the root OTD envelope and basic legal/administrative metadata."""
    if not isinstance(doc, dict):
        errors.append("OTD document root must be a mapping")
        return

    sections = {}
    if "statements" in doc:
        statements = doc["statements"]
        if not isinstance(statements, list):
            errors.append("statements must be a sequence")
        else:
            for index, node in enumerate(statements):
                if not isinstance(node, dict) or node.get("type") != "statement":
                    errors.append(f"statements[{index}] must be a statement node")
    for section_name, required_keys in REQUIRED.items():
        section = doc.get(section_name)
        if not isinstance(section, dict):
            if section_name not in doc:
                errors.append(f"Missing required section: {section_name}")
            else:
                errors.append(f"{section_name} must be a mapping")
            continue
        sections[section_name] = section
        for key in required_keys:
            if key not in section:
                errors.append(f"Missing required field: {section_name}.{key}")

    envelope = sections.get("otd")
    if envelope is not None:
        _require_nonempty_string(envelope, "version", "otd", errors)
        _require_nonempty_string(envelope, "document_id", "otd", errors)
        if "created" in envelope:
            _validate_iso_datetime(envelope, "created", "otd", errors)

        producer = envelope.get("producer")
        if isinstance(producer, dict):
            _require_nonempty_string(producer, "name", "otd.producer", errors)
            _require_nonempty_string(producer, "version", "otd.producer", errors)
        elif "producer" in envelope:
            errors.append("otd.producer must be a mapping")

        taxonomy_claim = envelope.get("taxonomy")
        if isinstance(taxonomy_claim, dict):
            _require_nonempty_string(taxonomy_claim, "id", "otd.taxonomy", errors)
            _require_nonempty_string(
                taxonomy_claim, "version", "otd.taxonomy", errors
            )
        elif "taxonomy" in envelope:
            errors.append("otd.taxonomy must be a mapping")

    form_metadata = sections.get("form_metadata")
    if form_metadata is not None:
        tax_year = form_metadata.get("tax_year")
        if not _is_integer(tax_year):
            errors.append("form_metadata.tax_year must be an integer")

        for key in ("fiscal_year", "amended", "final"):
            if key in form_metadata and not isinstance(form_metadata.get(key), bool):
                errors.append(f"form_metadata.{key} must be a boolean")

        if (
            "filing_status" in form_metadata
            and not isinstance(form_metadata.get("filing_status"), str)
        ):
            errors.append("form_metadata.filing_status must be a string")

        _validate_optional_iso_date(
            form_metadata, "fiscal_year_begin", "form_metadata", errors
        )
        _validate_optional_iso_date(
            form_metadata, "fiscal_year_end", "form_metadata", errors
        )
        _validate_optional_nonempty_string(
            form_metadata, "supersedes_document_id", "form_metadata", errors
        )

    body = sections.get("body")
    if body is not None:
        _require_nonempty_string(body, "form_id", "body", errors)
        if not _is_integer(body.get("tax_year")):
            errors.append("body.tax_year must be an integer")
        for key in ("part_i", "part_ii", "part_iii"):
            if key in body and not isinstance(body.get(key), dict):
                errors.append(f"body.{key} must be a mapping")


def resolve_taxonomy_path(input_path, taxonomy_path=None, *, taxonomy_claim=None):
    """Resolve an exact bundled version; explicit files still require identity binding."""
    if taxonomy_path is not None:
        return Path(taxonomy_path)

    # Document strings never become arbitrary filesystem paths.
    bundled = {
        ("irs-k1-1065-2025", "2025.1.0"):
            ("archive", "irs-k1-1065-2025-2025.1.0.yaml"),
        ("irs-k1-1065-2025", "2025.1.1"):
            ("irs-k1-1065-2025.yaml",),
    }
    relative = ("irs-k1-1065-2025.yaml",)
    if isinstance(taxonomy_claim, dict):
        tax_id, version = taxonomy_claim.get("id"), taxonomy_claim.get("version")
        if isinstance(tax_id, str) and isinstance(version, str):
            relative = bundled.get((tax_id, version))
            if relative is None:
                raise ValidatorConfigurationError(
                    f"No bundled taxonomy for {tax_id!r} version {version!r}; "
                    "pass --taxonomy for that exact version"
                )

    module = Path(__file__).resolve()
    roots = [
        *(ancestor / "taxonomies" for ancestor in Path(input_path).resolve().parents),
        module.parents[3] / "taxonomies",
        module.parent.parent / "reference",
    ]
    for root in dict.fromkeys(roots):
        probe = root.joinpath(*relative)
        if probe.is_file():
            return probe

    raise ValidatorConfigurationError(
        "Named taxonomy version could not be resolved; pass --taxonomy explicitly"
    )


def load_taxonomy(taxonomy_path):
    """Load and validate the minimum identity of the governing taxonomy."""
    path = Path(taxonomy_path)
    try:
        with path.open(encoding="utf-8") as stream:
            taxonomy = _yaml.load(stream)
    except FileNotFoundError as exc:
        raise ValidatorConfigurationError(
            f"Taxonomy file does not exist: {path}"
        ) from exc
    except OSError as exc:
        raise ValidatorConfigurationError(
            f"Taxonomy file could not be read: {path}: {exc}"
        ) from exc
    except Exception as exc:
        raise ValidatorConfigurationError(
            f"Taxonomy YAML is malformed: {path}: {exc}"
        ) from exc

    if not isinstance(taxonomy, dict) or not taxonomy:
        raise ValidatorConfigurationError(
            f"Taxonomy document must be a non-empty mapping: {path}"
        )
    problem = tree_problem(taxonomy)
    if problem:
        raise ValidatorConfigurationError(f"Taxonomy is not a bounded tree: {problem}")

    identity = taxonomy.get("taxonomy")
    if not isinstance(identity, dict):
        raise ValidatorConfigurationError(
            f"Taxonomy document has no taxonomy identity mapping: {path}"
        )

    missing = [key for key in REQUIRED_TAXONOMY_IDENTITY if key not in identity]
    if missing:
        raise ValidatorConfigurationError(
            "Taxonomy identity is missing required field(s): " + ", ".join(missing)
        )

    for key in ("id", "version", "form_id"):
        value = identity.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValidatorConfigurationError(
                f"taxonomy.{key} must be a non-empty string"
            )

    if not _is_integer(identity.get("tax_year")):
        raise ValidatorConfigurationError("taxonomy.tax_year must be an integer")

    nodes = taxonomy.get("nodes")
    if not isinstance(nodes, dict) or not nodes:
        raise ValidatorConfigurationError(
            "Taxonomy nodes must be a non-empty mapping"
        )

    constraints = taxonomy.get("constraints")
    if not isinstance(constraints, list) or not constraints:
        raise ValidatorConfigurationError(
            "K-1 taxonomy constraints must be a non-empty sequence"
        )

    try:
        compile_taxonomy(taxonomy)
    except TaxonomyCompilationError as exc:
        raise ValidatorConfigurationError(
            f"Taxonomy compilation failed: {exc}"
        ) from exc

    return taxonomy


def validate_taxonomy_identity(doc, taxonomy, errors):
    """Bind the document's identity to the exact supplied taxonomy."""
    if not isinstance(doc, dict):
        return

    envelope = doc.get("otd")
    form_metadata = doc.get("form_metadata")
    body = doc.get("body")
    identity = taxonomy["taxonomy"]

    if isinstance(envelope, dict):
        claim = envelope.get("taxonomy")
        if isinstance(claim, dict):
            for key in ("id", "version"):
                if claim.get(key) != identity.get(key):
                    errors.append(
                        f"otd.taxonomy.{key} does not match governing taxonomy "
                        f"({claim.get(key)!r} != {identity.get(key)!r})"
                    )

    if isinstance(body, dict) and body.get("form_id") != identity.get("form_id"):
        errors.append(
            "body.form_id does not match governing taxonomy "
            f"({body.get('form_id')!r} != {identity.get('form_id')!r})"
        )

    document_years = []
    if isinstance(form_metadata, dict):
        document_years.append(("form_metadata.tax_year", form_metadata.get("tax_year")))
    if isinstance(body, dict):
        document_years.append(("body.tax_year", body.get("tax_year")))

    for path, value in document_years:
        if value != identity.get("tax_year"):
            errors.append(
                f"{path} does not match governing taxonomy "
                f"({value!r} != {identity.get('tax_year')!r})"
            )


def _build_result(doc, errors, warnings):
    unverified = find_unverified(doc)
    for path in unverified:
        warnings.append(f"⚠️ HUMAN REVIEW at: {path}")

    statements = find_statements(doc)
    for statement in statements:
        semantic = statement.get("semantic")
        if not isinstance(semantic, dict):
            continue
        if (
            semantic.get("classification")
            == "unclassified_requires_review"
        ):
            warnings.append(
                "⚠️ Unclassified statement: "
                + str(semantic.get("id", "unknown"))
            )

    return {
        "passes": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "unverified_count": len(unverified),
        "unverified_paths": unverified[:20],
        "statement_count": len(statements),
    }


def _print_result(result):
    status = "PASS" if result["passes"] else "FAIL"
    print(f"\n=== OTD Validation: {status} ===")
    print(
        f"Errors: {len(result['errors'])} | Warnings: {len(result['warnings'])} | "
        f"Unverified: {result['unverified_count']} | "
        f"Statements: {result['statement_count']}"
    )
    for error in result["errors"]:
        print(f"  ERROR: {error}")
    for warning in result["warnings"][:15]:
        print(f"  WARN:  {warning}")
    if len(result["warnings"]) > 15:
        print(f"  ... +{len(result['warnings']) - 15} more warnings")


def _update_confidence_manifest(input_path, doc, result, confidence_path, input_sha256):
    """Update only an explicitly named companion for the same document."""
    conf_path = Path(confidence_path)
    if conf_path.resolve() == Path(input_path).resolve():
        raise ValidatorConfigurationError("confidence output must not overwrite the OTD input")
    envelope = doc.get("otd") if isinstance(doc, dict) else None
    document_id = envelope.get("document_id") if isinstance(envelope, dict) else None
    if not isinstance(document_id, str) or not document_id.strip():
        raise ValidatorConfigurationError("cannot bind confidence without a document ID")
    try:
        original = conf_path.read_bytes()
        confidence = json.loads(original.decode("utf-8-sig"), use_decimal=True, allow_nan=False)
    except (OSError, ValueError, UnicodeError) as exc:
        raise ValidatorConfigurationError(f"confidence manifest could not be read: {exc}") from exc
    if not isinstance(confidence, dict) or confidence.get("document_id") != document_id:
        raise ValidatorConfigurationError("confidence manifest document_id does not match OTD input")
    confidence["validation_passes"] = result["passes"]
    confidence["validation_errors"] = result["errors"]
    if not input_sha256:
        raise ValidatorConfigurationError("cannot bind confidence without captured input bytes")
    confidence["validated_document_sha256"] = input_sha256
    payload = json.dumps(
        confidence, indent=2, ensure_ascii=False, use_decimal=True, allow_nan=False,
    ) + "\n"
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n",
            dir=conf_path.parent, prefix="." + conf_path.name + ".", suffix=".tmp",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
        if conf_path.read_bytes() != original:
            raise ValidatorConfigurationError("confidence manifest changed during validation")
        if hashlib.sha256(Path(input_path).read_bytes()).hexdigest() != input_sha256:
            raise ValidatorConfigurationError("OTD input changed during validation")
        os.replace(temporary, conf_path)
        temporary = None
    except OSError as exc:
        raise ValidatorConfigurationError(f"confidence manifest could not be updated: {exc}") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    print(f"WROTE {conf_path.resolve().as_posix()}")


def _finish(input_path, doc, errors, warnings, confidence_path=None, input_sha256=None):
    result = _build_result(doc, errors, warnings)
    result["validated_document_sha256"] = input_sha256
    if confidence_path is not None:
        _update_confidence_manifest(input_path, doc, result, confidence_path, input_sha256)
    _print_result(result)
    return result


def validate(input_path, taxonomy_path=None, *, confidence_path=None):
    """Validate without writes unless an explicit confidence companion is supplied."""
    input_sha256 = None
    try:
        input_bytes = Path(input_path).read_bytes()
        input_sha256 = hashlib.sha256(input_bytes).hexdigest()
        doc = _yaml.load(input_bytes.decode("utf-8-sig"))
    except OSError as exc:
        raise ValidatorConfigurationError(
            f"Input document could not be read: {input_path}: {exc}"
        ) from exc
    except Exception as exc:
        return _finish(
            input_path,
            {},
            [f"Invalid YAML syntax in input document: {exc}"],
            [],
            confidence_path,
            input_sha256,
        )

    problem = tree_problem(doc)
    if problem:
        return _finish(input_path, {}, [problem], [], confidence_path, input_sha256)
    errors, warnings = [], []
    validate_document_contract(doc, errors)

    envelope = doc.get("otd") if isinstance(doc, dict) else None
    taxonomy_claim = envelope.get("taxonomy") if isinstance(envelope, dict) else None
    resolved_taxonomy_path = resolve_taxonomy_path(
        input_path, taxonomy_path, taxonomy_claim=taxonomy_claim,
    )
    taxonomy = load_taxonomy(resolved_taxonomy_path)
    validate_taxonomy_identity(doc, taxonomy, errors)

    body = doc.get("body", {}) if isinstance(doc, dict) else {}
    check_types(body, "body", warnings)
    form_metadata = doc.get("form_metadata", {}) if isinstance(doc, dict) else {}
    validate_filing_status(form_metadata, errors, warnings)

    if errors:
        return _finish(input_path, doc, errors, warnings, confidence_path, input_sha256)

    try:
        tax_errors, tax_warnings = run_taxonomy_driven_validation(
            body,
            taxonomy,
            doc.get("statements") if isinstance(doc, dict) else None,
        )
    except Exception as exc:
        raise ValidatorConfigurationError(
            f"Taxonomy evaluation failed: {exc}"
        ) from exc

    errors.extend(tax_errors)
    warnings.extend(tax_warnings)
    return _finish(input_path, doc, errors, warnings, confidence_path, input_sha256)


def main(argv=None):
    parser = argparse.ArgumentParser(description="OTD structural validation")
    parser.add_argument("--input", required=True, help="Path to output.otd.yaml")
    parser.add_argument(
        "--taxonomy",
        default=None,
        help="Path to governing taxonomy YAML (auto-detected if omitted)",
    )
    parser.add_argument(
        "--update-confidence",
        default=None,
        metavar="PATH",
        help="Opt in to updating an existing confidence manifest with a matching document_id",
    )
    args = parser.parse_args(argv)

    try:
        result = validate(
            args.input, taxonomy_path=args.taxonomy,
            confidence_path=args.update_confidence,
        )
    except ValidatorConfigurationError as exc:
        print("\n=== OTD Validation: VALIDATOR ERROR ===", file=sys.stderr)
        print(f"  ERROR: {exc}", file=sys.stderr)
        return EXIT_VALIDATOR_ERROR
    except Exception as exc:
        print("\n=== OTD Validation: VALIDATOR ERROR ===", file=sys.stderr)
        print(f"  ERROR: unexpected validator failure: {exc}", file=sys.stderr)
        return EXIT_VALIDATOR_ERROR

    return EXIT_CONFORMANT if result["passes"] else EXIT_INVALID_DOCUMENT


if __name__ == "__main__":
    sys.exit(main())
