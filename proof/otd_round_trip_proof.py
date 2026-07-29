#!/usr/bin/env python3
"""
Open Tax Document (OTD) — Round-Trip Proof of Concept
=====================================================

Demonstrates a bounded structured-data lifecycle for one in-memory K-1
fixture:
  1. EMIT:      Structured fixture → OTD YAML document
  2. PARSE:     OTD YAML → Queryable TaxDocument tree
  3. VALIDATE:  Run selected taxonomy constraints against parsed data
  4. QUERY:     Semantic lookup, form lookup, statement flattening
  5. ROUND-TRIP: Re-emit → compare after whitespace/newline normalization

This proof does not ingest a PDF, invoke page/face extraction, or exercise the
production assembler. It is evidence for this fixture, not universal OTD
conformance.

This script is self-contained and requires only:
  - Python 3.9+
  - ruamel.yaml (pip install ruamel.yaml)

License: CC BY 4.0 | Authors: Tom O'Sullivan, Second Wind | 2026-04-02
"""

import sys
import json
import hashlib
import uuid
import io
import difflib
from pathlib import Path
from collections import OrderedDict

# ── Dependency check ────────────────────────────────────────────────────────
try:
    from ruamel.yaml import YAML
    from ruamel.yaml.comments import CommentedMap, CommentedSeq
except ImportError:
    print("ERROR: ruamel.yaml is required. Install with: pip install ruamel.yaml")
    sys.exit(1)

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PROOF_DIR = Path(__file__).resolve().parent
REPO_ROOT = PROOF_DIR.parent
ARTIFACT_DIR = PROOF_DIR / "generated"
ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
EMITTED_FILE = ARTIFACT_DIR / "proof-emitted.otd.yaml"
RE_EMITTED_FILE = ARTIFACT_DIR / "proof-re-emitted.otd.yaml"
RESULTS_FILE = ARTIFACT_DIR / "proof-results.txt"

# ---------------------------------------------------------------------------
# Taxonomy-declared coded semantics
# ---------------------------------------------------------------------------
#
# These ids are transcribed from irs-k1-1065-2025.yaml. A binding dry run
# found 18 emitted entries carrying positional ids (other_income.a) where the
# taxonomy declares meaning-bearing ones (other_income.portfolio).
#
# A positional scheme cannot be made correct by care alone. Box 18 declares
# tax_exempt.interest for Code A and nondeductible.expenses for Code C --
# two concepts sharing one box. Any f"{prefix}.{code}" construction must pick
# one prefix and will mislabel the other.
#
# The proof keeps these explicit rather than loading the YAML: a reader of a
# demonstration artifact learns more from a visible mapping than from an
# opaque lookup. Drift is no longer silent -- the binding validator rejects
# any id that disagrees with the taxonomy.

PROOF_CODE_IDS = {
    "box_11": {
        "A": "other_income.portfolio",
        "F": "other_income.743b_positive",
    },
    "box_13": {
        "A": "other_deductions.cash_contributions_60",
        "H": "other_deductions.investment_interest",
        "K": "other_deductions.ebie",
    },
    "box_15": {
        "M": "credits.research_activities",
        "AW": "credits.carbon_oxide_sequestration",
    },
    "box_17": {
        "A": "amt.depreciation_adjustment",
    },
    "box_18": {
        "A": "tax_exempt.interest",
        "C": "nondeductible.expenses",
    },
    "box_19": {
        "A": "distributions.cash_marketable_securities",
    },
    "box_20": {
        "A": "other_information.investment_income",
        "B": "other_information.investment_expenses",
        "N": "other_information.business_interest_expense",
        "X": "other_information.payment_obligations",
        "Y": "other_information.nii",
        "Z": "other_information.section_199a",
        "ZZ": "other_information.other",
    },
}

_PROOF_CODE_FALLBACK_PREFIX = {
    "box_11": "other_income",
    "box_13": "other_deductions",
    "box_15": "credits",
    "box_17": "amt",
    "box_18": "tax_exempt",
    "box_19": "distributions",
    "box_20": "other_information",
}


def _proof_code_id(box_key, code):
    """Taxonomy-declared semantic id for one coded entry.

    Falls back to the historical positional form only for a code the map does
    not cover, which the binding validator will then reject by name rather
    than accept as declared fact.
    """
    declared = PROOF_CODE_IDS.get(box_key, {}).get(str(code).upper())
    if declared:
        return declared
    prefix = _PROOF_CODE_FALLBACK_PREFIX.get(box_key, box_key)
    return f"{prefix}.{str(code).lower()}"


CLASSIFICATION_ALIASES = {
    "section_199a": "section_199a_detail",
    "irs_form_926": "form_926_transfer_to_foreign_corp",
    "state_k1_grid": "state_apportionment",
}



def display_path(path: Path) -> str:
    """Return a stable repo-relative path for proof logs."""
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.as_posix()

# ============================================================================
# §1  SOURCE DATA — Realistic K-1 for proof
# ============================================================================
# Based on a realistic partnership scenario with complex Box 20 entries.

