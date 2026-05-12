"""
state_grid_parsers.py — Reusable State Grid Parsing Strategies

Part of the OTD K-1 Extraction Skill Pack.

PURPOSE
-------
State schedule grids on K-1s vary dramatically by issuer and OCR engine.
This library accumulates successful parsing strategies so each new format
builds on prior work rather than starting from scratch.

USAGE
-----
Call `parse_state_grid(text)` and it will try strategies in priority order,
returning the first that produces a non-empty result.

To add a new strategy:
1. Write a function `parse_<format_name>(lines) -> list[dict] | None`
2. Add it to STRATEGY_REGISTRY (with a name and description)
3. Submit a PR describing the format you solved

STRATEGY CONTRACT
-----------------
Each strategy function:
- Accepts: list[str] lines (from a single page text block)
- Returns: list[dict] with at minimum {"j": "<state_code>", ...numeric fields...}
           OR None if the strategy cannot parse this format

COLUMN CONVENTIONS (standard across strategies)
-------------------------------------------------
j            : 2-letter state code (or "NYC")
l1           : Line 1 — Ordinary Business Income (Loss)
l2           : Line 2 — Net Rental Real Estate Income (Loss)
l5           : Line 5 — Interest Income
l6a          : Line 6A — Ordinary Dividends
l8           : Line 8 — Net Short-Term Capital Gain (Loss)
l9a          : Line 9A — Net Long-Term Capital Gain (Loss)
l10          : Line 10 — Net Section 1231 Gain (Loss)
l13w         : Line 13W — Other Deductions
ded          : Combined deductions (Lines 12, 13A-13D)
depl         : Tentative Depletion
wh           : State Taxes Withheld
comp         : Composite Tax Paid
elt          : Entity Level Tax Credit
ubti         : Unrelated Business Taxable Income
"""

import re
from typing import Optional

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Known 2-letter US state codes + NYC + PR + VI + GU for robustness
_STATE_CODES = {
    "AL","AK","AZ","AR","CA","CO","CT","DE","DC","FL","GA","HI","ID","IL",
    "IN","IA","KS","KY","LA","ME","MD","MA","MI","MN","MS","MO","MT","NE",
    "NV","NH","NJ","NM","NY","NYC","NC","ND","OH","OK","OR","PA","RI","SC",
    "SD","TN","TX","UT","VT","VA","WA","WV","WI","WY","PR","VI","GU",
}


def _is_state(token: str) -> bool:
    return token.strip().upper() in _STATE_CODES


def _parse_number(s: str) -> Optional[float]:
    """Parse a numeric string with optional commas, parens for negatives."""
    s = s.strip().replace(",", "").replace(" ", "")
    if not s or s in ("-", "—", "–"):
        return None
    negative = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    try:
        val = float(s)
        return -val if negative else val
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Strategy 1: Pipe-delimited rows  (e.g.  AL | 12,345 | 6,789 | ... )
# ---------------------------------------------------------------------------

def parse_pipe_delimited(lines: list[str]) -> Optional[list[dict]]:
    """
    Handles pipe-delimited state grids.

    Format example:
        AL | 12,345 | (6,789) | 100
        AK | 18,538 | 15,620  | ...

    Detects presence of pipe characters as the discriminator.
    Column order is inferred from the header row (if present) or
    falls back to a positional mapping.
    """
    pipe_lines = [l for l in lines if "|" in l and any(c.isdigit() for c in l)]
    if not pipe_lines:
        return None

    results = []
    for line in pipe_lines:
        parts = [p.strip() for p in line.split("|")]
        if not parts or not _is_state(parts[0]):
            continue
        entry = {"j": parts[0].strip().upper()}
        # Positional fallback — columns 1..N mapped to standard keys
        col_keys = ["l1", "l2", "l5", "l6a", "l8", "l9a", "l10", "ded", "depl", "wh", "comp", "elt"]
        for i, key in enumerate(col_keys, start=1):
            if i < len(parts):
                val = _parse_number(parts[i])
                if val is not None:
                    entry[key] = val
        if len(entry) > 1:
            results.append(entry)

    return results if results else None


# ---------------------------------------------------------------------------
# Strategy 2: Fixed-width space-aligned columns
# ---------------------------------------------------------------------------

