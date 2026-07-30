#!/usr/bin/env python
import argparse
import json
from pathlib import Path

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", required=True, help="Path to text_blocks directory")
    parser.add_argument("--manifest", required=True, help="Path to page_manifest.json")
    parser.add_argument("--out", required=True, help="Path to save state_schedules.json")
    args = parser.parse_args()

    # Stub for deterministic PDF state grid extraction using pdfplumber.
    result = {
        "activity_schedule": {},
        "state_grids": [],
        "state_tax_summary": [],
        "_escalate": True,
        "_escalation_reason": "No state values were emitted; deterministic state-grid extraction requires pdfplumber bounding-box layout evidence."
    }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, indent=2)

    print(f"WROTE {out_path.absolute()} (Escalate: {result['_escalate']})")

if __name__ == "__main__":
    main()