SOURCE_DATA = {
    # Fixed fixture metadata keeps tracked proof evidence reproducible.
    "created": "2026-07-28T03:33:16Z",
    "partnership": {
        "name": "Greenfield Capital Partners LP",
        "address": "100 Park Avenue, Suite 2400, New York, NY 10017",
        "ein": "83-1234567",
        "irs_center": "Ogden, UT",
        "publicly_traded": False,
    },
    "partner": {
        "name": "Jane A. Doe",
        "address": "42 Maple Lane, Greenwich, CT 06830",
        "ssn": "987-65-4321",
        "entity_type": "individual",
        "general_or_limited": "limited_partner",
        "domestic_or_foreign": "domestic",
        "share_percentages": {
            "profit_beginning": 0.15, "profit_ending": 0.15,
            "loss_beginning": 0.15, "loss_ending": 0.15,
            "capital_beginning": 0.12, "capital_ending": 0.12,
            "decrease_due_to_sale": False, "decrease_due_to_exchange": False,
        },
        "liabilities": {
            "nonrecourse_beginning": 500000.00, "nonrecourse_ending": 480000.00,
            "qualified_nonrecourse_beginning": 0.00, "qualified_nonrecourse_ending": 0.00,
            "recourse_beginning": 100000.00, "recourse_ending": 95000.00,
        },
        "item_k2": False,
        "item_k3": True,
        "item_m": {
            "value": True,
            "statement": {
                "classification": "item_m_built_in_gain_loss",
                "content": {
                    "property_description": "Contributed partnership interest",
                    "contribution_date": "2025-01-15",
                    "built_in_gain": 125000.00,
                    "built_in_loss": None,
                },
            },
        },
        "item_n": {"beginning": 0.00, "ending": 0.00},
        "capital_account": {
            "beginning": 1000000.00,
            "contributions": 250000.00,
            "current_year_increase_decrease": 150000.00,
            "other_increase_decrease": 0.00,
            "withdrawals": -100000.00,
            "ending": 1300000.00,
            "basis_method": "tax",
        },
    },
    "international": {"box_16_checked": False},
    "income": {
        "box_1": 150000.00,
        "box_2": -25000.00,
        "box_3": 0.00,
        "box_4a": 75000.00,
        "box_4b": 25000.00,
        "box_4c": 100000.00,
        "box_5": 18500.00,
        "box_6a": 32000.00,
        "box_6b": 28000.00,
        "box_6c": 0.00,
        "box_7": 5000.00,
        "box_8": -3200.00,
        "box_9a": 45000.00,
        "box_9b": 0.00,
        "box_9c": 12000.00,
        "box_10": 8500.00,
        "box_11": {
            "A": 12500.00,
            "F": 35000.00,
        },
    },
    "deductions": {
        "box_12": 50000.00,
        "box_13": {
            "A": 15000.00,
            "H": 8200.00,
            "K": 22000.00,
        },
    },
    "self_employment": {
        "box_14": {
            "A": 0.00,
            "B": 0.00,
            "C": 0.00,
        },
    },
    "credits": {
        "box_15": {
            "M": 7500.00,
            "AW": 12000.00,
        },
    },
    "amt": {
        "box_17": {
            "A": 3200.00,
        },
    },
    "tax_exempt": {
        "box_18": {
            "A": 4500.00,
            "C": 1200.00,
        },
    },
    "distributions": {
        "box_19": {
            "A": 100000.00,
        },
    },
    "other_information": {
        "box_20": {
            "A": 50500.00,
            "B": 8200.00,
            "N": 22000.00,
            "X": {
                "value": 25000.00,
                "classification": "payment_obligation",
                "statement": {
                    "classification": "payment_obligation",
                    "content": {
                        "obligation_type": "recognized_guarantee",
                        "ending_balance": 25000.00,
                    },
                },
            },
            "Y": 195300.00,
            "Z": {
                "value": None,
                "statement": {
                    "classification": "section_199a",
                    "content": {
                        "qbi": 150000.00,
                        "w2_wages": 80000.00,
                        "ubia": 500000.00,
                        "sstb": False,
                        "business_name": "Greenfield Operations LLC",
                        "section_199a_dividends": None,
                        "patron_reduction": None,
                    },
                },
            },
            "ZZ": {
                "value": None,
                "classification": "irs_form_926",
                "statement": {
                    "classification": "irs_form_926",
                    "content": {
                        "transferee": "XYZ Holdings GmbH",
                        "transferee_country": "DE",
                        "transfer_date": "2025-06-15",
                        "total_fmv": 5000000.00,
                        "total_adjusted_basis": 500000.00,
                        "total_gain_recognized": 4500000.00,
                    },
                },
            },
        },
    },
    "foreign_taxes": {"box_21": 3200.00},
    "at_risk": {"box_22": False},
    "passive_activity": {"box_23": True},
}


# ============================================================================
# §2  EMITTER — Produces OTD YAML from source data
# ============================================================================

