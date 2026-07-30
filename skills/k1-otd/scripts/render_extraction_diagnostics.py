#!/usr/bin/env python
"""Render extraction and classification evidence over PDF page images.

The renderer is deliberately evidence-preserving:

* exact PDF-coordinate bounding boxes are drawn directly;
* logical-section boxes are derived by matching their recorded text-line ranges
  back to PDF text lines, with a reported geometry confidence;
* whole-page classifications use inset page outlines;
* items without defensible geometry remain visible in a sidebar;
* every rendered or sidebar item is recorded in diagnostic-index.json.

Colors:
    green   present / matched / resolved
    blue    verified absent
    gray    blank / not applicable
    amber   missing / unresolved / review required
    red     invalid / exception / hard error
    purple  structural classification
"""
import argparse
import json
import math
import re
import sys
import textwrap
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

try:
    import pdfplumber
    from PIL import Image, ImageDraw, ImageFont
except ImportError as exc:
    print("ERROR: missing dependency: %s" % exc, file=sys.stderr)
    sys.exit(2)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from page_classifier import split_sections  # noqa: E402


SCHEMA_VERSION = "otd-diagnostic/1.0"
SIDEBAR_WIDTH = 500

STYLES = {
    "present":          {"color": "#16a34a", "severity": "ok"},
    "matched":          {"color": "#16a34a", "severity": "ok"},
    "resolved":         {"color": "#16a34a", "severity": "ok"},
    "verified_absent":  {"color": "#2563eb", "severity": "info"},
    "blank":            {"color": "#6b7280", "severity": "info"},
    "not_applicable":   {"color": "#6b7280", "severity": "info"},
    "missing":          {"color": "#d97706", "severity": "warning"},
    "unresolved":       {"color": "#d97706", "severity": "warning"},
    "review_required":  {"color": "#d97706", "severity": "warning"},
    "invalid":          {"color": "#dc2626", "severity": "error"},
    "error":            {"color": "#dc2626", "severity": "error"},
    "exception":        {"color": "#dc2626", "severity": "error"},
    "hard_error":       {"color": "#dc2626", "severity": "error"},
    "classification":   {"color": "#7c3aed", "severity": "structural"},
}

LEGEND = [
    ("Present / matched / resolved", "present"),
    ("Verified absent", "verified_absent"),
    ("Blank / not applicable", "blank"),
    ("Missing / unresolved / review", "unresolved"),
    ("Invalid / exception / hard error", "error"),
    ("Structural classification", "classification"),
]

WINDOWS_PATH = re.compile(r"\b[A-Za-z]:[\\/]")
PAGE_FILE = re.compile(r"page[_ -]?(\d+)", re.I)


def normalize_text(value):
    return " ".join(re.findall(r"[a-z0-9]+", str(value).lower()))


def valid_bbox(value):
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        box = [float(v) for v in value]
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(v) for v in box):
        return None
    if box[2] <= box[0] or box[3] <= box[1]:
        return None
    return box


def page_number(value):
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, str):
        if value.isdigit() and int(value) > 0:
            return int(value)
        match = PAGE_FILE.search(value)
        if match:
            return int(match.group(1))
    return None


def style_for(status, method=None):
    status = str(status or "unresolved").lower()
    method = str(method or "").lower()
    if any(token in method for token in ("exception", "invalid", "hard_error")):
        status = "error"
    return status, STYLES.get(status, STYLES["unresolved"])


def make_item(source, item_id, label, status, page=None, bbox=None,
              method=None, match_text=None, layer="evidence", details=None):
    normalized_status, style = style_for(status, method)
    return {
        "source": source,
        "id": str(item_id),
        "label": str(label),
        "status": normalized_status,
        "severity": style["severity"],
        "color": style["color"],
        "page": page_number(page),
        "bbox_pdf": valid_bbox(bbox),
        "method": method,
        "match_text": match_text,
        "layer": layer,
        "details": details or {},
    }


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_face_evidence(path):
    data = load_json(path)
    items = []
    for key, record in (data.get("fields") or {}).items():
        status = record.get("status", "unresolved")
        semantic_id = record.get("semantic_id")
        label = "%s | %s" % (key, status)
        if semantic_id:
            label += " | " + str(semantic_id)
        items.append(make_item(
            Path(path).stem,
            key,
            label,
            status,
            page=record.get("page", 1),
            bbox=record.get("bbox"),
            method=record.get("method"),
            match_text=record.get("raw_text"),
            layer="face_evidence",
            details={
                "semantic_id": semantic_id,
                "rule_id": record.get("rule_id"),
                "coordinate_frame": record.get("coordinate_frame"),
            },
        ))
    return items


