#!/usr/bin/env python
"""K-1 OTD Skill -- Face vs. Detail Reconciliation Gate.

Compares the deterministic face reader's box values against the document's
OWN printed subtotal on the supplemental "LINE NN - ... DETAIL" pages (see
build_line_item_details.py). This is an independent cross-check derived
entirely from the source document -- two different regions of the same PDF
must agree on the same number, or the document is internally inconsistent
and a human needs to look at it.

DESIGN RULES (consistent with the project's established posture):
  - `total: null` in a detail record means the document printed no subtotal
    for that block. This is NOT a mismatch -- it is not_applicable. Coercing
    it into a comparison would fabricate a claim the document never made.
  - A real numeric disagreement is a HARD ERROR by default. The posture used
    is always recorded in the report so leniency is never silent.
  - Coded-box comparisons match on (box_key, code) -- never assume ordering.
  - Nothing here is vendor-specific: box/code identity comes from the
    fragment's grammar-derived box_key/code fields, not from a hardcoded
    per-document line-number table.
"""
import argparse
import json
import sys
from pathlib import Path

try:
    import yaml
except ImportError as exc:  # pragma: no cover
    print("ERROR: missing dependency: %s" % exc, file=sys.stderr)
    raise

TOLERANCE = 0.01


def _get(d, *path):
    cur = d
    for p in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(p)
    return cur


def load_otd(otd_path):
    with open(otd_path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def reconcile(otd, details, tolerance=TOLERANCE):
    """Returns (results, posture). results is a list of per-record dicts:
      status in {match, mismatch, not_applicable, face_missing}
    """
    body = otd.get("body") or {}
    part_iii = _get(body, "part_iii") or {}
    results = []

    for rec in details:
        entry = {
            "source_page": rec.get("source_page"),
            "line_token": rec.get("line_token"),
            "label": rec.get("label"),
            "box_key": rec.get("box_key"),
            "code": rec.get("code"),
            "printed_total": rec.get("total"),
        }

        if rec.get("total") is None:
            entry["status"] = "not_applicable"
            entry["reason"] = ("Document prints no TOTAL for this block; "
                               "nothing to reconcile against.")
            results.append(entry)
            continue

        box_node = part_iii.get(rec.get("box_key"))
        if box_node is None:
            entry["status"] = "face_missing"
            entry["reason"] = ("Box '%s' is absent from the assembled OTD -- "
                               "cannot reconcile." % rec.get("box_key"))
            results.append(entry)
            continue

        if rec.get("code"):
            # Coded box: find the matching (box_key, code) entry.
            entries = box_node.get("entries") or []
            match = next(
                (e for e in entries
                 if str(e.get("code", "")).upper() == rec["code"].upper()),
                None)
            if match is None:
                entry["status"] = "face_missing"
                entry["reason"] = (
                    "Code '%s' under box '%s' is absent from the assembled "
                    "OTD -- cannot reconcile." % (rec["code"], rec["box_key"]))
                results.append(entry)
                continue
            face_value = match.get("value")
        else:
            face_value = box_node.get("value")

        entry["face_value"] = face_value

        if face_value is None:
            entry["status"] = "face_missing"
            entry["reason"] = ("Face value is null/unverified -- cannot "
                               "reconcile against a printed detail total.")
            results.append(entry)
            continue

        delta = abs(float(face_value) - float(rec["total"]))
        entry["delta"] = delta
        if delta <= tolerance:
            entry["status"] = "match"
        else:
            entry["status"] = "mismatch"
            entry["reason"] = (
                "Face value %.2f does not equal document-printed detail "
                "total %.2f (delta %.2f exceeds tolerance %.2f)."
                % (face_value, rec["total"], delta, tolerance))
        results.append(entry)

    return results


def main():
    p = argparse.ArgumentParser(
        description="K-1 OTD Skill -- face-vs-detail reconciliation gate")
    p.add_argument("--otd", required=True, help="Path to assembled output.otd.yaml")
    p.add_argument("--details", required=True,
                   help="Path to line_item_details.json")
    p.add_argument("--out", required=True,
                   help="Output path for reconciliation report JSON")
    p.add_argument("--posture", choices=["hard_error", "warn"],
                   default="hard_error",
                   help="hard_error (default): mismatch fails the gate. "
                        "warn: mismatch is recorded but does not fail.")
    args = p.parse_args()

    otd = load_otd(args.otd)
    with open(args.details, "r", encoding="utf-8") as fh:
        details = json.load(fh)

    results = reconcile(otd, details)

    counts = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1

    report = {
        "posture": args.posture,
        "tolerance": TOLERANCE,
        "counts": counts,
        "results": results,
    }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, default=str),
                        encoding="utf-8")
    print("WROTE %s" % out_path)
    print("RECONCILIATION: %s" % counts)

    mismatches = [r for r in results if r["status"] == "mismatch"]
    for m in mismatches:
        print("  MISMATCH: box=%s code=%s face=%s printed=%s (%s)"
              % (m["box_key"], m.get("code"), m.get("face_value"),
                 m.get("printed_total"), m.get("reason")))

    face_missing = [r for r in results if r["status"] == "face_missing"]
    for fm in face_missing:
        print("  FACE_MISSING: box=%s code=%s (%s)"
              % (fm["box_key"], fm.get("code"), fm.get("reason")))

    if args.posture == "hard_error" and mismatches:
        print("RESULT: FAIL (%d mismatch(es) under hard_error posture)"
              % len(mismatches))
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
