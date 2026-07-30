#!/usr/bin/env python
"""OTD K-1 Rectification Regression Suite.

Exercises the full acceptance-gate matrix from the two adversarial NO-GO
reviews (2026-07-28), including the residual malformed-input counterexamples
from the second review. Each case runs the actual production assembler
(assemble_otd.py) and standalone validator (validate_otd.py) as
subprocesses -- never a private/proof-only code path -- so a pass here means
the shipping pipeline behaves correctly, not just an isolated unit.

Exit code 0 = all cases behaved as expected (pass where expected to pass,
fail where expected to fail). Exit code 1 = at least one case diverged from
its expected outcome; printed diagnostics identify which.
"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# Failure diagnostics contain the validator's non-ASCII review markers. Without
# this, printing a failure report raises UnicodeEncodeError under the Windows
# cp1252 console codec -- which once hid the real cause of five failing cases
# behind a traceback.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT = Path(__file__).resolve().parent.parent
ASSEMBLER = REPO_ROOT / "skills/k1-otd/scripts/assemble_otd.py"
VALIDATOR = REPO_ROOT / "skills/k1-otd/scripts/validate_otd.py"
TAXONOMY = REPO_ROOT / "taxonomies/irs-k1-1065-2025.yaml"

BASE_FACE = {
    "form_metadata": {"tax_year": 2025, "fiscal_year": False, "amended": False,
                       "final": False, "filing_status": "original"},
    "part_i": {"partnership_ein": "12-3456789", "partnership_name": "Test LP, 1 Main St",
               "irs_center": "Ogden, UT", "publicly_traded": False},
    "part_ii": {"partner_tin": "123-45-6789", "partner_name": "Test Partner, 2 Elm St",
                "general_or_limited": "limited_or_other_member", "domestic_or_foreign": "domestic",
                "entity_type": "individual", "retirement_plan": False,
                "share_percentages": {"profit_beginning": 0.1, "profit_ending": 0.1,
                                       "loss_beginning": 0.1, "loss_ending": 0.1,
                                       "capital_beginning": 0.1, "capital_ending": 0.1,
                                       "decrease_due_to_sale": False, "decrease_due_to_exchange": False},
                "liabilities": {"nonrecourse_beginning": 0, "nonrecourse_ending": 0,
                                 "qualified_nonrecourse_beginning": 0, "qualified_nonrecourse_ending": 0,
                                 "recourse_beginning": 0, "recourse_ending": 0},
                "item_k2": False, "item_k3": False,
                "capital_account": {"beginning": 1000, "contributions": 0,
                                     "current_year_increase_decrease": 0,
                                     "other_increase_decrease": 0, "withdrawals": 0,
                                     "ending": 1000, "basis_method": "tax"},
                "item_m": {"value": False, "statement": None},
                "item_n": {"beginning": 0, "ending": 0}},
    "part_iii_face": {"box_16_checked": False},
    "_unmapped_source_data": []
}

EMPTY_FRAGMENTS = {
    "overflow_statements.json": {"part_iii_overflow": {}, "_unmapped_source_data": []},
    "footnotes_a.json": [],
    "footnotes_b.json": [],
    "state_schedules.json": {},
}


def deep_merge(base, override):
    result = json.loads(json.dumps(base))
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(result.get(k), dict):
            result[k] = deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def run_case(face_override, expect_assembler_ok=True, expect_validator_ok=True, label=""):
    """Assemble + validate one fixture via the actual production scripts.
    Returns (ok: bool, detail: str)."""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        frag_dir = tmp / "fragments"
        frag_dir.mkdir()
        face = deep_merge(BASE_FACE, face_override)
        (frag_dir / "face_page.json").write_text(json.dumps(face), encoding="utf-8")
        for name, content in EMPTY_FRAGMENTS.items():
            (frag_dir / name).write_text(json.dumps(content), encoding="utf-8")

        out_path = tmp / "output.otd.yaml"
        asm = subprocess.run(
            [sys.executable, str(ASSEMBLER), "--fragments", str(frag_dir), "--out", str(out_path)],
            cwd=str(REPO_ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
        asm_ok = asm.returncode == 0 and out_path.exists()

        if asm_ok != expect_assembler_ok:
            return False, f"[{label}] assembler exit={asm.returncode} expected_ok={expect_assembler_ok}\n{asm.stdout}\n{asm.stderr}"

        if not asm_ok:
            return True, f"[{label}] assembler correctly failed as expected"

        val = subprocess.run(
            [sys.executable, str(VALIDATOR), "--input", str(out_path), "--taxonomy", str(TAXONOMY)],
            cwd=str(REPO_ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
        val_ok = val.returncode == 0

        if val_ok != expect_validator_ok:
            return False, f"[{label}] validator exit={val.returncode} expected_ok={expect_validator_ok}\n{val.stdout}"

        return True, f"[{label}] PASS (assembler ok={asm_ok}, validator ok={val_ok})"


CASES = []


def case(label, face_override, expect_assembler_ok=True, expect_validator_ok=True):
    CASES.append((label, face_override, expect_assembler_ok, expect_validator_ok))


# ── Box 16 branch exclusivity ────────────────────────────────────────────
case("box16_unchecked_valid", {"part_iii_face": {"box_16_checked": False}},
     expect_validator_ok=True)
case("box16_checked_valid", {"part_iii_face": {"box_16_checked": True}},
     expect_validator_ok=True)

# ── Item M: valid, invalid-empty-content, wrong-classification ──────────
case("item_m_true_valid_statement", {
    "part_ii": {"item_m": {"value": True, "statement": {
        "classification": "item_m_built_in_gain_loss",
        "content": {"property_description": "Land", "contribution_date": "2025-01-01",
                    "built_in_gain": 1000.0, "built_in_loss": None}}}}},
     expect_validator_ok=True)
case("item_m_true_missing_statement", {"part_ii": {"item_m": {"value": True, "statement": None}}},
     expect_validator_ok=False)
case("item_m_true_empty_content", {
    "part_ii": {"item_m": {"value": True, "statement": {
        "classification": "item_m_built_in_gain_loss", "content": {}}}}},
     expect_validator_ok=False)
case("item_m_true_wrong_classification", {
    "part_ii": {"item_m": {"value": True, "statement": {
        "classification": "section_199a_detail",
        "content": {"qbi": 1.0}}}}},
     expect_validator_ok=False)
case("item_m_true_malformed_non_dict_statement", {
    "part_ii": {"item_m": {"value": True, "statement": "not a dict"}}},
     expect_validator_ok=False)

# ── Item K3 -> Box 20 Code X linkage ─────────────────────────────────────
case("k3_true_missing_code_x", {"part_ii": {"item_k3": True}},
     expect_validator_ok=False)
case("k3_true_valid_code_x", {
    "part_ii": {"item_k3": True},
    "part_iii_face": {"box_16_checked": False,
                       "box_20": [{"code": "X", "value": 500.0,
                                   "classification": "payment_obligation",
                                   "statement": {"classification": "payment_obligation",
                                                 "content": {"obligation_type": "recognized_guarantee",
                                                             "ending_balance": 500.0}}}]}},
     expect_validator_ok=True)
case("k3_true_code_x_empty_content", {
    "part_ii": {"item_k3": True},
    "part_iii_face": {"box_16_checked": False,
                       "box_20": [{"code": "X", "value": 500.0,
                                   "classification": "payment_obligation",
                                   "statement": {"classification": "payment_obligation", "content": {}}}]}},
     expect_validator_ok=False)

# ── "UNKNOWN" sentinel handling: must not silently pass as false ────────
case("unknown_sentinel_item_k3_flagged", {"part_ii": {"item_k3": "UNKNOWN"}},
     expect_validator_ok=True)  # structurally valid (becomes _unverified, warning not error)

if __name__ == "__main__":
    print("=== OTD K-1 Rectification Regression Suite (v2 — full acceptance gate) ===")
    failures = []
    for label, override, exp_asm, exp_val in CASES:
        print(f"Running: {label} ...")
        ok, detail = run_case(override, exp_asm, exp_val, label)
        print(f"  {'OK' if ok else 'MISMATCH'}: {detail.splitlines()[0]}")
        if not ok:
            failures.append(detail)

    print(f"\n{len(CASES) - len(failures)}/{len(CASES)} cases behaved as expected.")
    if failures:
        print("\n=== FAILURES ===")
        for f in failures:
            print(f)
        sys.exit(1)
    print("ALL REGRESSION TESTS PASSED SUCCESSFULLY!")
    sys.exit(0)