class OTDEmitter:
    """Produces OTD-conformant YAML from source data."""

    def __init__(self, redaction_policy="partial"):
        self.redaction_policy = redaction_policy
        self.fields_redacted = []
        self.yaml = YAML()
        self.yaml.default_flow_style = False
        self.yaml.width = 120
        self.yaml.indent(mapping=2, sequence=4, offset=2)

    def _redact_ein(self, ein: str) -> str:
        if self.redaction_policy == "none":
            return ein
        elif self.redaction_policy == "partial":
            return f"XX-XXXX{ein[-3:]}" if len(ein) >= 3 else "XX-XXXXXXX"
        return "XX-XXXXXXX"

    def _redact_ssn(self, ssn: str) -> str:
        if self.redaction_policy == "none":
            return ssn
        elif self.redaction_policy == "partial":
            return f"XXX-XX-{ssn[-4:]}" if len(ssn) >= 4 else "XXX-XX-XXXX"
        return "XXX-XX-XXXX"

    def _make_scalar(self, semantic_id, label, form_location, box, value,
                     currency="USD", sign_convention=None, value_type="decimal"):
        node = CommentedMap()
        node["type"] = "scalar"
        node["semantic"] = CommentedMap([("id", semantic_id), ("label", label)])
        node["form"] = CommentedMap([("form_id", "k1-1065"), ("location", form_location), ("box", box)])
        node["value"] = value
        if value_type == "decimal" and value is not None:
            node["currency"] = currency
        if sign_convention:
            node["sign_convention"] = sign_convention
        return node

    def _make_coded_entry(self, semantic_id, label, code, value,
                          statement=None, classification=None):
        entry = CommentedMap()
        entry["code"] = code
        sem = CommentedMap([("id", semantic_id), ("label", label)])
        if classification:
            sem["classification"] = self._canonical_classification(classification)
        entry["semantic"] = sem
        entry["value"] = value
        if statement:
            entry["statement"] = self._make_statement(statement)
        return entry

    def _canonical_classification(self, classification):
        return CLASSIFICATION_ALIASES.get(classification, classification) if classification else classification

    def _make_statement(self, stmt_data):
        if not isinstance(stmt_data, dict):
            stmt = CommentedMap()
            stmt["type"] = "statement"
            sem = CommentedMap()
            sem["id"] = "stmt_malformed_input"
            sem["label"] = "Malformed Statement Input"
            sem["classification"] = "unclassified_requires_review"
            sem["role"] = "investor_footnote"
            stmt["semantic"] = sem
            stmt["form"] = CommentedMap([("attachment", True)])
            stmt["content"] = CommentedMap()
            stmt["_unverified"] = (
                "HUMAN REVIEW REQUIRED: statement input was not a structured "
                f"object (received {type(stmt_data).__name__})")
            return stmt
        classification = self._canonical_classification(stmt_data["classification"])
        stmt = CommentedMap()
        stmt["type"] = "statement"
        sem = CommentedMap()
        sem["id"] = f"stmt_{classification}"
        sem["label"] = classification.replace("_", " ").title()
        sem["classification"] = classification
        sem["role"] = stmt_data.get("role", "investor_footnote")
        stmt["semantic"] = sem
        stmt["form"] = CommentedMap([("attachment", True)])
        content = CommentedMap()
        for k, v in (stmt_data.get("content") or {}).items():
            content[k] = v
        stmt["content"] = content
        return stmt

    def emit(self, source: dict) -> CommentedMap:
        doc = CommentedMap()
        p = source["partnership"]
        pr = source["partner"]

        # ── Envelope ────────────────────────────────────────────────────
        envelope = CommentedMap()
        envelope["version"] = "0.1"
        envelope["document_id"] = str(uuid.uuid5(uuid.NAMESPACE_URL, f"otd-proof-{p['ein']}-{pr['ssn']}"))
        envelope["created"] = source["created"]
        producer = CommentedMap([("name", "OTD Round-Trip Proof"), ("version", "0.1.0")])
        envelope["producer"] = producer
        taxonomy = CommentedMap()
        taxonomy["id"] = "irs-k1-1065-2025"
        taxonomy["version"] = "2025.1.0"
        taxonomy["source"] = "Partner's Instructions for Schedule K-1 (Form 1065), 2025"
        envelope["taxonomy"] = taxonomy
        doc["otd"] = envelope

        doc["form_metadata"] = CommentedMap([
            ("tax_year", 2025),
            ("fiscal_year", False),
            ("fiscal_year_begin", None),
            ("fiscal_year_end", None),
            ("amended", False),
            ("final", False),
            ("supersedes_document_id", None),
            ("filing_status", "original"),
            ("form_revision_date", "2025"),
        ])

        # ── Body ────────────────────────────────────────────────────────
        body = CommentedMap()
        body["form_id"] = "k1-1065"
        body["tax_year"] = 2025

        # Part I — IRS Item A is the partnership EIN; Item B is name and address
        part_i = CommentedMap()
        redacted_ein = self._redact_ein(p["ein"])
        if redacted_ein != p["ein"]:
            self.fields_redacted.append("body.part_i.item_a")
        part_i["item_a"] = self._make_scalar(
            "partnership.ein", "Partnership's EIN",
            "Part I, Item A", "A", redacted_ein, value_type="string")
        part_i["item_b"] = self._make_scalar(
            "partnership.name_address", "Partnership's name, address",
            "Part I, Item B", "B", f"{p['name']}, {p['address']}", value_type="string")
        part_i["item_c"] = self._make_scalar(
            "partnership.irs_center", "IRS Center",
            "Part I, Item C", "C", p["irs_center"], value_type="string")
        part_i["item_d"] = self._make_scalar(
            "partnership.publicly_traded", "Publicly Traded Partnership",
            "Part I, Item D", "D", p["publicly_traded"], value_type="boolean")
        body["part_i"] = part_i

        # Part II — physical IRS item keys
        part_ii = CommentedMap()
        redacted_ssn = self._redact_ssn(pr["ssn"])
        if redacted_ssn != pr["ssn"]:
            self.fields_redacted.append("body.part_ii.item_e")
        part_ii["item_e"] = self._make_scalar(
            "partner.identifying_number", "Partner's SSN/TIN",
            "Part II, Item E", "E", redacted_ssn, value_type="string")
        part_ii["item_f"] = self._make_scalar(
            "partner.name_address", "Partner's name, address",
            "Part II, Item F", "F", f"{pr['name']}, {pr['address']}", value_type="string")
        part_ii["item_g"] = self._make_scalar(
            "partner.general_or_limited", "General or Limited",
            "Part II, Item G", "G", pr["general_or_limited"], value_type="enum")
        part_ii["item_h1"] = self._make_scalar(
            "partner.domestic_or_foreign", "Domestic or Foreign Partner",
            "Part II, Item H1", "H1", pr.get("domestic_or_foreign"), value_type="enum")
        h2_value = pr.get("disregarded_entity_info", {
            "is_disregarded_entity": False,
            "partner_tin": None,
            "partner_name": None,
        })
        part_ii["item_h2"] = self._make_scalar(
            "partner.disregarded_entity_info", "Disregarded Entity Information",
            "Part II, Item H2", "H2", CommentedMap(h2_value), value_type="object")
        part_ii["item_i1"] = self._make_scalar(
            "partner.entity_type", "Partner Entity Type",
            "Part II, Item I1", "I1", pr["entity_type"], value_type="enum")
        part_ii["item_i2"] = self._make_scalar(
            "partner.retirement_plan", "Partner Is a Retirement Plan",
            "Part II, Item I2", "I2", pr.get("retirement_plan", False), value_type="boolean")

        # Item J — Share percentages and IRS decrease reasons
        j_node = CommentedMap()
        j_node["type"] = "scalar"
        j_node["semantic"] = CommentedMap([("id", "partner.share_percentages"), ("label", "Partner's Share")])
        j_node["form"] = CommentedMap([("form_id", "k1-1065"), ("location", "Part II, Item J"), ("box", "J")])
        j_node["value"] = CommentedMap(pr["share_percentages"])
        j_node["format"] = "percentage"
        part_ii["item_j"] = j_node

        # Item K1 — Liabilities
        k1_node = CommentedMap()
        k1_node["type"] = "scalar"
        k1_node["semantic"] = CommentedMap([("id", "partner.share_of_liabilities"), ("label", "Liabilities")])
        k1_node["form"] = CommentedMap([("form_id", "k1-1065"), ("location", "Part II, Item K1"), ("box", "K1")])
        k1_node["value"] = CommentedMap(pr["liabilities"])
        k1_node["currency"] = "USD"
        part_ii["item_k1"] = k1_node
        part_ii["item_k2"] = self._make_scalar(
            "partner.liabilities_from_lower_tier_partnerships",
            "Liabilities from Lower-Tier Partnerships",
            "Part II, Item K2", "K2", pr.get("item_k2", False), value_type="boolean")
        part_ii["item_k3"] = self._make_scalar(
            "partner.liabilities_subject_to_guarantees_or_payment_obligations",
            "Liabilities Subject to Guarantees or Payment Obligations",
            "Part II, Item K3", "K3", pr.get("item_k3", False), value_type="boolean")

        # Item L — Capital account
        l_node = CommentedMap()
        l_node["type"] = "scalar"
        l_node["semantic"] = CommentedMap([("id", "partner.capital_account_analysis"), ("label", "Capital Account")])
        l_node["form"] = CommentedMap([("form_id", "k1-1065"), ("location", "Part II, Item L"), ("box", "L")])
        l_node["value"] = CommentedMap(pr["capital_account"])
        l_node["currency"] = "USD"
        part_ii["item_l"] = l_node

        # Item M — built-in gain/loss attachment when checked
        m_data = pr.get("item_m", {"value": False})
        m_value = m_data.get("value", False) if isinstance(m_data, dict) else bool(m_data)
        m_node = self._make_scalar(
            "partner.contributed_property_built_in_gain_loss",
            "Contributed Property with Built-In Gain or Loss",
            "Part II, Item M", "M", m_value, value_type="boolean")
        if isinstance(m_data, dict) and m_data.get("statement"):
            m_node["statement"] = self._make_statement(m_data["statement"])
        part_ii["item_m"] = m_node

        # Item N — net unrecognized Section 704(c) gain/loss
        part_ii["item_n"] = self._make_scalar(
            "partner.net_unrecognized_section_704c_gain_loss",
            "Net Unrecognized Section 704(c) Gain or (Loss)",
            "Part II, Item N", "N", CommentedMap(pr.get("item_n", {})), value_type="object")

        body["part_ii"] = part_ii

        # Part III
        part_iii = CommentedMap()

        # Income scalars (Boxes 1-10)
        income_map = [
            ("box_1", "ordinary_business_income", "Ordinary Business Income (Loss)", 1, "positive_is_income"),
            ("box_2", "net_rental_real_estate_income", "Net Rental Real Estate Income (Loss)", 2, "positive_is_income"),
            ("box_3", "other_net_rental_income", "Other Net Rental Income (Loss)", 3, "positive_is_income"),
            ("box_4a", "guaranteed_payments_services", "Guaranteed Payments for Services", "4a", None),
            ("box_4b", "guaranteed_payments_capital", "Guaranteed Payments for Capital", "4b", None),
            ("box_4c", "guaranteed_payments_total", "Total Guaranteed Payments", "4c", None),
            ("box_5", "interest_income", "Interest Income", 5, None),
            ("box_6a", "ordinary_dividends", "Ordinary Dividends", "6a", None),
            ("box_6b", "qualified_dividends", "Qualified Dividends", "6b", None),
            ("box_6c", "dividend_equivalents", "Dividend Equivalents", "6c", None),
            ("box_7", "royalties", "Royalties", 7, None),
            ("box_8", "net_short_term_capital_gain", "Net Short-Term Capital Gain (Loss)", 8, "positive_is_gain"),
            ("box_9a", "net_long_term_capital_gain", "Net Long-Term Capital Gain (Loss)", "9a", "positive_is_gain"),
            ("box_9b", "collectibles_gain", "Collectibles (28%) Gain (Loss)", "9b", None),
            ("box_9c", "unrecaptured_section_1250_gain", "Unrecaptured Section 1250 Gain", "9c", None),
            ("box_10", "net_section_1231_gain", "Net Section 1231 Gain (Loss)", 10, "positive_is_gain"),
        ]

        inc = source["income"]
        for key, sem_id, label, box, sign in income_map:
            val = inc.get(key)
            # A box reported as zero is a reported fact, not an omission.
            # Suppressing zeros left the physical form incomplete, which the
            # taxonomy-driven completeness validator correctly rejected.
            if val is not None:
                part_iii[key] = self._make_scalar(
                    sem_id, label, f"Part III, Box {box}", box, val,
                    sign_convention=sign)

        # Box 11 — Other Income (coded)
        if "box_11" in inc and inc["box_11"]:
            b11 = CommentedMap()
            b11["type"] = "coded"
            b11["semantic"] = CommentedMap([("id", "other_income"), ("label", "Other Income (Loss)")])
            b11["form"] = CommentedMap([("form_id", "k1-1065"), ("location", "Part III, Box 11"), ("box", 11)])
            entries = CommentedSeq()
            code_labels = {"A": "Other portfolio income", "F": "Section 743(b) positive adjustments"}
            for code, val in inc["box_11"].items():
                entries.append(self._make_coded_entry(
                    _proof_code_id("box_11", code), code_labels.get(code, code),
                    code, val))
            b11["entries"] = entries
            part_iii["box_11"] = b11

        # Box 12 — Section 179
        ded = source["deductions"]
        if ded.get("box_12"):
            part_iii["box_12"] = self._make_scalar(
                "section_179_deduction", "Section 179 Deduction",
                "Part III, Box 12", 12, ded["box_12"])

        # Box 13 — Other Deductions (coded)
        if "box_13" in ded and ded["box_13"]:
            b13 = CommentedMap()
            b13["type"] = "coded"
            b13["semantic"] = CommentedMap([("id", "other_deductions"), ("label", "Other Deductions")])
            b13["form"] = CommentedMap([("form_id", "k1-1065"), ("location", "Part III, Box 13"), ("box", 13)])
            entries = CommentedSeq()
            code_labels_13 = {
                "A": "Cash contributions (60%)",
                "H": "Investment interest expense",
                "K": "Excess business interest expense (EBIE)",
            }
            for code, val in ded["box_13"].items():
                entries.append(self._make_coded_entry(
                    _proof_code_id("box_13", code), code_labels_13.get(code, code),
                    code, val))
            b13["entries"] = entries
            part_iii["box_13"] = b13

        # Box 14 — Self-Employment Earnings (coded)
        se = source["self_employment"]
        if "box_14" in se and se["box_14"]:
            b14 = CommentedMap()
            b14["type"] = "coded"
            b14["semantic"] = CommentedMap([("id", "self_employment"), ("label", "Self-Employment Earnings (Loss)")])
            b14["form"] = CommentedMap([("form_id", "k1-1065"), ("location", "Part III, Box 14"), ("box", 14)])
            entries = CommentedSeq()
            code_ids_14 = {
                "A": ("self_employment.net_earnings", "Net earnings (loss) from self-employment"),
                "B": ("self_employment.gross_farming", "Gross farming or fishing income"),
                "C": ("self_employment.gross_nonfarm", "Gross nonfarm income"),
            }
            for code, val in se["box_14"].items():
                sem_id, sem_label = code_ids_14.get(code, (f"self_employment.{code.lower()}", code))
                entries.append(self._make_coded_entry(sem_id, sem_label, code, val))
            b14["entries"] = entries
            part_iii["box_14"] = b14

        # Box 15 — Credits (coded)
        cr = source["credits"]
        if "box_15" in cr and cr["box_15"]:
            b15 = CommentedMap()
            b15["type"] = "coded"
            b15["semantic"] = CommentedMap([("id", "credits"), ("label", "Credits")])
            b15["form"] = CommentedMap([("form_id", "k1-1065"), ("location", "Part III, Box 15"), ("box", 15)])
            entries = CommentedSeq()
            code_labels_15 = {
                "M": "Credit for increasing research activities",
                "AW": "Carbon oxide sequestration credit",
            }
            for code, val in cr["box_15"].items():
                entries.append(self._make_coded_entry(
                    _proof_code_id("box_15", code), code_labels_15.get(code, code),
                    code, val))
            b15["entries"] = entries
            part_iii["box_15"] = b15

        # Box 16 — International transactions and K-3 state
        international = source.get("international", {})
        box_16_checked = international.get("box_16_checked")
        b16 = CommentedMap()
        b16["type"] = "reference"
        b16["semantic"] = CommentedMap([("id", "international_transactions"), ("label", "International Transactions")])
        b16["form"] = CommentedMap([("form_id", "k1-1065"), ("location", "Part III, Box 16"), ("box", 16)])
        b16["checked"] = box_16_checked
        if box_16_checked is True:
            target = CommentedMap()
            target["document_type"] = "otd"
            target["taxonomy_id"] = "irs-k3-1065-2025"
            target["document_id"] = international.get("k3_document_id", "k3-partner-001")
            target["node_path"] = "/"
            b16["target"] = target
        elif box_16_checked is False:
            b16["notification"] = self._make_statement({
                "classification": "k3_not_attached_notification",
                "content": {
                    "notification_text": (
                        "The partner will not receive Schedule K-3 unless the partner requests the schedule."
                    )
                },
            })
        else:
            b16["_unverified"] = "HUMAN REVIEW REQUIRED: Box 16 checkbox state was not extracted"
        part_iii["box_16"] = b16
        amt = source["amt"]
        if "box_17" in amt and amt["box_17"]:
            b17 = CommentedMap()
            b17["type"] = "coded"
            b17["semantic"] = CommentedMap([("id", "amt_items"), ("label", "AMT Items")])
            b17["form"] = CommentedMap([("form_id", "k1-1065"), ("location", "Part III, Box 17"), ("box", 17)])
            entries = CommentedSeq()
            for code, val in amt["box_17"].items():
                entries.append(self._make_coded_entry(
                    _proof_code_id("box_17", code), f"AMT Code {code}", code, val))
            b17["entries"] = entries
            part_iii["box_17"] = b17

        # Box 18 — Tax Exempt
        te = source["tax_exempt"]
        if "box_18" in te and te["box_18"]:
            b18 = CommentedMap()
            b18["type"] = "coded"
            b18["semantic"] = CommentedMap([("id", "tax_exempt_nondeductible"), ("label", "Tax-Exempt & Nondeductible")])
            b18["form"] = CommentedMap([("form_id", "k1-1065"), ("location", "Part III, Box 18"), ("box", 18)])
            entries = CommentedSeq()
            code_labels_18 = {"A": "Tax-exempt interest income", "C": "Nondeductible expenses"}
            for code, val in te["box_18"].items():
                entries.append(self._make_coded_entry(
                    _proof_code_id("box_18", code), code_labels_18.get(code, code), code, val))
            b18["entries"] = entries
            part_iii["box_18"] = b18

        # Box 19 — Distributions
        dist = source["distributions"]
        if "box_19" in dist and dist["box_19"]:
            b19 = CommentedMap()
            b19["type"] = "coded"
            b19["semantic"] = CommentedMap([("id", "distributions"), ("label", "Distributions")])
            b19["form"] = CommentedMap([("form_id", "k1-1065"), ("location", "Part III, Box 19"), ("box", 19)])
            entries = CommentedSeq()
            for code, val in dist["box_19"].items():
                entries.append(self._make_coded_entry(
                    _proof_code_id("box_19", code), f"Distribution Code {code}", code, val))
            b19["entries"] = entries
            part_iii["box_19"] = b19

        # Box 20 — Other Information (the complex one)
        oi = source["other_information"]
        if "box_20" in oi:
            b20 = CommentedMap()
            b20["type"] = "coded"
            b20["semantic"] = CommentedMap([("id", "other_information"), ("label", "Other Information")])
            b20["form"] = CommentedMap([("form_id", "k1-1065"), ("location", "Part III, Box 20"), ("box", 20)])
            entries = CommentedSeq()
            code_labels_20 = {
                "A": "Investment income", "B": "Investment expenses",
                "N": "Business interest expense (BIE)", "Y": "Net investment income (NII)",
                "Z": "Section 199A information", "ZZ": "Other",
            }
            for code, val in oi["box_20"].items():
                if isinstance(val, dict):
                    # Complex entry with statement
                    classification = val.get("classification")
                    entries.append(self._make_coded_entry(
                        _proof_code_id("box_20", code),
                        code_labels_20.get(code, f"Code {code}"),
                        code, val.get("value"),
                        statement=val.get("statement"),
                        classification=classification))
                else:
                    entries.append(self._make_coded_entry(
                        _proof_code_id("box_20", code),
                        code_labels_20.get(code, f"Code {code}"),
                        code, val))
            b20["entries"] = entries
            part_iii["box_20"] = b20

        # Box 21 — Foreign taxes
        if source["foreign_taxes"].get("box_21"):
            part_iii["box_21"] = self._make_scalar(
                "foreign_taxes_paid_accrued", "Foreign Taxes Paid or Accrued",
                "Part III, Box 21", 21, source["foreign_taxes"]["box_21"])

        # Boxes 22-23
        part_iii["box_22"] = self._make_scalar(
            "at_risk_activities", "More Than One At-Risk Activity",
            "Part III, Box 22", 22, source["at_risk"]["box_22"], value_type="boolean")
        part_iii["box_23"] = self._make_scalar(
            "passive_activities", "More Than One Passive Activity",
            "Part III, Box 23", 23, source["passive_activity"]["box_23"], value_type="boolean")

        body["part_iii"] = part_iii

        doc["body"] = body

        # ── Redaction ───────────────────────────────────────────────────
        redaction = CommentedMap()
        redaction["policy"] = self.redaction_policy
        redaction["fields_redacted"] = self.fields_redacted
        doc["redaction"] = redaction

        return doc

    def emit_to_file(self, source: dict, path: Path) -> str:
        doc = self.emit(source)
        buf = io.StringIO()
        self.yaml.dump(doc, buf)
        text = buf.getvalue()
        path.write_text(text, encoding="utf-8", newline="\n")
        return text


