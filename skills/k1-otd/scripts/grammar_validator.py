
#!/usr/bin/env python
"""Grammar shape validator -- fail-closed preflight for k1-face-grammar files.

Closes defect D16: the grammar had NO shape validation, so a field could
declare a correct value at the WRONG NESTING LEVEL and be silently ignored.

Historical instance (2026-07-29): box_22 and box_23 declared `governing_label`
and `label_association` as DIRECT CHILDREN of the field instead of under
`anchors:`. The reader read `anchors` as {}, found no label, and returned
`no_label_no_unique_hint`. The labels were CORRECT and matched the printed
page exactly -- the grammar was semantically right and structurally wrong,
and nothing in the pipeline said so. That is a silent-ignore defect, the same
fail-quiet family as `except: return True`.

DESIGN RULES
  1. Fail CLOSED. A structural violation raises. It never degrades to a
     warning that lets extraction proceed against a misread grammar.
  2. Allow-lists are DERIVED FROM MEASUREMENT of the shipped grammar
     (d16-key-census probe, 2026-07-29), not from recollection. Writing them
     from memory would have rejected three valid fields -- see rule 3.
  3. `options` is deliberately permitted at BOTH levels because it carries two
     distinct meanings:
        field level  -> declared value vocabulary, e.g. ["yes", "no"]
        anchor level -> multi_checkbox_set option anchor definitions
     A naive "no anchor keys at field level" rule produces false positives on
     item_g, item_h1, and item_m. Measured, not assumed.
  4. Reader names are validated against the KNOWN VOCABULARY -- the union of
     READER_REQUIRED_ANCHORS keys and the caller's live READERS registry -- and
     NOT against the live registry alone. A first attempt validated against the
     live registry only, and on its first enforced run it rejected 15 VALID
     fields: this pipeline DELIBERATELY declares readers it has not built yet
     (bounded_text, bounded_identity, address_block, coded_rows,
     conditional_record), and those fall through to a not-implemented fallback
     that emits unresolved(reader_not_implemented) by design.
     Therefore:
       unknown reader name          -> ERROR   (typo or invention)
       known but not yet implemented -> WARNING (tracked design state)
     The union keeps the anti-drift property -- any reader present in the live
     registry is automatically valid vocabulary -- without treating a recorded
     scoping decision as a structural defect.
  5. Every finding names the field and the key. A validator that says only
     "invalid grammar" has not helped anyone.
"""

import copy
import sys
from pathlib import Path

try:
    import yaml
except ImportError as exc:  # pragma: no cover
    print("ERROR: missing dependency: %s" % exc, file=sys.stderr)
    raise


# --------------------------------------------------------------------------
# Measured vocabulary (source: d16-key-census, 2026-07-29, 53 fields)
# --------------------------------------------------------------------------

TOP_REQUIRED = {"schema_version", "form", "status_values", "fields"}
TOP_OPTIONAL = {
    "status", "source_evidence", "coordinate_policy", "fit_signals",
    "regions", "validation", "agent_review",
}
TOP_ALLOWED = TOP_REQUIRED | TOP_OPTIONAL

FIELD_REQUIRED = {"semantic_id", "region", "reader", "value_type"}
FIELD_OPTIONAL = {
    "anchors", "box_number_2025", "cardinality", "notes", "face_slots",
    "overflow", "codes_ref", "signed", "constraints", "options",
    "taxonomy_gap", "excluded_not_taxpayer_data", "yes_requires_statement",
    "target_form", "assert_only_when_checked", "may_attach_statement",
    "value_aliases",
}
FIELD_ALLOWED = FIELD_REQUIRED | FIELD_OPTIONAL

ANCHOR_ALLOWED = {
    "label", "label_association", "frame_row_top_hint", "frame_x0_hint",
    "governing_label", "governing_checkbox_label", "end_anchor_field",
    "row_header_top_hint", "rows", "columns", "column_header", "row_context",
    "option_left", "option_right", "options", "dependent_fields",
    "absence_probe_tokens",
}

# Anchor keys that must NEVER appear at field level. `options` is excluded --
# see DESIGN RULE 3. This set is what catches the box_22 signature.
ANCHOR_ONLY = ANCHOR_ALLOWED - {"options"}

# Per-reader mandatory anchor keys, measured from the shipped grammar.
# An empty set means "this reader binds by another mechanism" -- coded_rows
# uses codes_ref/face_slots/overflow and carries no anchors block at all.
READER_REQUIRED_ANCHORS = {
    "address_block":         {"label"},
    "bounded_identity":      {"label"},
    "bounded_text":          {"label"},
    "scalar_cell":           {"label"},
    "header_period":         {"column_header"},
    "ledger":                {"rows"},
    "two_column_grid":       {"columns", "rows"},
    "exclusive_choice_pair": {"option_left", "option_right"},
    "multi_checkbox_set":    {"options"},
    "conditional_record":    {"governing_checkbox_label"},
    "coded_rows":            set(),
    "independent_checkbox":  set(),
    "attachment_reference":  set(),
    "not_printed":           {"absence_probe_tokens"},
}

