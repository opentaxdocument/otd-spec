#!/usr/bin/env python
"""Project deterministic K-1 extraction evidence into assembler fragments.

This bridge is deliberately narrower than a general PDF-to-OTD converter. It
supports the 2025 K-1 face plus taxonomy-safe numeric totals from parsed
LINE NN detail blocks. Statement-dependent content, state grids, and logical
sections outside that profile are excluded through a machine-readable ledger.

No value is sourced from checked-in expected files or historical fragments.
"""

import argparse
import datetime
import hashlib
import json
import sys
from pathlib import Path

try:
    import yaml
except ImportError as exc:  # pragma: no cover
    print("ERROR: missing dependency: %s" % exc, file=sys.stderr)
    raise


PROFILE_ID = "k1-1065-2025-face-and-numeric-details/1.0"
SCALAR_BOXES = (
    "box_1", "box_2", "box_3", "box_4a", "box_4b", "box_4c",
    "box_5", "box_6a", "box_6b", "box_6c", "box_7", "box_8",
    "box_9a", "box_9b", "box_9c", "box_10", "box_12", "box_21",
    "box_22", "box_23",
)
CODED_BOXES = (
    "box_11", "box_13", "box_14", "box_15",
    "box_17", "box_18", "box_19", "box_20",
)
NUMERIC_VALUE_TYPES = {"decimal", "integer", "percentage"}


class ProjectionError(RuntimeError):
    pass


def load_json(path):
    with Path(path).open(encoding="utf-8-sig") as stream:
        return json.load(stream)