def load_page_manifest(path):
    data = load_json(path)
    items = []
    for section in data.get("sections", []):
        section_type = section.get("type") or section.get("name") or "classification"
        status = "unresolved" if section_type == "unresolved" else "classification"
        for page in section.get("pages", []):
            label = "PAGE | %s | worker=%s" % (
                section.get("name", section_type),
                section.get("worker") or "-",
            )
            items.append(make_item(
                Path(path).stem,
                "page-%s-%s" % (page, section.get("name", section_type)),
                label,
                status,
                page=page,
                layer="page_classification",
                details={"whole_page": True, "type": section_type},
            ))
    return items


def load_section_manifest(path):
    data = load_json(path)
    items = []
    for record in data.get("sections", []):
        role = record.get("role") or record.get("section") or "unresolved"
        status = "unresolved" if role == "unresolved" else "classification"
        score = record.get("score")
        score_text = "?" if score is None else ("%.2f" % float(score))
        label = "SECTION #%s | %s | score=%s" % (
            record.get("index"),
            role,
            score_text,
        )
        details = {
            "line_start": record.get("line_start"),
            "line_end": record.get("line_end"),
            "row_count": record.get("row_count"),
            "dominant_shape": record.get("dominant_shape"),
            "reasons": record.get("reasons") or [],
            "derive_from_lines": True,
        }
        items.append(make_item(
            Path(path).stem,
            "p%s-section-%s" % (record.get("page"), record.get("index")),
            label,
            status,
            page=record.get("page"),
            layer="section_classification",
            details=details,
        ))
        for continued_page in record.get("continued_pages", []):
            items.append(make_item(
                Path(path).stem,
                "p%s-section-%s-continued" % (
                    continued_page, record.get("index")),
                label + " | continued",
                status,
                page=continued_page,
                layer="section_classification",
                details={
                    "continued_from_page": record.get("page"),
                    "geometry_unavailable_reason":
                        "continued-page line range is not recorded",
                },
            ))
    return items


def record_page(record, inherited_page=None):
    return (
        page_number(record.get("page"))
        or page_number(record.get("source_page"))
        or inherited_page
    )


def load_generic_evidence(path):
    """Load arbitrary evidence records, preserving unboxable items in sidebars."""
    data = load_json(path)
    source = Path(path).stem
    items = []

    def walk(node, pointer, inherited_page=None):
        if isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, "%s[%d]" % (pointer, index), inherited_page)
            return
        if not isinstance(node, dict):
            return

        page = record_page(node, inherited_page)
        escalated = node.get("_escalate") is True
        has_identity = any(key in node for key in (
            "bbox", "status", "semantic_id", "rule_id", "source_page",
            "_escalate",
        ))
        inherited_component = (
            page is not None
            and "label" in node
            and any(key in node for key in ("value", "normalized_value"))
        )

        if has_identity or inherited_component:
            status = node.get("status")
            if escalated:
                status = "unresolved"
            elif status is None:
                status = "present"

            label_parts = [pointer]
            for key in ("line_token", "code", "label", "semantic_id"):
                value = node.get(key)
                if value not in (None, ""):
                    label_parts.append(str(value))
            if escalated:
                label_parts.append("ESCALATION")

            match_text = node.get("label") or node.get("raw_text")
            items.append(make_item(
                source,
                pointer,
                " | ".join(label_parts),
                status,
                page=page,
                bbox=node.get("bbox"),
                method=node.get("method"),
                match_text=match_text,
                layer="generic_evidence",
                details={
                    "escalation_reason": node.get("_escalation_reason"),
                    "source_page": node.get("source_page"),
                },
            ))

        for key, value in node.items():
            if isinstance(value, (dict, list)):
                walk(value, "%s.%s" % (pointer, key), page)

    walk(data, source)
    return items


def pdf_lines(page):
    try:
        return page.extract_text_lines(
            layout=False,
            return_chars=False,
            x_tolerance=1,
            y_tolerance=3,
        ) or []
    except TypeError:
        return page.extract_text_lines(
            layout=False, return_chars=False) or []


