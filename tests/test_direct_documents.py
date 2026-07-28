#!/usr/bin/env python
"""OTD K-1 Direct-Document Validation Suite.

The assembler-routed suite (test_rectification.py) can only produce documents
the assembler is willing to emit. Both adversarial reviews probed a different
surface: hand-built OTD documents fed straight to the validator, bypassing the
assembler entirely. Silent-acceptance defects live precisely there.

This suite takes the known-good golden document, applies one targeted mutation
per case, and asserts the validator rejects it. A pass here means the validator
enforces the document contract itself -- not merely that the assembler happens
to emit conforming output.

Exit 0 = every case behaved as expected.
Exit 1 = at least one malformed document was accepted (or a valid one rejected).
"""
import copy
import subprocess
import sys
import tempfile
from pathlib import Path

# See test_rectification.py: validator diagnostics carry non-ASCII markers and
# must not crash the report under the Windows cp1252 console codec.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


try:
    from ruamel.yaml import YAML
    _yaml = YAML()
    _yaml.indent(mapping=2, sequence=4, offset=2)
    _yaml.default_flow_style = False
except ImportError:
    print("ERROR: pip install ruamel.yaml", file=sys.stderr)
    sys.exit(1)

REPO_ROOT = Path(__file__).resolve().parent.parent
VALIDATOR = REPO_ROOT / "skills/k1-otd/scripts/validate_otd.py"
TAXONOMY = REPO_ROOT / "taxonomies/irs-k1-1065-2025.yaml"
PROOF = REPO_ROOT / "proof/otd_round_trip_proof.py"
GOLDEN = REPO_ROOT / "proof/generated/proof-emitted.otd.yaml"


class HarnessError(Exception):
    """The golden document lacks a structure a case depends on.

    Raised rather than swallowed: a mutation that silently no-ops would make a
    malformed-input case pass for the wrong reason, which is exactly the class
    of false assurance this suite exists to prevent.
    """


def ensure_golden():
    """Load the golden document, generating it first if absent."""
    if not GOLDEN.exists():
        subprocess.run(
            [sys.executable, str(PROOF)], cwd=str(REPO_ROOT),
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=120)
    if not GOLDEN.exists():
        print(f"ERROR: golden document not found at {GOLDEN}", file=sys.stderr)
        sys.exit(1)
    with open(GOLDEN, encoding="utf-8") as f:
        return _yaml.load(f)


def run_validator(doc):
    """Write a document to a temp file and validate it. Returns (ok, stdout)."""
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "case.otd.yaml"
        with open(path, "w", encoding="utf-8") as f:
            _yaml.dump(doc, f)
        r = subprocess.run(
            [sys.executable, str(VALIDATOR), "--input", str(path),
             "--taxonomy", str(TAXONOMY)],
            cwd=str(REPO_ROOT), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=30)
        return r.returncode == 0, r.stdout


# ---------------------------------------------------------------------------
# Structural accessors -- fail loudly if the golden shape is not what we expect
# ---------------------------------------------------------------------------

def _body(doc):
    body = doc.get("body")
    if not isinstance(body, dict):
        raise HarnessError("golden document has no body")
    return body


def _part(doc, name):
    part = _body(doc).get(name)
    if not isinstance(part, dict):
        raise HarnessError(f"golden document has no {name}")
    return part


def _first_statement(doc):
    """Find a statement node to mutate: the root-level list first, then any
    statement attached to a node inside the body.

    The golden document nests its statements inside the nodes they qualify
    (Item M, Box 16, Box 20 Codes X/Z/ZZ) rather than collecting them at the
    document root. A root-only accessor therefore found nothing -- and had it
    returned None instead of raising, both role cases would have reported
    "correctly rejected" for entirely the wrong reason.
    """
    for s in doc.get("statements") or []:
        if isinstance(s, dict) and isinstance(s.get("semantic"), dict):
            return s

    found = []

    def walk(node):
        if found:
            return
        if isinstance(node, dict):
            if node.get("type") == "statement" and isinstance(node.get("semantic"), dict):
                found.append(node)
                return
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(_body(doc))
    if found:
        return found[0]
    raise HarnessError("golden document has no statement node to mutate")


def _capital_value(doc):
    node = _part(doc, "part_ii").get("item_l")
    if not isinstance(node, dict) or not isinstance(node.get("value"), dict):
        raise HarnessError("golden document has no structured part_ii.item_l value")
    return node["value"]