# Readers that bind to a geometric checkbox frame. Each MUST carry at least
# one binding signal -- a governing label or a coordinate hint -- otherwise
# the frame-first binder has nothing to disambiguate with and the field will
# silently return checkbox_frame_not_found.
FRAME_BOUND_READERS = {
    "independent_checkbox", "attachment_reference", "exclusive_choice_pair",
    "multi_checkbox_set", "conditional_record",
}
BINDING_SIGNALS = {
    "governing_label", "governing_checkbox_label", "frame_x0_hint",
    "frame_row_top_hint", "option_left", "option_right", "options",
}

# A null semantic_id is only honest when the gap is DECLARED. Otherwise it is
# an untracked hole in taxonomy binding.
NULL_SEMANTIC_EXCUSES = {"taxonomy_gap", "excluded_not_taxpayer_data"}


class GrammarShapeError(Exception):
    """Raised when a grammar is structurally invalid. Fail-closed."""


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------

def validate_grammar(grammar, path="<memory>", known_readers=None):
    """Validate grammar shape. Returns (errors, warnings) -- never raises for
    content reasons, so callers can choose their own severity policy.

    `known_readers` should be the live READERS registry keys from the module
    that will execute this grammar. When omitted, reader names are checked
    against READER_REQUIRED_ANCHORS instead (weaker, but never silent)."""
    errors, warns = [], []

    if not isinstance(grammar, dict):
        return (["%s: top level is %s, expected a mapping"
                 % (path, type(grammar).__name__)], warns)

    # ---- top level ----
    for key in sorted(TOP_REQUIRED - set(grammar)):
        errors.append("top level: required key '%s' is missing" % key)
    for key in sorted(set(grammar) - TOP_ALLOWED):
        errors.append("top level: unknown key '%s' (allowed: %s)"
                      % (key, ", ".join(sorted(TOP_ALLOWED))))

    declared_regions = set()
    regions = grammar.get("regions")
    if regions is not None:
        if not isinstance(regions, list):
            errors.append("regions: expected a list, got %s"
                          % type(regions).__name__)
        else:
            for idx, reg in enumerate(regions):
                if not isinstance(reg, dict) or "id" not in reg:
                    errors.append("regions[%d]: expected a mapping with 'id'"
                                  % idx)
                else:
                    declared_regions.add(reg["id"])

    # Vocabulary = every reader name the spec recognizes, implemented or not.
    # See DESIGN RULE 4.
    reader_vocabulary = set(READER_REQUIRED_ANCHORS) | (
        set(known_readers) if known_readers else set())
    implemented = set(known_readers) if known_readers else reader_vocabulary

    fields = grammar.get("fields")
    if fields is None:
        return (errors, warns)
    if not isinstance(fields, dict):
        errors.append("fields: expected a mapping, got %s"
                      % type(fields).__name__)
        return (errors, warns)

    frame_claims = {}

    # ---- per field ----
    for fkey in sorted(fields):
        fdef = fields[fkey]
        if not isinstance(fdef, dict):
            errors.append("%s: field is %s, expected a mapping"
                          % (fkey, type(fdef).__name__))
            continue

        for key in sorted(FIELD_REQUIRED - set(fdef)):
            errors.append("%s: required key '%s' is missing" % (fkey, key))

        unknown = sorted(set(fdef) - FIELD_ALLOWED)
        misplaced = sorted(set(fdef) & ANCHOR_ONLY)
        for key in unknown:
            if key in ANCHOR_ONLY:
                continue  # reported with a sharper message below
            errors.append("%s: unknown field key '%s'" % (fkey, key))
        for key in misplaced:
            errors.append(
                "%s: key '%s' is an ANCHOR key declared at FIELD level -- "
                "nest it under 'anchors:' or the reader will silently ignore "
                "it (this is the box_22 defect signature)" % (fkey, key))

        # semantic_id honesty
        if "semantic_id" in fdef and fdef["semantic_id"] is None:
            if not any(fdef.get(k) for k in NULL_SEMANTIC_EXCUSES):
                errors.append(
                    "%s: semantic_id is null with no declared gap marker "
                    "(expected one of: %s)"
                    % (fkey, ", ".join(sorted(NULL_SEMANTIC_EXCUSES))))

        # region must exist
        region = fdef.get("region")
        if declared_regions and region is not None \
                and region not in declared_regions:
            errors.append("%s: region '%s' is not declared in regions (%s)"
                          % (fkey, region, ", ".join(sorted(declared_regions))))

        # A reader name outside the known vocabulary is a structural error.
        # A known name that is not yet implemented is a tracked design state.
        reader = fdef.get("reader")
        if reader is not None and reader not in reader_vocabulary:
            errors.append(
                "%s: reader '%s' is not a known reader type (vocabulary: %s)"
                % (fkey, reader, ", ".join(sorted(reader_vocabulary))))
        elif reader is not None and reader not in implemented:
            warns.append(
                "%s: reader '%s' is declared but not yet implemented; this "
                "field will emit unresolved(reader_not_implemented)"
                % (fkey, reader))

        # ---- value aliases ----
        value_aliases = fdef.get("value_aliases")
        if value_aliases is not None:
            if reader != "bounded_text" or fdef.get("value_type") != "enum":
                errors.append(
                    "%s: value_aliases requires reader 'bounded_text' and value_type 'enum'"
                    % fkey)
            if not isinstance(value_aliases, dict):
                errors.append("%s: value_aliases is %s, expected a mapping"
                              % (fkey, type(value_aliases).__name__))
            elif not value_aliases:
                errors.append("%s: value_aliases must not be empty" % fkey)
            else:
                for printed, canonical in value_aliases.items():
                    if not isinstance(printed, str) or not printed.strip():
                        errors.append(
                            "%s: value_aliases contains an empty or non-string printed value"
                            % fkey)
                    if not isinstance(canonical, str) or not canonical.strip():
                        errors.append(
                            "%s: value_aliases[%r] has an empty or non-string canonical value"
                            % (fkey, printed))

        # ---- anchors ----
        anchors = fdef.get("anchors")
        if anchors is None:
            anchors = {}
        elif not isinstance(anchors, dict):
            errors.append("%s: anchors is %s, expected a mapping"
                          % (fkey, type(anchors).__name__))
            anchors = {}
        else:
            for key in sorted(set(anchors) - ANCHOR_ALLOWED):
                errors.append("%s: unknown anchor key 'anchors.%s'"
                              % (fkey, key))

        required = READER_REQUIRED_ANCHORS.get(reader)
        if required:
            for key in sorted(required - set(anchors)):
                errors.append("%s: reader '%s' requires anchors.%s"
                              % (fkey, reader, key))

        if reader in FRAME_BOUND_READERS:
            if not (set(anchors) & BINDING_SIGNALS):
                errors.append(
                    "%s: reader '%s' binds to a geometric frame but declares "
                    "no binding signal (need one of: %s)"
                    % (fkey, reader, ", ".join(sorted(BINDING_SIGNALS))))

        # ---- duplicate frame ownership ----
        x0 = anchors.get("frame_x0_hint")
        top = anchors.get("frame_row_top_hint")
        if x0 is not None and top is not None:
            claim = (round(float(x0), 2), round(float(top), 2))
            if claim in frame_claims:
                errors.append(
                    "%s: coordinate hint %s is already claimed by '%s' -- two "
                    "fields cannot own one geometric frame"
                    % (fkey, claim, frame_claims[claim]))
            else:
                frame_claims[claim] = fkey

    return (errors, warns)


