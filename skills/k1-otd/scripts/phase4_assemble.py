
#!/usr/bin/env python
"""K-1 OTD Extraction Skill — Phase 4: OTD Assembly from JSON Fragments
Merges 5 extraction fragments into a compliant OTD YAML document + confidence manifest.
"""
import sys
import json
import uuid
import argparse
import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

try:
    from ruamel.yaml import YAML
    _yaml = YAML()
    _yaml.indent(mapping=2, sequence=4, offset=2)
    _yaml.default_flow_style = False
except ImportError:
    print("ERROR: pip install ruamel.yaml", file=sys.stderr)
    sys.exit(1)


def load(path):
    if not Path(path).exists():
        print(f"WARNING: missing fragment: {path}", file=sys.stderr)
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def scalar(sem_id, label, loc, box, value):
    return {
        "type": "scalar",
        "semantic": {"id": sem_id, "label": label},
        "form": {"form_id": "k1-1065", "location": loc, "box": box},
        "value": value
    }


def count_unverified(obj, path=""):
    count, paths = 0, []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "_unverified":
                count += 1
                paths.append(path)
            else:
                c, p = count_unverified(v, f"{path}.{k}" if path else k)
                count += c
                paths.extend(p)
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            c, p = count_unverified(item, f"{path}[{i}]")
            count += c
            paths.extend(p)
    return count, paths


def count_leaves(obj):
    if isinstance(obj, dict):
        return sum(count_leaves(v) for v in obj.values())
    if isinstance(obj, list):
        return sum(count_leaves(v) for v in obj)
    return 1 if obj is not None else 0


def as_list(obj):
    """Extract a list from various fragment response shapes."""
    if isinstance(obj, list):
        return obj
    if isinstance(obj, dict):
        for v in obj.values():
            if isinstance(v, list) and v:
                return v
    return []


# Part III scalar box definitions: (key, semantic_id, label, location, box)
SCALAR_BOXES = [
    ("box_1",  "ordinary_business_income",     "Ordinary Business Income (Loss)",      "Part III, Box 1",  1),
    ("box_2",  "net_rental_real_estate_income", "Net Rental Real Estate Income (Loss)", "Part III, Box 2",  2),
    ("box_3",  "other_net_rental_income",       "Other Net Rental Income (Loss)",       "Part III, Box 3",  3),
    ("box_4a", "guaranteed_payments_services",  "Guaranteed Payments for Services",     "Part III, Box 4a", "4a"),
    ("box_4b", "guaranteed_payments_capital",   "Guaranteed Payments for Capital",      "Part III, Box 4b", "4b"),
    ("box_4c", "guaranteed_payments_total",     "Total Guaranteed Payments",            "Part III, Box 4c", "4c"),
    ("box_5",  "interest_income",               "Interest Income",                      "Part III, Box 5",  5),
    ("box_6a", "ordinary_dividends",            "Ordinary Dividends",                   "Part III, Box 6a", "6a"),
    ("box_6b", "qualified_dividends",           "Qualified Dividends",                  "Part III, Box 6b", "6b"),
    ("box_7",  "royalties",                     "Royalties",                            "Part III, Box 7",  7),
    ("box_8",  "net_stcg",                      "Net Short-Term Capital Gain (Loss)",   "Part III, Box 8",  8),
    ("box_9a", "net_ltcg",                      "Net Long-Term Capital Gain (Loss)",    "Part III, Box 9a", "9a"),
    ("box_9c", "unrec_1250",                    "Unrecaptured Section 1250 Gain",       "Part III, Box 9c", "9c"),
    ("box_10", "net_1231",                      "Net Section 1231 Gain (Loss)",         "Part III, Box 10", 10),
    ("box_12", "sec_179",                       "Section 179 Deduction",                "Part III, Box 12", 12),
]

CODED_BOXES = {
    "box_11": "Other Income (Loss)",
    "box_13": "Other Deductions",
    "box_14": "Self-Employment Earnings (Loss)",
    "box_15": "Credits",
    "box_17": "AMT Items",
    "box_18": "Tax-Exempt Income and Nondeductible Expenses",
    "box_19": "Distributions",
    "box_20": "Other Information",
}