# ============================================================================
# §3  PARSER — Consumes OTD YAML into a queryable tree
# ============================================================================

class TaxNode:
    """Represents a single node in the parsed OTD tree."""
    def __init__(self, raw: dict, path: str = ""):
        self.raw = raw
        self.path = path
        self.type = raw.get("type", "unknown")
        self.semantic = raw.get("semantic", {})
        self.semantic_id = self.semantic.get("id", "")
        self.label = self.semantic.get("label", "")
        self.form = raw.get("form", {})
        self.value = raw.get("value")
        self.entries = raw.get("entries", [])
        self.statement = raw.get("statement")
        self.target = raw.get("target")

    def __repr__(self):
        return f"TaxNode({self.type}, id={self.semantic_id}, value={self.value})"


class TaxDocument:
    """Parsed OTD document with query interface."""

    def __init__(self, raw: dict):
        self.raw = raw
        self.envelope = raw.get("otd", {})
        self.body = raw.get("body", {})
        self.redaction = raw.get("redaction", {})
        self._nodes = {}
        self._statements = []
        self._references = []
        self._index(self.body, "body")

    def _index(self, obj, path):
        if not isinstance(obj, dict):
            return
        if "type" in obj and "semantic" in obj:
            node = TaxNode(obj, path)
            sem_id = node.semantic_id
            if sem_id:
                self._nodes[sem_id] = node
            # Index coded entries
            for entry in node.entries:
                code = entry.get("code", "")
                entry_id = entry.get("semantic", {}).get("id", f"{sem_id}.{code}")
                entry_node = TaxNode(entry, f"{path}.{code}")
                self._nodes[entry_id] = entry_node
                # Index statements within entries
                if entry.get("statement"):
                    stmt = TaxNode(entry["statement"], f"{path}.{code}.statement")
                    self._statements.append(stmt)
            if node.type == "statement":
                self._statements.append(node)
            if node.type == "reference":
                self._references.append(node)
        for key, val in obj.items():
            if isinstance(val, dict):
                self._index(val, f"{path}.{key}")

    def get_node(self, semantic_id: str):
        return self._nodes.get(semantic_id)

    def get_value(self, semantic_id: str):
        node = self.get_node(semantic_id)
        return node.value if node else None

    def get_all_by_type(self, node_type: str):
        return [n for n in self._nodes.values() if n.type == node_type]

    def get_statements(self):
        return self._statements

    def get_references(self):
        return self._references

    def list_all_ids(self):
        return sorted(self._nodes.keys())