def validate_grammar_or_die(grammar, path="<memory>", known_readers=None):
    """Fail-closed wrapper. Returns warnings; raises on any error."""
    errors, warns = validate_grammar(grammar, path, known_readers)
    if errors:
        raise GrammarShapeError(
            "grammar shape validation FAILED for %s (%d error(s)):\n  - %s"
            % (path, len(errors), "\n  - ".join(errors)))
    return warns


# --------------------------------------------------------------------------
# Self-test: positive control + negative cases
# --------------------------------------------------------------------------

DEFAULT_GRAMMAR = str(
    Path(__file__).resolve().parent.parent
    / "grammars"
    / "k1-1065-2025.grammar.yaml"
)


def _load(path):
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _first_field_with(fields, reader):
    for key in sorted(fields):
        if isinstance(fields[key], dict) and fields[key].get("reader") == reader:
            return key
    return sorted(fields)[0]


def _selftest(path=DEFAULT_GRAMMAR):
    real = _load(path)
    ok = True

    print("=" * 92, flush=True)
    print("POSITIVE CONTROL -- the shipped grammar must pass clean", flush=True)
    errors, warns = validate_grammar(real, path)
    if errors:
        ok = False
        print("  FAIL: %d error(s) on a grammar believed valid:" % len(errors),
              flush=True)
        for e in errors:
            print("    %s" % e, flush=True)
    else:
        print("  PASS: 0 errors, %d warning(s)" % len(warns), flush=True)

    fields = real["fields"]
    cb = _first_field_with(fields, "independent_checkbox")
    any_field = sorted(fields)[0]

    def mutate(label, fn, expect_substring):
        nonlocal ok
        g = copy.deepcopy(real)
        fn(g)
        errs, _ = validate_grammar(g, "<mutated:%s>" % label)
        hit = any(expect_substring in e for e in errs)
        print("  [%s] %-46s errors=%d" % ("OK " if hit else "FAIL", label,
                                          len(errs)), flush=True)
        if not hit:
            ok = False
            print("        expected an error containing %r" % expect_substring,
                  flush=True)
            for e in errs[:4]:
                print("        got: %s" % e, flush=True)

    print("=" * 92, flush=True)
    print("NEGATIVE CASES -- each malformation must be rejected", flush=True)

    # 1. THE historical D16 signature: anchor key at field level.
    mutate("box_22 signature (governing_label at field level)",
           lambda g: g["fields"][cb].__setitem__(
               "governing_label", "Some printed label"),
           "ANCHOR key declared at FIELD level")

    # 2. Unknown field key -- typo or invented vocabulary.
    mutate("unknown field key",
           lambda g: g["fields"][any_field].__setitem__("labl", "typo"),
           "unknown field key")

    # 3. Unknown anchor key.
    mutate("unknown anchor key",
           lambda g: g["fields"][cb].setdefault("anchors", {}).__setitem__(
               "governing_lable", "typo"),
           "unknown anchor key")

    # 4. Missing required field key.
    mutate("missing required key (value_type)",
           lambda g: g["fields"][any_field].pop("value_type", None),
           "required key 'value_type' is missing")

    # 5. anchors is not a mapping.
    mutate("anchors is a list, not a mapping",
           lambda g: g["fields"][cb].__setitem__("anchors", ["oops"]),
           "anchors is list")

    # 6. Reader name outside the known vocabulary (typo or invention).
    mutate("unknown reader name",
           lambda g: g["fields"][any_field].__setitem__(
               "reader", "telepathy_reader"),
           "is not a known reader type")

    # 7. Region that is not declared.
    mutate("undeclared region",
           lambda g: g["fields"][any_field].__setitem__("region", "part_ix"),
           "is not declared in regions")

    # 8. Two fields claiming one geometric frame (the Box 16 defect).
    def _dup_frame(g):
        donor = None
        for k, v in g["fields"].items():
            a = v.get("anchors") or {}
            if a.get("frame_x0_hint") is not None \
                    and a.get("frame_row_top_hint") is not None:
                donor = (k, a["frame_x0_hint"], a["frame_row_top_hint"])
                break
        if donor is None:
            g["fields"][cb].setdefault("anchors", {}).update(
                {"frame_x0_hint": 100.0, "frame_row_top_hint": 200.0})
            donor = (cb, 100.0, 200.0)
        thief = "zz_frame_thief"
        g["fields"][thief] = {
            "semantic_id": "test.thief", "region": "header",
            "reader": "independent_checkbox", "value_type": "boolean",
            "anchors": {"frame_x0_hint": donor[1],
                        "frame_row_top_hint": donor[2]},
        }
    mutate("two fields claiming one frame", _dup_frame,
           "cannot own one geometric frame")

    # 9. Frame-bound reader with no binding signal at all.
    mutate("frame-bound reader with no binding signal",
           lambda g: g["fields"][cb].__setitem__("anchors", {}),
           "no binding signal")

    # 10. Value aliases must be a non-empty string-to-string mapping.
    mutate("value_aliases is not a mapping",
           lambda g: g["fields"]["item_i1"].__setitem__(
               "value_aliases", ["PARTNERSHIP (LIMITED)", "partnership"]),
           "value_aliases is list")

    mutate("value_aliases canonical value is blank",
           lambda g: g["fields"]["item_i1"].__setitem__(
               "value_aliases", {"PARTNERSHIP (LIMITED)": ""}),
           "empty or non-string canonical value")

    print("=" * 92, flush=True)
    print("SELFTEST: %s" % ("ALL PASS" if ok else "FAILURES PRESENT"),
          flush=True)
    return 0 if ok else 1


def main(argv):
    args = [a for a in argv[1:] if a != "--selftest"]
    if "--selftest" in argv[1:]:
        return _selftest(args[0] if args else DEFAULT_GRAMMAR)

    path = args[0] if args else DEFAULT_GRAMMAR
    errors, warns = validate_grammar(_load(path), path)
    for w in warns:
        print("WARN:  %s" % w, flush=True)
    for e in errors:
        print("ERROR: %s" % e, flush=True)
    print("%s: %d error(s), %d warning(s)" % (path, len(errors), len(warns)),
          flush=True)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