def main():
    p = argparse.ArgumentParser(description="K-1 OTD Phase 4 — assemble fragments into OTD YAML")
    p.add_argument("--fragments", required=True, help="Path to fragments/ directory")
    p.add_argument("--out", required=True, help="Output OTD YAML path")
    p.add_argument("--sha256", default="not_computed", help="Source PDF SHA-256")
    args = p.parse_args()

    fd = Path(args.fragments)
    face   = load(fd / "face_page.json")
    ov_raw = load(fd / "overflow_statements.json")
    fn_a   = load(fd / "footnotes_a.json")
    fn_b   = load(fd / "footnotes_b.json")
    states = load(fd / "state_schedules.json")

    pi  = face.get("part_i", {}) or {}
    pii = face.get("part_ii", {}) or {}
    fm  = face.get("form_metadata", {}) or {}
    fb  = face.get("part_iii_face", {}) or {}
    ov  = (ov_raw.get("part_iii_overflow", {}) or {}) if isinstance(ov_raw, dict) else {}

    # ── Statements ────────────────────────────────────────────────────────
    statements_root = []
    statement_map = {}

    for i, fn in enumerate(as_list(fn_a) + as_list(fn_b), 1):
        node = {
            "type": "statement",
            "semantic": {
                "id": fn.get("id", f"stmt-{i:03d}"),
                "label": fn.get("label", ""),
                "classification": fn.get("classification", "unclassified_requires_review")
            },
            "form": {"attachment": True, "attachment_sequence": i},
            "cross_references": fn.get("cross_references", []),
            "content": fn.get("content", fn.get("structured", {})),
            "source_text": fn.get("source_text", ""),
            "confidence": fn.get("confidence", 0.5),
            "extraction_method": fn.get("extraction_method", "ai_structured")
        }
        for flag in ("_unverified", "_escalate"):
            if flag in fn:
                node[flag] = fn[flag]
        
        refs = fn.get("cross_references", [])
        if refs:
            for ref in refs:
                statement_map.setdefault(ref.lower(), []).append(node)
        else:
            statements_root.append(node)

    # ── Part III ─────────────────────────────────────────────────────────
    part_iii = {}
    for box_key, sem_id, label, loc, box in SCALAR_BOXES:
        part_iii[box_key] = scalar(sem_id, label, loc, box, fb.get(box_key))

    for box_key, label in CODED_BOXES.items():
        box_num = box_key.replace("box_", "")

        # Gather from face and overflow
        face_list = as_list(fb.get(box_key, []))
        ov_list = as_list(ov.get(box_key, []))

        # Deduplicate by (code, amount) keeping the richest dictionary
        merged_dict = {}
        for raw_entry in face_list + ov_list:
            code = raw_entry.get("code", "")
            amt = raw_entry.get("amount")
            key = (code, amt)
            if key not in merged_dict or len(raw_entry) > len(merged_dict[key]):
                merged_dict[key] = dict(raw_entry)

        entries = []
        for entry_node in merged_dict.values():
            code = entry_node.get("code", "")
            ref_str = f"{box_key}_{code}".lower()
            if ref_str in statement_map and len(statement_map[ref_str]) > 0:
                entry_node["statement"] = statement_map[ref_str].pop(0)
            entries.append(entry_node)

        part_iii[box_key] = {
            "type": "coded",
            "semantic": {"id": f"part_iii.{box_key}", "label": label},
            "form": {
                "form_id": "k1-1065",
                "location": f"Part III, Box {box_num}",
                "box": int(box_num) if box_num.isdigit() else box_num
            },
            "entries": entries
        }

    for remaining_list in statement_map.values():
        statements_root.extend(remaining_list)

    # ── Document ─────────────────────────────────────────────────────────
    run_id = str(uuid.uuid4())
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Normalize filing_status against spec enum (original | amended | superseded | void)
    raw_status = (fm.get("filing_status") or "original").lower()
    if raw_status in ("original", "amended", "superseded", "void"):
        filing_status = raw_status
    elif fm.get("amended"):
        filing_status = "amended"
    else:
        filing_status = "original"

    # Detect masked PII fields and emit redaction declaration
    redacted_fields = []
    if pi.get("_partnership_ein_masked") or (isinstance(pi.get("partnership_ein"), str) and "*" in (pi.get("partnership_ein") or "")):
        redacted_fields.append("body.part_i.partnership_ein")
    if pii.get("_partner_tin_masked") or (isinstance(pii.get("partner_tin"), str) and "*" in (pii.get("partner_tin") or "")):
        redacted_fields.append("body.part_ii.partner_tin")

    # Box 16 reference to K-3 (always emitted; spec-conformant cross-form reference)
    box_16_ref = {
        "type": "reference",
        "semantic": {
            "id": "international_transactions",
            "label": "International Transactions"
        },
        "form": {
            "form_id": "k1-1065",
            "location": "Part III, Box 16",
            "box": 16
        },
        "target": {
            "document_type": "otd",
            "taxonomy_id": "irs-k3-1065-2025",
            "node_path": "/"
        }
    }
    # Insert box_16 between box_15 and box_17 in part_iii (preserve form order)
    ordered_part_iii = {}
    for k in part_iii.keys():
        ordered_part_iii[k] = part_iii[k]
        if k == "box_15":
            ordered_part_iii["box_16"] = box_16_ref
    if "box_16" not in ordered_part_iii:
        ordered_part_iii["box_16"] = box_16_ref
    part_iii = ordered_part_iii

    doc = {
        "otd": {
            "version": "0.1",
            "document_id": run_id,
            "created": now,
            "producer": {"name": "SecondWind K-1 OTD Extractor", "version": "1.1.0"},
            "taxonomy": {
                "id": "irs-k1-1065-2025",
                "version": "2025.1.0",
                "source": "IRS Instructions for Schedule K-1 (Form 1065), 2025"
            },
            "source_document": {
                "sha256": args.sha256,
                "extraction_method": "ai_structured",
                "extraction_date": now
            }
        },
        "form_metadata": {
            "tax_year": fm.get("tax_year"),
            "fiscal_year": fm.get("fiscal_year", False),
            "fiscal_year_begin": fm.get("fiscal_year_begin"),
            "fiscal_year_end": fm.get("fiscal_year_end"),
            "amended": fm.get("amended", False),
            "final": fm.get("final", False),
            "supersedes_document_id": fm.get("supersedes_document_id"),
            "filing_status": filing_status,
            "form_revision_date": fm.get("form_revision_date", "2025")
        },
        "body": {
            "form_id": "k1-1065",
            "part_i": {
                "partnership_name": scalar("partnership.name_address", "Partnership's name, address", "Part I, Item A", "A", pi.get("partnership_name")),
                "partnership_ein":  scalar("partnership.ein",          "Partnership's EIN",           "Part I, Item B", "B", pi.get("partnership_ein")),
                "irs_center":       scalar("partnership.irs_center",   "IRS Center",                  "Part I, Item C", "C", pi.get("irs_center")),
                "publicly_traded":  scalar("partnership.publicly_traded", "Publicly Traded Partnership", "Part I, Item D", "D", pi.get("publicly_traded", False))
            },
            "part_ii": {
                "partner_name":       scalar("partner.name_address",        "Partner's name, address",       "Part II, Item E",  "E",  pii.get("partner_name")),
                "partner_tin":        scalar("partner.identifying_number",  "Partner's identifying number",  "Part II, Item E",  "E",  pii.get("partner_tin")),
                "entity_type":        scalar("partner.entity_type",         "Entity type",                   "Part II, Item F",  "F",  pii.get("entity_type")),
                "general_or_limited": scalar("partner.general_or_limited",  "General or Limited",            "Part II, Item G",  "G",  pii.get("general_or_limited")),
                "share_percentages":  scalar("partner.share_percentages",   "Share percentages",             "Part II, Item J",  "J",  pii.get("share_percentages")),
                "liabilities":        scalar("partner.share_of_liabilities","Liabilities",                   "Part II, Item K1", "K1", pii.get("liabilities")),
                "capital_account":    scalar("partner.capital_account_analysis", "Capital account analysis", "Part II, Item L",  "L",  pii.get("capital_account"))
            },
            "part_iii": part_iii
        },
        "statements": statements_root
    }

    if redacted_fields:
        doc["redaction"] = {
            "policy": "partial",
            "fields_redacted": redacted_fields
        }

    # State schedules as spec-conformant statement nodes (not custom extensions)
    if states and isinstance(states, dict):
        state_grids = states.get("state_grids", []) or []
        state_tax_summary = states.get("state_tax_summary", []) or []
        activity_schedule = states.get("activity_schedule", {}) or {}

        next_seq = len(doc["statements"]) + 1

        # Per-grid statement (one per state_grid type — ECI, UBTI, etc.)
        for grid in state_grids:
            doc["statements"].append({
                "type": "statement",
                "semantic": {
                    "id": f"state_grid_{grid.get('grid_type', 'unknown')}",
                    "label": f"State Grid — {grid.get('grid_type', 'unknown').upper()}",
                    "classification": "state_apportionment"
                },
                "form": {"attachment": True, "attachment_sequence": next_seq},
                "cross_references": [],
                "content": {
                    "grid_type": grid.get("grid_type"),
                    "page": grid.get("page"),
                    "jurisdictions": grid.get("jurisdictions", [])
                },
                "source_text": grid.get("source_text", ""),
                "confidence": 0.85,
                "extraction_method": "ai_structured"
            })
            next_seq += 1

        # Aggregate state tax summary as a single statement
        if state_tax_summary:
            doc["statements"].append({
                "type": "statement",
                "semantic": {
                    "id": "state_tax_summary",
                    "label": "State Tax Summary (Source Income, Withholding, Composite)",
                    "classification": "state_apportionment"
                },
                "form": {"attachment": True, "attachment_sequence": next_seq},
                "cross_references": [],
                "content": {"jurisdictions": state_tax_summary},
                "source_text": "",
                "confidence": 0.85,
                "extraction_method": "ai_structured"
            })
            next_seq += 1

        # Activity schedule retained as supplemental until OTD adopts a canonical class
        if activity_schedule.get("activities"):
            doc["statements"].append({
                "type": "statement",
                "semantic": {
                    "id": "activity_schedule",
                    "label": "Schedule of Activities",
                    "classification": "custom"
                },
                "form": {"attachment": True, "attachment_sequence": next_seq},
                "cross_references": [],
                "content": {
                    "custom_classification": "activity_schedule",
                    "activities": activity_schedule.get("activities", [])
                },
                "source_text": "",
                "confidence": 0.80,
                "extraction_method": "ai_structured"
            })

    # ── Write OTD YAML ────────────────────────────────────────────────────
    # ── Write OTD YAML ────────────────────────────────────────────────────
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        _yaml.dump(doc, f)
    print(f"WROTE {out}")

    # ── Confidence manifest ───────────────────────────────────────────────
    uv_count, uv_paths = count_unverified(doc)
    total = count_leaves(doc)
    conf = out.parent / "output.confidence.json"
    with open(conf, "w", encoding="utf-8") as f:
        json.dump({
            "document_id": run_id,
            "extraction_date": now,
            "total_fields": total,
            "unverified_count": uv_count,
            "unverified_fields": uv_paths[:50],
            "confidence_overall": round(max(0.0, 1.0 - uv_count / max(1, total)), 3),
            "adversary_recommendation": "PENDING"
        }, f, indent=2)
    print(f"WROTE {conf}")
    print(f"\nAssembly complete: {total} fields | {uv_count} unverified markers")


if __name__ == "__main__":
    main()