def _first_key(part, predicate, what):
    for k in part:
        if predicate(k):
            return k
    raise HarnessError(f"golden document has no {what}")


# ---------------------------------------------------------------------------
# Mutations
# ---------------------------------------------------------------------------

CAPITAL_COMPONENTS = ("beginning", "contributions",
                      "current_year_increase_decrease",
                      "other_increase_decrease", "withdrawals")


def mut_valid(doc):
    return doc


def mut_malformed_scalar(doc):
    """A node declaring only its type -- no semantic, no form, no value."""
    part_iii = _part(doc, "part_iii")
    key = _first_key(part_iii, lambda k: str(k).startswith("box_"), "part_iii box")
    part_iii[key] = {"type": "scalar"}
    return doc


def mut_empty_parts(doc):
    """Structurally present but physically empty parts."""
    body = _body(doc)
    for p in ("part_i", "part_ii", "part_iii"):
        body[p] = {}
    return doc


def mut_statement_missing_role(doc):
    _first_statement(doc)["semantic"].pop("role", None)
    return doc


def mut_statement_unrecognized_role(doc):
    _first_statement(doc)["semantic"]["role"] = "machine_only"
    return doc


def mut_capital_incomplete(doc):
    cap = _capital_value(doc)
    for k in CAPITAL_COMPONENTS:
        if k in cap:
            del cap[k]
            return doc
    raise HarnessError("golden capital account has no component to remove")


def mut_capital_duplicate_alias(doc):
    """Both current-year fields present -- the change becomes unreconcilable."""
    cap = _capital_value(doc)
    cap["current_year_increase_decrease"] = cap.get(
        "current_year_increase_decrease", 0)
    cap["current_year_net"] = 12345.00
    return doc


def mut_reference_without_payload(doc):
    """A reference asserting neither an attached K-3 nor a notification."""
    node = _part(doc, "part_iii").get("box_16")
    if not isinstance(node, dict):
        raise HarnessError("golden document has no part_iii.box_16 reference")
    for k in ("target", "notification", "_unverified"):
        node.pop(k, None)
    return doc


def mut_coded_without_entries(doc):
    """A coded node carrying no entries at all."""
    part_iii = _part(doc, "part_iii")
    for k, v in part_iii.items():
        if isinstance(v, dict) and v.get("type") == "coded":
            v.pop("entries", None)
            return doc
    raise HarnessError("golden document has no coded node in part_iii")


CASES = [
    ("valid_baseline",              mut_valid,                     True),
    ("malformed_scalar_node",       mut_malformed_scalar,          False),
    ("empty_parts",                 mut_empty_parts,               False),
    ("statement_missing_role",      mut_statement_missing_role,    False),
    ("statement_unrecognized_role", mut_statement_unrecognized_role, False),
    ("capital_incomplete",          mut_capital_incomplete,        False),
    ("capital_duplicate_alias",     mut_capital_duplicate_alias,   False),
    ("reference_without_payload",   mut_reference_without_payload, False),
    ("coded_without_entries",       mut_coded_without_entries,     False),
]


def main():
    print("=== OTD K-1 Direct-Document Validation Suite ===")
    print("(hand-mutated documents fed straight to the validator)")
    golden = ensure_golden()
    failures = []

    for label, mutate, expect_ok in CASES:
        print(f"Running: {label} ...")
        try:
            doc = mutate(copy.deepcopy(golden))
        except HarnessError as e:
            failures.append(f"[{label}] HARNESS ERROR: {e}")
            print(f"  HARNESS ERROR: [{label}] {e}")
            continue
        ok, out = run_validator(doc)
        if ok != expect_ok:
            detail = (f"[{label}] validator ok={ok} expected_ok={expect_ok}\n"
                      f"{out}")
            failures.append(detail)
            print(f"  MISMATCH: {detail.splitlines()[0]}")
        else:
            verdict = "accepted" if ok else "rejected"
            print(f"  OK: [{label}] correctly {verdict}")

    total = len(CASES)
    print(f"\n{total - len(failures)}/{total} cases behaved as expected.")
    if failures:
        print("=== FAILURES ===")
        for f in failures:
            print(f)
        return 1
    print("ALL DIRECT-DOCUMENT TESTS PASSED SUCCESSFULLY!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
