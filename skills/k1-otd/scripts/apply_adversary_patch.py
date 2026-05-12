#!/usr/bin/env python
import argparse
import json
from pathlib import Path
try:
    import ruamel.yaml
except ImportError:
    import sys
    print("ERROR: ruamel.yaml not installed. Run 'pip install ruamel.yaml'")
    sys.exit(1)

def deep_merge(target, patch):
    for key, value in patch.items():
        if isinstance(value, dict) and key in target and isinstance(target[key], dict):
            deep_merge(target[key], value)
        else:
            target[key] = value

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Path to output.otd.yaml")
    parser.add_argument("--patch", required=True, help="Path to adversarial_patch.json")
    args = parser.parse_args()

    input_path = Path(args.input)
    patch_path = Path(args.patch)

    if not patch_path.exists():
        print(f"No patch file found at {patch_path}. Skipping.")
        return

    with open(patch_path, 'r', encoding='utf-8') as f:
        patch_data = json.load(f)

    yaml = ruamel.yaml.YAML()
    yaml.preserve_quotes = True
    
    with open(input_path, 'r', encoding='utf-8') as f:
        otd_data = yaml.load(f)

    deep_merge(otd_data, patch_data)

    with open(input_path, 'w', encoding='utf-8') as f:
        yaml.dump(otd_data, f)
        
    print(f"WROTE PATCHED OTD TO {input_path.absolute()}")

if __name__ == "__main__":
    main()
