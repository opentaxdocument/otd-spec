#!/usr/bin/env python
import argparse
import re
from pathlib import Path
try:
    import ruamel.yaml
except ImportError:
    import sys
    print("ERROR: ruamel.yaml not installed. Run 'pip install ruamel.yaml'")
    sys.exit(1)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--text-dir", required=True)
    parser.add_argument("--master-taxonomy", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    text_dir = Path(args.text_dir)
    master_tax_path = Path(args.master_taxonomy)
    out_path = Path(args.out)

    full_text = ""
    for txt_file in text_dir.glob("*.txt"):
        full_text += txt_file.read_text(encoding="utf-8") + "\n"

    found_codes = set()
    # Match strings like "Box 20 Code Z", "Line 13W", "13 W"
    matches = re.findall(r'(?i)(?:box|line)?\s*(11|13|14|15|17|18|19|20)\s*(?:code\s*)?([A-Z]{1,2})\b', full_text)
    for box, code in matches:
        found_codes.add(f"box_{box}_{code.upper()}")

    yaml = ruamel.yaml.YAML()
    yaml.preserve_quotes = True
    
    with open(master_tax_path, 'r', encoding='utf-8') as f:
        master = yaml.load(f)

    mini = {"nodes": { "part_iii": { "children": {} } } }
    part_iii = master.get("nodes", {}).get("part_iii", {}).get("children", {})
    
    boxes_to_filter = ["box_11", "box_13", "box_14", "box_15", "box_17", "box_18", "box_19", "box_20"]
    for box_id in boxes_to_filter:
        if box_id in part_iii:
            box_data = part_iii[box_id]
            filtered_codes = {}
            for code_key, code_val in box_data.get("codes", {}).items():
                if f"{box_id}_{code_key}" in found_codes or code_key in ["ZZ", "other"]:
                    filtered_codes[code_key] = code_val
            
            box_copy = {k: v for k, v in box_data.items() if k != "codes"}
            box_copy["codes"] = filtered_codes
            mini["nodes"]["part_iii"]["children"][box_id] = box_copy

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, 'w', encoding='utf-8') as f:
        yaml.dump(mini, f)
        
    print(f"WROTE {out_path.absolute()}")

if __name__ == "__main__":
    main()