def parse_space_aligned(lines: list[str]) -> Optional[list[dict]]:
    """
    Handles space-aligned columnar state grids where columns are inferred
    by consistent character-position alignment.

    Approach:
    1. Find rows that start with a 2-letter state code.
    2. Infer column boundaries from the header row (or use heuristics).
    3. Extract numeric values by column position.

    This strategy works best when the OCR has preserved consistent spacing.
    """
    state_lines = [l for l in lines if re.match(r"^\s{0,4}[A-Z]{2,3}\s", l)]
    if len(state_lines) < 3:
        return None

    results = []
    for line in state_lines:
        tokens = line.split()
        if not tokens or not _is_state(tokens[0]):
            continue
        entry = {"j": tokens[0].upper()}
        # Extract all numeric tokens after the state code
        nums = []
        for t in tokens[1:]:
            val = _parse_number(t)
            if val is not None:
                nums.append(val)
        # Map to standard keys positionally
        col_keys = ["l1", "l2", "l5", "l6a", "l8", "l9a", "l10", "ded", "depl", "wh", "comp", "elt"]
        for i, key in enumerate(col_keys):
            if i < len(nums):
                entry[key] = nums[i]
        if len(entry) > 1:
            results.append(entry)

    return results if results else None


# ---------------------------------------------------------------------------
# Strategy 3: Split/wrapped state codes  (e.g. "ALABAM | A" across cells)
# ---------------------------------------------------------------------------

def parse_wrapped_state_code(lines: list[str]) -> Optional[list[dict]]:
    """
    Handles grids where state names are split across two OCR cells due to
    column-boundary artifacts (e.g., 'ALABAM' on one line, 'A' on the next,
    or 'ALABAM | A | 12,345 | ...' in a pipe-delimited row).

    Strategy: reconstruct the state code by concatenating split tokens,
    then delegate to pipe_delimited or space_aligned for the numeric portion.
    """
    # Look for lines containing partial state names (5+ alpha chars at start)
    reconstructed = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        # Detect potential split: line starts with 4-6 alpha chars but is not a full state code
        m = re.match(r"^([A-Z]{4,6})\s*\|?\s*(.*)$", line)
        if m:
            prefix = m.group(1)
            rest = m.group(2)
            # Check if next line starts with 1-2 chars that complete a state code
            if i + 1 < len(lines):
                next_tok = lines[i + 1].strip().split()[0] if lines[i + 1].strip() else ""
                candidate = (prefix + next_tok)[:3]
                if _is_state(candidate):
                    reconstructed.append(candidate + " " + rest + " " + lines[i + 1])
                    i += 2
                    continue
        reconstructed.append(line)
        i += 1

    if not reconstructed:
        return None

    # Delegate to pipe_delimited on the reconstructed lines
    result = parse_pipe_delimited(reconstructed)
    if result:
        return result
    return parse_space_aligned(reconstructed)


# ---------------------------------------------------------------------------
# Strategy Registry
# ---------------------------------------------------------------------------

STRATEGY_REGISTRY = [
    {
        "name": "pipe_delimited",
        "description": "Pipe-separated (|) columns with 2-letter state code in first cell",
        "fn": parse_pipe_delimited,
    },
    {
        "name": "space_aligned",
        "description": "Space-aligned fixed-width columns; state code starts each row",
        "fn": parse_space_aligned,
    },
    {
        "name": "wrapped_state_code",
        "description": "State name split across OCR cells (e.g. 'ALABAM' + 'A'); reconstructs then delegates",
        "fn": parse_wrapped_state_code,
    },
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_state_grid(text: str) -> tuple[Optional[list[dict]], Optional[str]]:
    """
    Try all registered strategies in priority order.

    Returns:
        (results, strategy_name) — results is a list of row dicts, strategy_name is the winner.
        (None, None) — if no strategy succeeded.
    """
    lines = text.splitlines()
    for strategy in STRATEGY_REGISTRY:
        try:
            result = strategy["fn"](lines)
            if result:
                return result, strategy["name"]
        except Exception:
            continue  # Strategy failed; try the next one
    return None, None


def describe_strategies() -> str:
    """Return a human-readable summary of registered strategies."""
    lines = ["Registered state grid parsing strategies:"]
    for i, s in enumerate(STRATEGY_REGISTRY, 1):
        lines.append(f"  {i}. {s['name']}: {s['description']}")
    return "\n".join(lines)


if __name__ == "__main__":
    print(describe_strategies())