def line_score(target, candidate):
    target_norm = normalize_text(target)
    candidate_norm = normalize_text(candidate)
    if len(target_norm) < 2 or len(candidate_norm) < 2:
        return 0.0
    if target_norm == candidate_norm:
        return 1.0
    if target_norm in candidate_norm or candidate_norm in target_norm:
        length_ratio = min(len(target_norm), len(candidate_norm)) / max(
            len(target_norm), len(candidate_norm))
        return 0.82 + (0.16 * length_ratio)
    return SequenceMatcher(
        None, target_norm, candidate_norm, autojunk=False).ratio()


def bbox_for_texts(targets, lines):
    used = set()
    matched = []
    eligible = [target for target in targets if len(normalize_text(target)) >= 2]

    for target in eligible:
        best_index = None
        best_score = 0.0
        for index, line in enumerate(lines):
            if index in used:
                continue
            score = line_score(target, line.get("text", ""))
            if score > best_score:
                best_index = index
                best_score = score
        if best_index is not None and best_score >= 0.70:
            used.add(best_index)
            matched.append((lines[best_index], best_score))

    if not matched:
        return None, {
            "eligible_line_count": len(eligible),
            "matched_line_count": 0,
            "confidence": 0.0,
        }

    box = [
        min(line["x0"] for line, _ in matched),
        min(line["top"] for line, _ in matched),
        max(line["x1"] for line, _ in matched),
        max(line["bottom"] for line, _ in matched),
    ]
    confidence = (
        sum(score for _, score in matched) / max(1, len(eligible))
    )
    return box, {
        "eligible_line_count": len(eligible),
        "matched_line_count": len(matched),
        "confidence": round(confidence, 4),
    }


def section_targets(text_dir, page_num, start, end):
    if text_dir is None:
        return []
    path = Path(text_dir) / ("page_%02d.txt" % page_num)
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8", errors="replace")
    body, _ = split_sections(text)
    lines = body.splitlines()
    if not isinstance(start, int) or not isinstance(end, int):
        return []
    start = max(0, start)
    end = min(len(lines) - 1, end)
    return lines[start:end + 1] if end >= start else []


def font(size, bold=False):
    candidates = (
        ["arialbd.ttf", "DejaVuSans-Bold.ttf"]
        if bold else ["arial.ttf", "DejaVuSans.ttf"]
    )
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def clip_box(box, width, height):
    x0, y0, x1, y1 = box
    x0 = max(0, min(width - 1, int(round(x0))))
    y0 = max(0, min(height - 1, int(round(y0))))
    x1 = max(x0 + 1, min(width - 1, int(round(x1))))
    y1 = max(y0 + 1, min(height - 1, int(round(y1))))
    return [x0, y0, x1, y1]