# ============================================================================
# §4  VALIDATOR — Runs taxonomy constraints
# ============================================================================

class ValidationResult:
    def __init__(self, constraint_id, severity, passed, message, expected=None, actual=None):
        self.constraint_id = constraint_id
        self.severity = severity
        self.passed = passed
        self.message = message
        self.expected = expected
        self.actual = actual

    def __repr__(self):
        status = "PASS" if self.passed else "FAIL"
        return f"[{status}] {self.constraint_id}: {self.message}"


def validate(doc: TaxDocument) -> list:
    results = []

    # 1. Box 4c = 4a + 4b
    v4a = doc.get_value("guaranteed_payments_services") or 0
    v4b = doc.get_value("guaranteed_payments_capital") or 0
    v4c = doc.get_value("guaranteed_payments_total") or 0
    expected = v4a + v4b
    passed = abs(v4c - expected) <= 0.01
    results.append(ValidationResult(
        "box_4c_equals_4a_plus_4b", "error", passed,
        f"Box 4c ({v4c}) {'==' if passed else '!='} Box 4a ({v4a}) + Box 4b ({v4b}) = {expected}",
        expected, v4c))

    # 2. Box 6b <= 6a
    v6a = doc.get_value("ordinary_dividends") or 0
    v6b = doc.get_value("qualified_dividends") or 0
    passed = v6b <= v6a
    results.append(ValidationResult(
        "box_6b_lte_6a", "error", passed,
        f"Qualified dividends ({v6b}) {'<=' if passed else '>'} Ordinary dividends ({v6a})",
        v6a, v6b))

    # 3. Capital account continuity
    cap = doc.get_value("partner.capital_account_analysis")
    if cap and isinstance(cap, dict):
        begin = cap.get("beginning") or 0
        contrib = cap.get("contributions") or 0
        inc_dec = cap.get("current_year_increase_decrease") or 0
        other = cap.get("other_increase_decrease") or 0
        wd = cap.get("withdrawals") or 0
        ending = cap.get("ending") or 0
        computed = begin + contrib + inc_dec + other + wd
        passed = abs(ending - computed) <= 1.00
        results.append(ValidationResult(
            "capital_account_continuity", "warning", passed,
            f"Ending ({ending}) {'==' if passed else '!='} computed ({computed}) [tol=$1]",
            computed, ending))

    # 4. Share percentages in [0, 1]
    shares = doc.get_value("partner.share_percentages")
    if shares and isinstance(shares, dict):
        all_valid = all(0 <= v <= 1 for v in shares.values() if isinstance(v, (int, float)))
        results.append(ValidationResult(
            "percentages_valid_range", "error", all_valid,
            f"All share percentages in [0,1]: {all_valid}"))

    # 5. ZZ classification checks
    for box_name in ["other_information"]:
        node = doc.get_node(box_name)
        if node:
            for entry in node.entries:
                if entry.get("code") == "ZZ":
                    has_class = bool(entry.get("semantic", {}).get("classification"))
                    results.append(ValidationResult(
                        f"box_20_zz_requires_classification", "error", has_class,
                        f"Box 20 Code ZZ classification present: {has_class}"))

    return results