def write_json(path, value):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(value, indent=2, sort_keys=False, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print("WROTE %s" % target.resolve())


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_evidence_receipt(receipt, source_sha, artifact_paths):
    if not isinstance(receipt, dict):
        raise ProjectionError("evidence receipt must be an object")
    if receipt.get("profile_id") != PROFILE_ID:
        raise ProjectionError("evidence receipt profile does not match projector")
    if str(receipt.get("source_sha256", "")).lower() != source_sha:
        raise ProjectionError("evidence receipt source hash does not match requested PDF")
    declared = receipt.get("artifacts")
    if not isinstance(declared, dict):
        raise ProjectionError("evidence receipt has no artifacts object")
    if set(declared) != set(artifact_paths):
        raise ProjectionError(
            "evidence receipt artifact set mismatch: expected %s, received %s"
            % (sorted(artifact_paths), sorted(declared))
        )
    for name, path in artifact_paths.items():
        expected = declared.get(name)
        if not isinstance(expected, str) or len(expected) != 64:
            raise ProjectionError(
                "evidence receipt has invalid digest for %s" % name
            )
        actual = file_sha256(path)
        if actual != expected.lower():
            raise ProjectionError(
                "evidence artifact %s does not match its receipt" % name
            )


def canonical_face_sha256(face):
    canonical = dict(face)
    canonical.pop("extraction_timestamp_utc", None)
    payload = json.dumps(
        canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def parse_face_date(value, field_name):
    if not isinstance(value, str):
        raise ProjectionError("%s is not a date string" % field_name)
    for pattern in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(value, pattern).date()
        except ValueError:
            continue
    raise ProjectionError("%s has unsupported date value %r" % (field_name, value))


def taxonomy_children(taxonomy, part_name):
    root = taxonomy.get("nodes", taxonomy)
    part = root.get(part_name, {}) if isinstance(root, dict) else {}
    children = part.get("children") if isinstance(part, dict) else None
    return children if isinstance(children, dict) else (
        part if isinstance(part, dict) else {}
    )


def taxonomy_code_declaration(taxonomy, box_key, code):
    box = taxonomy_children(taxonomy, "part_iii").get(box_key, {})
    codes = box.get("codes", {}) if isinstance(box, dict) else {}
    declaration = codes.get(code) if isinstance(codes, dict) else None
    return declaration if isinstance(declaration, dict) else None


def extract_nested_cell(container, *path, allow_blank=False):
    current = container
    for key in path:
        if not isinstance(current, dict) or key not in current:
            raise ProjectionError("missing nested evidence cell: %s" % ".".join(path))
        current = current[key]
    if not isinstance(current, dict):
        raise ProjectionError("evidence cell %s is not an object" % ".".join(path))
    status = current.get("status")
    if status == "present":
        if current.get("value") is None:
            raise ProjectionError(
                "present evidence cell %s has no value" % ".".join(path)
            )
        return current.get("value")
    if allow_blank and status == "blank":
        return None
    raise ProjectionError(
        "evidence cell %s has unsupported status %r" % (".".join(path), status)
    )


def boolean_from_choice(value, field_name):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"yes", "true", "checked"}:
            return True
        if normalized in {"no", "false", "unchecked"}:
            return False
    raise ProjectionError("%s has unsupported boolean choice %r" % (field_name, value))


def masked_identifier(value):
    if not isinstance(value, str):
        return False
    upper = value.upper()
    return "*" in value or "X" in upper


def section_identity(section, fallback_index):
    page = (
        section.get("page")
        or section.get("page_number")
        or section.get("source_page")
    )
    index = (
        section.get("section_index")
        if section.get("section_index") is not None
        else section.get("index", fallback_index)
    )
    return page, index


def normalized_page(value):
    text = str(value or "")
    digits = "".join(ch for ch in text if ch.isdigit())
    if digits:
        return int(digits)
    return text


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Project K-1 extraction evidence into reviewed assembler fragments"
    )
    parser.add_argument("--face-evidence", required=True)
    parser.add_argument("--line-details", required=True)
    parser.add_argument("--page-manifest", required=True)
    parser.add_argument("--section-manifest", required=True)
    parser.add_argument("--state-attempt", required=True)
    parser.add_argument("--evidence-receipt", required=True)
    parser.add_argument("--taxonomy", required=True)
    parser.add_argument("--source-sha256", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--projection-manifest", required=True)
    parser.add_argument(
        "--disposition-ledger", "--omission-ledger",
        dest="disposition_ledger", required=True,
    )
    args = parser.parse_args(argv)

    source_sha = args.source_sha256.lower()
    if len(source_sha) != 64 or any(ch not in "0123456789abcdef" for ch in source_sha):
        raise ProjectionError("--source-sha256 must be a lowercase SHA-256 digest")

    artifact_paths = {
        "face_evidence": Path(args.face_evidence),
        "line_details": Path(args.line_details),
        "page_manifest": Path(args.page_manifest),
        "section_manifest": Path(args.section_manifest),
        "state_attempt": Path(args.state_attempt),
    }
    receipt_path = Path(args.evidence_receipt)
    receipt = load_json(receipt_path)
    validate_evidence_receipt(receipt, source_sha, artifact_paths)
    receipt_sha = file_sha256(receipt_path)

    face_path = artifact_paths["face_evidence"]
    face = load_json(face_path)
    details = load_json(artifact_paths["line_details"])
    page_manifest = load_json(artifact_paths["page_manifest"])
    section_manifest = load_json(artifact_paths["section_manifest"])
    state_attempt = load_json(artifact_paths["state_attempt"])
    with Path(args.taxonomy).open(encoding="utf-8-sig") as stream:
        taxonomy = yaml.safe_load(stream) or {}

    if face.get("source_pdf_sha256", "").lower() != source_sha:
        raise ProjectionError("face evidence source hash does not match requested PDF")
    if not isinstance(face.get("fields"), dict):
        raise ProjectionError("face evidence has no fields object")
    if not isinstance(details, list):
        raise ProjectionError("line detail evidence must be a list")

    fields = face["fields"]
    scalar_geometry = {}
    for key in SCALAR_BOXES:
        event = fields.get(key)
        if not isinstance(event, dict) or event.get("status") != "present":
            continue
        bbox = event.get("bbox")
        if not isinstance(bbox, list) or len(bbox) != 4:
            raise ProjectionError(
                "present scalar field %s has no four-coordinate bbox" % key
            )
        signature = tuple(round(float(value), 6) for value in bbox)
        prior = scalar_geometry.get(signature)
        if (
            prior is not None
            and prior["normalized_value"] != event.get("normalized_value")
        ):
            raise ProjectionError(
                "incompatible scalar fields %s and %s claim identical source "
                "geometry %r" % (prior["field"], key, bbox)
            )
        scalar_geometry[signature] = {
            "field": key,
            "normalized_value": event.get("normalized_value"),
        }

    consumed = set()
    projected_face = []
    omitted_face = []
    excluded_rows = []
    derived_facts = []
    detail_dispositions = []

    def source_event(key, output_paths, transformations=None):
        if key in consumed:
            raise ProjectionError("face field %s was consumed more than once" % key)
        event = fields.get(key)
        if not isinstance(event, dict):
            raise ProjectionError("face field %s is missing" % key)
        status = event.get("status")
        if status not in {"present", "verified_absent", "blank"}:
            raise ProjectionError(
                "face field %s cannot be projected from status %r" % (key, status)
            )
        if status == "present" and event.get("bbox") is None:
            raise ProjectionError(
                "present face field %s has no bounding-box evidence" % key
            )
        consumed.add(key)
        projected_face.append({
            "source_field": key,
            "output_paths": list(output_paths),
            "status": status,
            "page": event.get("page"),
            "bbox": event.get("bbox"),
            "raw_text": event.get("raw_text"),
            "normalized_value": event.get("normalized_value"),
            "method": event.get("method"),
            "rule_id": event.get("rule_id"),
            "transformations": list(transformations or []),
        })
        return event

    def omit_face(key, reason_code, disposition):
        if key in consumed:
            raise ProjectionError("face field %s was consumed more than once" % key)
        event = fields.get(key)
        if not isinstance(event, dict):
            raise ProjectionError("face field %s is missing" % key)
        consumed.add(key)
        record = {
            "id": "face-%s" % key.replace(".", "-"),
            "kind": "face_field",
            "source_field": key,
            "source_sha256": source_sha,
            "page": event.get("page"),
            "status": event.get("status"),
            "reason_code": reason_code,
            "disposition": disposition,
            "claim_impact": "not represented as an OTD fact",
        }
        omitted_face.append(record)
        return event

    final_event = source_event(
        "header.final_k1", ["form_metadata.final"],
        ["verified checkbox absence preserved as false"],
    )
    amended_event = source_event(
        "header.amended_k1", ["form_metadata.amended", "form_metadata.filing_status"],
        ["verified checkbox absence preserved as false", "filing status derived"],
    )
    begin_event = source_event(
        "header.tax_year_begin",
        ["form_metadata.fiscal_year_begin", "form_metadata.fiscal_year"],
        ["M/D/YYYY normalized to ISO-8601", "calendar/fiscal indicator derived"],
    )
    end_event = source_event(
        "header.tax_year_end",
        ["form_metadata.fiscal_year_end", "form_metadata.tax_year",
         "form_metadata.fiscal_year"],
        ["M/D/YYYY normalized to ISO-8601", "tax year derived from ending date",
         "calendar/fiscal indicator derived"],
    )

    begin_date = parse_face_date(
        begin_event.get("normalized_value"), "header.tax_year_begin"
    )
    end_date = parse_face_date(
        end_event.get("normalized_value"), "header.tax_year_end"
    )
    fiscal_year = not (
        begin_date.year == end_date.year
        and begin_date.month == 1 and begin_date.day == 1
        and end_date.month == 12 and end_date.day == 31
    )
    amended = amended_event.get("normalized_value")
    final = final_event.get("normalized_value")
    if not isinstance(amended, bool) or not isinstance(final, bool):
        raise ProjectionError("Final/Amended evidence did not resolve to booleans")

    form_metadata = {
        "tax_year": end_date.year,
        "fiscal_year": fiscal_year,
        "fiscal_year_begin": begin_date.isoformat(),
        "fiscal_year_end": end_date.isoformat(),
        "amended": amended,
        "final": final,
        "filing_status": "amended" if amended else "original",
        "form_revision_date": str(end_date.year),
    }

    part_i_map = {
        "item_a": ("partnership_ein", "part_i.partnership_ein"),
        "item_b": ("partnership_name", "part_i.partnership_name"),
        "item_c": ("irs_center", "part_i.irs_center"),
        "item_d": ("publicly_traded", "part_i.publicly_traded"),
    }
    part_i = {}
    for source_key, (target_key, output_path) in part_i_map.items():
        event = source_event(source_key, [output_path])
        part_i[target_key] = event.get("normalized_value")
    part_i["_partnership_ein_masked"] = masked_identifier(
        part_i.get("partnership_ein")
    )

    simple_part_ii = {
        "item_e": ("partner_tin", "part_ii.partner_tin"),
        "item_f": ("partner_name", "part_ii.partner_name"),
        "item_g": ("general_or_limited", "part_ii.general_or_limited"),
        "item_h1": ("domestic_or_foreign", "part_ii.domestic_or_foreign"),
        "item_h2": ("disregarded_entity_info", "part_ii.disregarded_entity_info"),
        "item_i1": ("entity_type", "part_ii.entity_type"),
        "item_i2": ("retirement_plan", "part_ii.retirement_plan"),
        "item_k2": ("item_k2", "part_ii.item_k2"),
        "item_k3": ("item_k3", "part_ii.item_k3"),
    }
    part_ii = {}
    for source_key, (target_key, output_path) in simple_part_ii.items():
        event = source_event(source_key, [output_path])
        part_ii[target_key] = event.get("normalized_value")
    part_ii["_partner_tin_masked"] = masked_identifier(part_ii.get("partner_tin"))

    item_j_event = source_event(
        "item_j", ["part_ii.share_percentages"],
        ["nested evidence cells flattened", "percentage points divided by 100"],
    )
    item_j_raw = item_j_event.get("normalized_value")
    shares = {}
    for category in ("profit", "loss", "capital"):
        for period in ("beginning", "ending"):
            value = extract_nested_cell(item_j_raw, category, period)
            shares["%s_%s" % (category, period)] = float(value) / 100.0

    decrease_event = source_event(
        "item_j.decrease_reason",
        ["part_ii.share_percentages.decrease_due_to_sale",
         "part_ii.share_percentages.decrease_due_to_exchange"],
        ["verified checkbox absence preserved as false"],
    )
    decrease_raw = decrease_event.get("normalized_value")
    for source_name, target_name in (
        ("sale", "decrease_due_to_sale"),
        ("exchange", "decrease_due_to_exchange"),
    ):
        cell = decrease_raw.get(source_name) if isinstance(decrease_raw, dict) else None
        if not isinstance(cell, dict) or cell.get("status") != "verified_absent":
            raise ProjectionError(
                "item_j.decrease_reason.%s was not verified absent" % source_name
            )
        shares[target_name] = False
    part_ii["share_percentages"] = shares

    k1_event = source_event(
        "item_k1", ["part_ii.liabilities"],
        ["nested evidence cells flattened"],
    )
    k1_raw = k1_event.get("normalized_value")
    liabilities = {}
    for category in (
        "nonrecourse", "qualified_nonrecourse_financing", "recourse"
    ):
        for period in ("beginning", "ending"):
            liabilities["%s_%s" % (category, period)] = extract_nested_cell(
                k1_raw, category, period
            )
    part_ii["liabilities"] = liabilities

    capital_event = source_event(
        "item_l", ["part_ii.capital_account"],
        ["nested evidence cells flattened", "partial observed blank quarantined"],
    )
    capital_raw = capital_event.get("normalized_value")
    capital_mapping = (
        ("beginning", "beginning"),
        ("contributions", "contributions"),
        ("current_year_net_income_loss", "current_year_increase_decrease"),
        ("other_increase_decrease", "other_increase_decrease"),
        ("withdrawals_distributions", "withdrawals"),
        ("ending", "ending"),
    )
    capital = {}
    blank_capital = []
    for source_name, target_name in capital_mapping:
        value = extract_nested_cell(
            capital_raw, source_name, allow_blank=True
        )
        capital[target_name] = value
        cell = capital_raw[source_name]
        if cell.get("status") == "blank":
            blank_capital.append(source_name)
    if blank_capital:
        capital_reason = (
            "HUMAN REVIEW REQUIRED: Item L contains observed blank component(s): %s; "
            "capital continuity is not asserted"
            % ", ".join(blank_capital)
        )
        part_ii["capital_account"] = {"_unverified": capital_reason}
        excluded_rows.append({
            "id": "face-item-l-partial-object",
            "kind": "face_partial_object",
            "source_field": "item_l",
            "source_sha256": source_sha,
            "reason_code": "partial_object_contains_observed_blank",
            "disposition": "otd_value_quarantined_as_null",
            "claim_impact": (
                "observed Item L components remain in the projection manifest, "
                "but no partial capital-account fact is asserted"
            ),
        })
    else:
        part_ii["capital_account"] = capital

    omit_face(
        "item_l.basis_method",
        "not_printed_on_2025_form_revision",
        "excluded_verified_form_revision_absence",
    )

    item_m_event = source_event(
        "item_m", ["part_ii.item_m"],
        ["exclusive-choice text normalized to boolean"],
    )
    part_ii["item_m"] = boolean_from_choice(
        item_m_event.get("normalized_value"), "item_m"
    )

    item_n_event = source_event(
        "item_n", ["part_ii.item_n"],
        ["nested evidence cells flattened"],
    )
    item_n_raw = item_n_event.get("normalized_value")
    part_ii["item_n"] = {
        "beginning": extract_nested_cell(item_n_raw, "beginning"),
        "ending": extract_nested_cell(item_n_raw, "ending"),
    }

    part_iii_face = {"_unverified_fields": {}}
    for box_key in SCALAR_BOXES:
        transformations = []
        if box_key == "box_4c":
            transformations = [
                "observed blank preserved as null in the OTD",
                "taxonomy-declared sum retained only as derivation evidence",
            ]
        event = source_event(
            box_key,
            ["part_iii_face.%s" % box_key],
            transformations,
        )
        value = event.get("normalized_value")
        if box_key == "box_4c" and event.get("status") == "blank":
            left = fields["box_4a"].get("normalized_value")
            right = fields["box_4b"].get("normalized_value")
            if (
                isinstance(left, bool)
                or isinstance(right, bool)
                or not isinstance(left, (int, float))
                or not isinstance(right, (int, float))
            ):
                raise ProjectionError(
                    "Box 4c cannot be derived because Box 4a or 4b is non-numeric"
                )
            derived_value = float(left) + float(right)
            derived_facts.append({
                "output_path": "part_iii_face.box_4c",
                "operation": "sum",
                "taxonomy_constraint": "box_4c_equals_4a_plus_4b",
                "operands": {
                    "part_iii_face.box_4a": left,
                    "part_iii_face.box_4b": right,
                },
                "result": derived_value,
            })
            value = None
            part_iii_face["_unverified_fields"]["box_4c"] = (
                "HUMAN REVIEW REQUIRED: printed Box 4c was observed blank; "
                "no source value is asserted. A taxonomy-derived comparison "
                "is retained separately in the projection manifest."
            )
        part_iii_face[box_key] = value

    box_16_event = source_event(
        "box_16", ["part_iii_face.box_16_checked"],
        ["reference checkbox status normalized to boolean"],
    )
    part_iii_face["box_16_checked"] = box_16_event.get("normalized_value")

    existing_face_codes = {}
    for box_key in CODED_BOXES:
        event = source_event(box_key, ["part_iii_face.%s" % box_key])
        raw_entries = event.get("normalized_value")
        if not isinstance(raw_entries, list):
            raise ProjectionError("%s evidence is not a coded-entry list" % box_key)
        emitted = []
        existing = {}
        for index, raw in enumerate(raw_entries):
            if not isinstance(raw, dict):
                raise ProjectionError("%s entry %d is not an object" % (box_key, index))
            code = str(raw.get("code", "")).strip().upper()
            value = raw.get("value")
            if code == "*" or raw.get("statement_reference") is True:
                excluded_rows.append({
                    "id": "face-%s-statement-reference-%d" % (box_key, index + 1),
                    "kind": "face_statement_reference",
                    "source_field": box_key,
                    "source_row_index": index,
                    "source_sha256": source_sha,
                    "page": event.get("page"),
                    "raw_text": raw.get("raw_text"),
                    "reason_code": "statement_target_not_parsed",
                    "disposition": "excluded_no_statement_fabricated",
                    "claim_impact": "attachment content is outside the supported profile",
                })
                continue
            if not code or value is None:
                excluded_rows.append({
                    "id": "face-%s-unusable-row-%d" % (box_key, index + 1),
                    "kind": "face_coded_row",
                    "source_field": box_key,
                    "source_row_index": index,
                    "source_sha256": source_sha,
                    "page": event.get("page"),
                    "raw_text": raw.get("raw_text"),
                    "reason_code": "coded_row_missing_code_or_value",
                    "disposition": "excluded",
                    "claim_impact": "coded row is not represented as a fact",
                })
                continue
            declaration = taxonomy_code_declaration(taxonomy, box_key, code)
            if declaration is None:
                excluded_rows.append({
                    "id": "face-%s-unknown-code-%s" % (box_key, code),
                    "kind": "face_coded_row",
                    "source_field": box_key,
                    "source_row_index": index,
                    "source_sha256": source_sha,
                    "page": event.get("page"),
                    "raw_text": raw.get("raw_text"),
                    "code": code,
                    "reason_code": "taxonomy_has_no_matching_code",
                    "disposition": "excluded_unknown_taxonomy_code",
                    "claim_impact": "coded row is not represented as a fact",
                })
                continue
            requires_statement = (
                declaration.get("requires_statement") is True
                or declaration.get("statement_required") is True
            )
            if declaration.get("requires_classification") is True or requires_statement:
                reason_code = (
                    "statement_not_parsed"
                    if requires_statement
                    else "classification_not_parsed"
                )
                disposition = (
                    "excluded_requires_statement"
                    if requires_statement
                    else "excluded_requires_classification"
                )
                excluded_rows.append({
                    "id": "face-%s-unsupported-code-%s" % (box_key, code),
                    "kind": "face_coded_row",
                    "source_field": box_key,
                    "source_row_index": index,
                    "source_sha256": source_sha,
                    "page": event.get("page"),
                    "raw_text": raw.get("raw_text"),
                    "code": code,
                    "value": value,
                    "reason_code": reason_code,
                    "required_classification": declaration.get(
                        "statement_classification"
                    ),
                    "disposition": disposition,
                    "claim_impact": "coded amount is outside the supported profile",
                })
                continue
            emitted.append({"code": code, "value": value})
            existing[code] = value
        part_iii_face[box_key] = emitted
        existing_face_codes[box_key] = existing

    overflow = {box_key: [] for box_key in CODED_BOXES}
    for record in details:
        if not isinstance(record, dict):
            raise ProjectionError("line detail record is not an object")
        source_page = record.get("source_page")
        line_token = record.get("line_token")
        box_key = record.get("box_key")
        code = str(record.get("code") or "").upper()
        total = record.get("total")
        record_id = "detail-%s-%s" % (
            str(source_page).replace(".", "-"),
            str(line_token).lower(),
        )
        base = {
            "id": record_id,
            "kind": "line_item_detail",
            "source_sha256": source_sha,
            "source_page": source_page,
            "line_token": line_token,
            "box_key": box_key,
            "code": code or None,
            "printed_total": total,
        }

        if record.get("mode") == "scalar":
            detail_dispositions.append({
                **base,
                "disposition": "reconciliation_only",
                "reason_code": "face_scalar_is_canonical_otd_fact",
            })
            continue
        if record.get("mode") != "coded" or box_key not in CODED_BOXES:
            detail_dispositions.append({
                **base,
                "disposition": "excluded",
                "reason_code": "unmatched_or_unsupported_detail_identity",
            })
            continue
        if total is None:
            detail_dispositions.append({
                **base,
                "disposition": "excluded_no_printed_total",
                "reason_code": "document_printed_no_total",
            })
            continue
        if code in existing_face_codes.get(box_key, {}):
            face_value = existing_face_codes[box_key][code]
            if abs(float(face_value) - float(total)) > 0.01:
                raise ProjectionError(
                    "%s %s face/detail mismatch before assembly: %r != %r"
                    % (box_key, code, face_value, total)
                )
            detail_dispositions.append({
                **base,
                "disposition": "reconciliation_only",
                "reason_code": "already_projected_from_face",
            })
            continue

        declaration = taxonomy_code_declaration(taxonomy, box_key, code)
        if declaration is None:
            detail_dispositions.append({
                **base,
                "disposition": "excluded_unknown_taxonomy_code",
                "reason_code": "taxonomy_has_no_matching_code",
            })
            continue
        if declaration.get("requires_classification") is True:
            detail_dispositions.append({
                **base,
                "disposition": "excluded_requires_classification",
                "reason_code": "classification_not_parsed",
            })
            continue
        if declaration.get("requires_statement") is True:
            detail_dispositions.append({
                **base,
                "disposition": "excluded_requires_statement",
                "reason_code": "statement_not_parsed",
            })
            continue
        if declaration.get("value_type") not in NUMERIC_VALUE_TYPES:
            detail_dispositions.append({
                **base,
                "disposition": "excluded_non_numeric_taxonomy_payload",
                "reason_code": "numeric_total_cannot_satisfy_taxonomy_payload",
            })
            continue

        overflow[box_key].append({"code": code, "value": total})
        detail_dispositions.append({
            **base,
            "disposition": "projected_overflow",
            "output_path": "part_iii_overflow.%s[%s]" % (box_key, code),
            "reason_code": "taxonomy_safe_numeric_total",
        })

    for box_key in overflow:
        overflow[box_key].sort(key=lambda item: item["code"])
    overflow = {key: value for key, value in overflow.items() if value}

    unconsumed = sorted(set(fields) - consumed)
    if unconsumed:
        raise ProjectionError(
            "face evidence partition is incomplete; unconsumed fields: %s"
            % ", ".join(unconsumed)
        )

    source_common = {
        "_profile_id": PROFILE_ID,
        "_source_pdf_sha256": source_sha,
        "_evidence_receipt_sha256": receipt_sha,
    }
    fragments = {
        "face_page.json": {
            **source_common,
            "form_metadata": form_metadata,
            "part_i": part_i,
            "part_ii": part_ii,
            "part_iii_face": part_iii_face,
        },
        "overflow_statements.json": {
            **source_common,
            "part_iii_overflow": overflow,
            "_excluded_from_overflow_entries": [],
        },
        "footnotes_a.json": {
            **source_common,
            "footnotes": [],
            "_reviewed_exclusion": "No deterministic statement parser is in this profile.",
        },
        "footnotes_b.json": {
            **source_common,
            "footnotes": [],
            "_reviewed_exclusion": "No deterministic statement parser is in this profile.",
        },
        "state_schedules.json": {
            **source_common,
            "activity_schedule": {},
            "state_grids": [],
            "state_tax_summary": [],
            "_escalate": bool(state_attempt.get("_escalate")),
            "_escalation_reason": state_attempt.get("_escalation_reason"),
        },
    }

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, fragment in fragments.items():
        write_json(out_dir / name, fragment)

    fragment_sha256 = {
        name: file_sha256(out_dir / name) for name in sorted(fragments)
    }

    unresolved = page_manifest.get("unresolved_sections", [])
    if not isinstance(unresolved, list):
        raise ProjectionError("page manifest unresolved_sections must be a list")
    unresolved_keys = set()
    for index, section in enumerate(unresolved):
        if not isinstance(section, dict):
            continue
        page, section_index = section_identity(section, index)
        unresolved_keys.add((str(page), str(section_index)))

    consumed_detail_pages = {
        normalized_page(item.get("source_page"))
        for item in detail_dispositions
        if item.get("disposition") in {"projected_overflow", "reconciliation_only"}
    }
    section_dispositions = []
    seen_section_ids = set()
    sections = section_manifest.get("sections", [])
    if not isinstance(sections, list):
        raise ProjectionError("section manifest sections must be a list")
    for index, section in enumerate(sections):
        if not isinstance(section, dict):
            continue
        page, section_index = section_identity(section, index)
        if str(page) in {"1", "page_01.txt", "page_1.txt"}:
            continue
        section_id = "section-page-%s-index-%s" % (page, section_index)
        if section_id in seen_section_ids:
            continue
        seen_section_ids.add(section_id)
        is_unresolved = (str(page), str(section_index)) in unresolved_keys
        role = section.get("role") or section.get("section_role")
        partially_consumed = (
            not is_unresolved
            and role == "line_item_detail"
            and normalized_page(page) in consumed_detail_pages
        )
        section_dispositions.append({
            "id": section_id,
            "kind": "logical_section",
            "source_sha256": source_sha,
            "page": page,
            "section_index": section_index,
            "role": role,
            "dominant_shape": section.get("dominant_shape"),
            "reason_code": (
                "unresolved_section_requires_review"
                if is_unresolved
                else "numeric_total_consumed_components_excluded"
                if partially_consumed
                else "section_content_outside_supported_profile"
            ),
            "disposition": (
                "excluded_no_content_inferred"
                if is_unresolved or not partially_consumed
                else "partially_consumed_numeric_total_only"
            ),
            "claim_impact": "full source-document extraction is false",
        })

    for index, section in enumerate(unresolved):
        if not isinstance(section, dict):
            continue
        page, section_index = section_identity(section, index)
        section_id = "section-page-%s-index-%s" % (page, section_index)
        if section_id in seen_section_ids:
            continue
        seen_section_ids.add(section_id)
        section_dispositions.append({
            "id": section_id,
            "kind": "unresolved_section",
            "source_sha256": source_sha,
            "page": page,
            "section_index": section_index,
            "dominant_shape": section.get("dominant_shape"),
            "reason": section.get("reason"),
            "reason_code": "unresolved_section_requires_review",
            "disposition": "excluded_no_content_inferred",
            "claim_impact": "full source-document extraction is false",
        })

    capability_omissions = [
        {
            "id": "unsupported-investor-statements",
            "kind": "unsupported_capability",
            "source_sha256": source_sha,
            "reason_code": "deterministic_statement_parser_not_implemented",
            "disposition": "excluded_empty_reviewed_fragments",
            "claim_impact": "investor statements and footnotes are excluded",
        },
        {
            "id": "state-grid-escalation",
            "kind": "unsupported_capability",
            "source_sha256": source_sha,
            "reason_code": "state_grid_extractor_escalated_without_values",
            "reason": state_attempt.get("_escalation_reason"),
            "disposition": "excluded_no_values_fabricated",
            "claim_impact": "state schedules are excluded",
        },
    ]
    omitted_details = [
        item for item in detail_dispositions
        if str(item.get("disposition", "")).startswith("excluded")
    ]
    omitted_sections = [
        item for item in section_dispositions
        if str(item.get("disposition", "")).startswith("excluded")
    ]
    partial_sections = [
        item for item in section_dispositions
        if item.get("disposition") == "partially_consumed_numeric_total_only"
    ]
    omission_entries = (
        omitted_face
        + excluded_rows
        + capability_omissions
        + omitted_details
        + omitted_sections
    )
    disposition_entries = (
        omission_entries
        + [
            item for item in detail_dispositions
            if item not in omitted_details
        ]
        + partial_sections
    )

    projected_details = [
        item for item in detail_dispositions
        if item.get("disposition") == "projected_overflow"
    ]
    projection_manifest = {
        "schema_version": "otd-extraction-projection/1.0",
        "profile_id": PROFILE_ID,
        "source": {
            "pdf_name": face.get("source_pdf"),
            "sha256": source_sha,
        },
        "source_artifacts": {
            "evidence_receipt_sha256": receipt_sha,
            "face_evidence_canonical_sha256": canonical_face_sha256(face),
            "line_details_sha256": file_sha256(artifact_paths["line_details"]),
            "page_manifest_sha256": file_sha256(artifact_paths["page_manifest"]),
            "section_manifest_sha256": file_sha256(artifact_paths["section_manifest"]),
            "state_attempt_sha256": file_sha256(artifact_paths["state_attempt"]),
        },
        "fragment_sha256": fragment_sha256,
        "scope": {
            "face_fields": True,
            "taxonomy_safe_numeric_detail_totals": True,
            "detail_components": False,
            "investor_statements": False,
            "state_schedules": False,
            "full_source_document": False,
        },
        "counts": {
            "source_face_fields": len(fields),
            "projected_face_fields": len(projected_face),
            "omitted_face_fields": len(omitted_face),
            "source_detail_records": len(details),
            "projected_detail_records": len(projected_details),
            "omission_entries": len(omission_entries),
            "partially_consumed_sections": len(partial_sections),
            "derived_facts": len(derived_facts),
        },
        "projected_face_fields": projected_face,
        "projected_detail_records": projected_details,
        "derived_facts": derived_facts,
    }

    disposition_ledger = {
        "schema_version": "otd-evidence-disposition-ledger/1.0",
        "profile_id": PROFILE_ID,
        "source_sha256": source_sha,
        "evidence_receipt_sha256": receipt_sha,
        "claim_boundary": {
            "full_source_document_extraction": False,
            "unsupported_sections_inferred_or_defaulted": False,
            "omissions_are_explicit": True,
        },
        "counts": {
            "entries": len(disposition_entries),
            "omission_entries": len(omission_entries),
            "omitted_face_fields": len(omitted_face),
            "excluded_face_rows": len(excluded_rows),
            "line_detail_dispositions": len(detail_dispositions),
            "excluded_detail_records": len(omitted_details),
            "excluded_logical_sections": len(omitted_sections),
            "partially_consumed_sections": len(partial_sections),
            "unresolved_sections": len(unresolved),
        },
        "entries": disposition_entries,
    }

    write_json(args.projection_manifest, projection_manifest)
    write_json(args.disposition_ledger, disposition_ledger)
    print(
        "PROJECTED: %d/%d face fields | %d/%d detail records | %d omissions"
        % (
            len(projected_face), len(fields),
            len(projected_details), len(details),
            len(omission_entries),
        )
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ProjectionError as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        sys.exit(2)