def draw_label(draw, box, label, color, label_font, collision_count):
    x0, y0, _, _ = box
    key = (x0 // 12, y0 // 12)
    offset = collision_count[key]
    collision_count[key] += 1

    text = label if len(label) <= 72 else label[:69] + "..."
    text_box = draw.textbbox((0, 0), text, font=label_font)
    text_width = text_box[2] - text_box[0]
    text_height = text_box[3] - text_box[1]
    label_y = y0 + offset * (text_height + 4)
    draw.rectangle(
        [x0, label_y, x0 + text_width + 6, label_y + text_height + 4],
        fill=color,
    )
    draw.text(
        (x0 + 3, label_y + 1), text, fill="white", font=label_font)


def render_sidebar(canvas, page_num, image_width, items, summary, fonts):
    draw = ImageDraw.Draw(canvas)
    x = image_width + 18
    y = 16

    draw.text((x, y), "OTD Diagnostic - Page %d" % page_num,
              fill="#111827", font=fonts["title"])
    y += 28
    draw.text(
        (x, y),
        "boxed=%d  sidebar=%d  total=%d" % (
            summary["boxed"], summary["sidebar"], summary["total"]),
        fill="#374151",
        font=fonts["body"],
    )
    y += 26

    for legend_label, style_name in LEGEND:
        color = STYLES[style_name]["color"]
        draw.rectangle([x, y + 2, x + 12, y + 14], fill=color)
        draw.text((x + 18, y), legend_label,
                  fill="#111827", font=fonts["small"])
        y += 18

    y += 10
    draw.line([x, y, canvas.width - 16, y], fill="#d1d5db", width=1)
    y += 10
    draw.text((x, y), "Items without defensible geometry",
              fill="#111827", font=fonts["body_bold"])
    y += 22

    if not items:
        draw.text((x, y), "None", fill="#16a34a", font=fonts["body"])
        return

    for item in items:
        color = item["color"]
        draw.rectangle([x, y + 2, x + 10, y + 12], fill=color)
        wrapped = textwrap.wrap(item["label"], width=58) or [item["label"]]
        for line_index, line in enumerate(wrapped[:3]):
            draw.text(
                (x + 16, y),
                line,
                fill="#111827" if line_index == 0 else "#4b5563",
                font=fonts["small"],
            )
            y += 15
        reason = item.get("details", {}).get(
            "geometry_unavailable_reason")
        if not reason and item.get("details", {}).get("escalation_reason"):
            reason = item["details"]["escalation_reason"]
        if reason:
            for line in textwrap.wrap(str(reason), width=58)[:2]:
                draw.text((x + 16, y), line,
                          fill="#b45309", font=fonts["small"])
                y += 15
        y += 6


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Render color-coded OTD extraction diagnostics")
    parser.add_argument("--pdf", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--face-evidence")
    parser.add_argument("--page-manifest")
    parser.add_argument("--section-manifest")
    parser.add_argument("--text-dir")
    parser.add_argument("--evidence", action="append", default=[])
    parser.add_argument("--resolution", type=int, default=144)
    parser.add_argument("--max-pages", type=int, default=None)
    args = parser.parse_args(argv)

    if args.resolution < 72 or args.resolution > 600:
        print("ERROR: --resolution must be between 72 and 600",
              file=sys.stderr)
        return 2

    items = []
    if args.face_evidence:
        items.extend(load_face_evidence(args.face_evidence))
    if args.page_manifest:
        items.extend(load_page_manifest(args.page_manifest))
    if args.section_manifest:
        items.extend(load_section_manifest(args.section_manifest))
    for path in args.evidence:
        items.extend(load_generic_evidence(path))

    output_dir = Path(args.out)
    output_dir.mkdir(parents=True, exist_ok=True)

    pdf_path = Path(args.pdf)
    pages_index = []
    all_records = []
    counts = Counter()
    items_by_page = defaultdict(list)
    unplaced = []

    for item in items:
        if item["page"] is None:
            unplaced.append(item)
        else:
            items_by_page[item["page"]].append(item)

    fonts = {
        "title": font(16, bold=True),
        "body_bold": font(12, bold=True),
        "body": font(12),
        "small": font(10),
        "label": font(10, bold=True),
    }

    with pdfplumber.open(str(pdf_path)) as document:
        total_pages = len(document.pages)
        render_count = total_pages
        if args.max_pages is not None:
            render_count = min(total_pages, max(0, args.max_pages))

        if render_count > 0 and unplaced:
            for item in unplaced:
                item["diagnostic_page"] = 1
                item["details"]["geometry_unavailable_reason"] = (
                    item["details"].get("geometry_unavailable_reason")
                    or "evidence record does not identify a source page"
                )
                items_by_page[1].append(item)

        for page_num in range(1, render_count + 1):
            page = document.pages[page_num - 1]
            raster = page.to_image(
                resolution=args.resolution, antialias=True
            ).original.convert("RGB")
            scale = args.resolution / 72.0
            lines = pdf_lines(page)
            page_items = items_by_page.get(page_num, [])

            whole_page_index = 0
            for item in page_items:
                if item["bbox_pdf"] is not None:
                    continue
                if item["details"].get("whole_page"):
                    inset = 4 + whole_page_index * 4
                    whole_page_index += 1
                    item["bbox_pdf"] = [
                        inset,
                        inset,
                        max(inset + 1, float(page.width) - inset),
                        max(inset + 1, float(page.height) - inset),
                    ]
                    continue

                if item["details"].get("derive_from_lines"):
                    targets = section_targets(
                        args.text_dir,
                        page_num,
                        item["details"].get("line_start"),
                        item["details"].get("line_end"),
                    )
                    box, geometry = bbox_for_texts(targets, lines)
                    item["bbox_pdf"] = box
                    item["details"]["geometry"] = geometry
                    if box is None:
                        item["details"]["geometry_unavailable_reason"] = (
                            "recorded section lines did not map to PDF text "
                            "geometry"
                        )
                    elif geometry["confidence"] < 0.50:
                        item["status"] = "review_required"
                        item["severity"] = STYLES["review_required"]["severity"]
                        item["color"] = STYLES["review_required"]["color"]
                    continue

                if item.get("match_text"):
                    box, geometry = bbox_for_texts(
                        [item["match_text"]], lines)
                    item["bbox_pdf"] = box
                    item["details"]["geometry"] = geometry
                    if box is None:
                        item["details"]["geometry_unavailable_reason"] = (
                            "evidence label did not map to PDF text geometry"
                        )

            layer_order = {
                "page_classification": 0,
                "section_classification": 1,
                "generic_evidence": 2,
                "face_evidence": 3,
            }
            page_items.sort(key=lambda item: (
                layer_order.get(item["layer"], 9),
                item["id"],
            ))

            boxed = []
            sidebar = []
            collision_count = defaultdict(int)
            draw = ImageDraw.Draw(raster)

            for item in page_items:
                box = valid_bbox(item.get("bbox_pdf"))
                record = {
                    key: item.get(key) for key in (
                        "source", "id", "label", "status", "severity",
                        "color", "page", "method", "layer", "details",
                    )
                }
                if item in unplaced:
                    record["source_page"] = None
                    record["diagnostic_page"] = page_num

                if box is None:
                    item["placement"] = "sidebar"
                    record["placement"] = "sidebar"
                    record["bbox_pdf"] = None
                    record["bbox_pixels"] = None
                    sidebar.append(item)
                    counts["sidebar"] += 1
                else:
                    pixel_box = clip_box(
                        [value * scale for value in box],
                        raster.width,
                        raster.height,
                    )
                    draw.rectangle(
                        pixel_box, outline=item["color"], width=3)
                    draw_label(
                        draw,
                        pixel_box,
                        item["label"],
                        item["color"],
                        fonts["label"],
                        collision_count,
                    )
                    item["placement"] = "box"
                    record["placement"] = "box"
                    record["bbox_pdf"] = [
                        round(value, 3) for value in box]
                    record["bbox_pixels"] = pixel_box
                    boxed.append(item)
                    counts["boxed"] += 1

                counts["status:" + item["status"]] += 1
                all_records.append(record)

            sidebar_height = 168 + max(1, len(sidebar)) * 64
            canvas_height = max(raster.height, sidebar_height)
            canvas = Image.new(
                "RGB",
                (raster.width + SIDEBAR_WIDTH, canvas_height),
                "white",
            )
            canvas.paste(raster, (0, 0))
            render_sidebar(
                canvas,
                page_num,
                raster.width,
                sidebar,
                {
                    "boxed": len(boxed),
                    "sidebar": len(sidebar),
                    "total": len(page_items),
                },
                fonts,
            )

            image_name = "page_%03d.png" % page_num
            image_path = output_dir / image_name
            canvas.save(image_path, format="PNG", optimize=True)
            print("WROTE %s" % image_path.resolve())

            pages_index.append({
                "page": page_num,
                "image": image_name,
                "pdf_size_points": [
                    round(float(page.width), 3),
                    round(float(page.height), 3),
                ],
                "raster_size_pixels": [raster.width, raster.height],
                "canvas_size_pixels": [canvas.width, canvas.height],
                "boxed_item_count": len(boxed),
                "sidebar_item_count": len(sidebar),
                "item_count": len(page_items),
            })

    summary = {
        "total_item_count": len(all_records),
        "boxed_item_count": counts["boxed"],
        "sidebar_item_count": counts["sidebar"],
        "status_counts": {
            key.split(":", 1)[1]: value
            for key, value in sorted(counts.items())
            if key.startswith("status:")
        },
        "rendered_page_count": len(pages_index),
    }

    index = {
        "schema_version": SCHEMA_VERSION,
        "source_pdf": pdf_path.name,
        "resolution_dpi": args.resolution,
        "coordinate_frame": "pdf_top_left_origin_points",
        "color_legend": {
            label: STYLES[style]["color"] for label, style in LEGEND
        },
        "summary": summary,
        "pages": pages_index,
        "items": all_records,
    }

    payload = json.dumps(index, indent=2, ensure_ascii=False) + "\n"
    if WINDOWS_PATH.search(payload):
        print("ERROR: diagnostic index contains a machine-local path",
              file=sys.stderr)
        return 2

    index_path = output_dir / "diagnostic-index.json"
    index_path.write_text(payload, encoding="utf-8", newline="\n")
    print("WROTE %s" % index_path.resolve())
    print(
        "Diagnostic render complete: %d page(s), %d boxed item(s), "
        "%d sidebar item(s)"
        % (
            summary["rendered_page_count"],
            summary["boxed_item_count"],
            summary["sidebar_item_count"],
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