# ============================================================================
# §5  ROUND-TRIP TEST
# ============================================================================

def normalize_yaml(text: str) -> str:
    """Normalize only trailing whitespace and the final newline for comparison."""
    lines = [line.rstrip() for line in text.splitlines()]
    return "\n".join(lines) + "\n"


def round_trip_test(original_text: str, re_emit_path: Path) -> tuple:
    """Parse original, re-emit, compare."""
    yaml = YAML()
    yaml.default_flow_style = False
    yaml.width = 120
    yaml.indent(mapping=2, sequence=4, offset=2)

    # Parse
    parsed = yaml.load(original_text)

    # Re-emit
    buf = io.StringIO()
    yaml.dump(parsed, buf)
    re_emitted = buf.getvalue()

    # Write re-emitted for inspection
    re_emit_path.write_text(re_emitted, encoding="utf-8", newline="\n")

    # Normalize and compare
    norm_orig = normalize_yaml(original_text)
    norm_re = normalize_yaml(re_emitted)

    if norm_orig == norm_re:
        return True, "PASS — Normalized serialization equivalent"

    # Generate diff for debugging
    diff = list(difflib.unified_diff(
        norm_orig.splitlines(keepends=True),
        norm_re.splitlines(keepends=True),
        fromfile="original",
        tofile="re-emitted",
        n=3))
    diff_text = "".join(diff[:100])  # Cap diff output
    return False, f"FAIL — Differences found:\n{diff_text}"


# ============================================================================
# §6  MAIN — Execute all phases
# ============================================================================

def main():
    output = []
    def log(msg):
        print(msg)
        output.append(msg)

    log("=" * 72)
    log("Open Tax Document (OTD) — Round-Trip Proof of Concept")
    log("=" * 72)
    log("")

    # ── Phase 1: EMIT ───────────────────────────────────────────────────
    log("PHASE 1: EMIT")
    log("-" * 40)
    emitter = OTDEmitter(redaction_policy="partial")
    emitted_text = emitter.emit_to_file(SOURCE_DATA, EMITTED_FILE)
    log(f"  Emitted: {display_path(EMITTED_FILE)}")
    log(f"  Size: {len(emitted_text):,} bytes")
    log(f"  Redacted fields: {emitter.fields_redacted}")
    log("")

    # ── Phase 2: PARSE ──────────────────────────────────────────────────
    log("PHASE 2: PARSE")
    log("-" * 40)
    yaml = YAML()
    raw = yaml.load(EMITTED_FILE.read_text(encoding="utf-8"))
    doc = TaxDocument(raw)
    log(f"  Envelope version: {doc.envelope.get('version')}")
    log(f"  Taxonomy: {doc.envelope.get('taxonomy', {}).get('id')}")
    log(f"  Total indexed nodes: {len(doc.list_all_ids())}")
    log(f"  Statements found: {len(doc.get_statements())}")
    log(f"  References found: {len(doc.get_references())}")
    log("")

    # ── Phase 3: VALIDATE ───────────────────────────────────────────────
    log("PHASE 3: VALIDATE")
    log("-" * 40)
    results = validate(doc)
    all_passed = True
    for r in results:
        status = "PASS" if r.passed else "FAIL"
        icon = "  ✓" if r.passed else "  ✗"
        log(f"{icon} [{r.severity.upper()}] {r.constraint_id}: {r.message}")
        if not r.passed and r.severity == "error":
            all_passed = False
    log(f"\n  Validation: {'ALL PASSED' if all_passed else 'FAILURES DETECTED'}")
    log("")

    # ── Phase 4: QUERY DEMO ─────────────────────────────────────────────
    log("PHASE 4: QUERY DEMO")
    log("-" * 40)

    # Semantic lookup
    log("  4a. Semantic lookup: 'ordinary_business_income'")
    node = doc.get_node("ordinary_business_income")
    log(f"      → {node}")
    log(f"      Value: ${node.value:,.2f}" if node and isinstance(node.value, (int, float)) else "      → Not found")

    # Value shortcut
    log("\n  4b. Value shortcut: 'guaranteed_payments_total'")
    val = doc.get_value("guaranteed_payments_total")
    log(f"      → ${val:,.2f}" if val else "      → None")

    # Statement enumeration
    log("\n  4c. Statement enumeration (flattened):")
    for stmt in doc.get_statements():
        log(f"      → {stmt.semantic_id} [{stmt.semantic.get('classification', 'n/a')}]")

    # Reference enumeration
    log("\n  4d. Reference enumeration:")
    for ref in doc.get_references():
        log(f"      → {ref.semantic_id} → {ref.target}")

    # Type filtering
    log("\n  4e. Type filter: all 'coded' nodes:")
    coded = doc.get_all_by_type("coded")
    for c in coded:
        log(f"      → {c.semantic_id} ({len(c.entries)} entries)")

    # All indexed IDs
    log(f"\n  4f. All {len(doc.list_all_ids())} indexed semantic IDs:")
    for sid in doc.list_all_ids():
        log(f"      • {sid}")
    log("")

    # ── Phase 5: ROUND-TRIP ─────────────────────────────────────────────
    log("PHASE 5: ROUND-TRIP")
    log("-" * 40)
    passed, message = round_trip_test(emitted_text, RE_EMITTED_FILE)
    log(f"  {message}")
    if passed:
        log(f"  Re-emitted: {display_path(RE_EMITTED_FILE)}")
        # Verify hashes
        h1 = hashlib.sha256(normalize_yaml(emitted_text).encode()).hexdigest()[:16]
        h2 = hashlib.sha256(normalize_yaml(RE_EMITTED_FILE.read_text(encoding="utf-8")).encode()).hexdigest()[:16]
        log(f"  SHA256 (original, normalized):   {h1}")
        log(f"  SHA256 (re-emitted, normalized): {h2}")
        log(f"  Match: {h1 == h2}")
    log("")

    # ── Summary ─────────────────────────────────────────────────────────
    log("=" * 72)
    log("SUMMARY")
    log("=" * 72)
    log(f"  Emit:       ✓ ({len(emitted_text):,} bytes)")
    log(f"  Parse:      ✓ ({len(doc.list_all_ids())} nodes indexed)")
    log(f"  Validate:   {'✓ All passed' if all_passed else '✗ Failures detected'}")
    log(f"  Query:      ✓ (semantic, value, type, statement, reference)")
    log(f"  Round-trip: {'✓ Normalized-equivalent' if passed else '✗ Differences found'}")
    log("")

    # Save results
    RESULTS_FILE.write_text("\n".join(output), encoding="utf-8", newline="\n")
    print(f"\nResults saved to: {display_path(RESULTS_FILE)}")

    return 0 if (all_passed and passed) else 1


if __name__ == "__main__":
    sys.exit(main())
