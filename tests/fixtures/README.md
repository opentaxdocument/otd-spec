
# Test Fixtures

Input fragments for exercising the production assembly and validation
pipeline (`skills/k1-otd/scripts/phase4_assemble.py` ->
`skills/k1-otd/scripts/validate_otd.py`).

## `hostile-k1/`

A deliberately non-conforming K-1 extraction fragment set. It is not a
valid K-1; it is a set of conditions that a correct pipeline must reject
or flag rather than silently accept.

| Condition | Correct behaviour |
|---|---|
| `box_16_checked: false` | Emit the K-3 non-furnishing notification; emit **no** K-3 reference target |
| `item_m: true` with a statement | Emit a boolean scalar plus an attached `item_m_built_in_gain_loss` statement |
| `item_k3: true`, no Box 20 Code X | **Reject.** IRS instructions require payment-obligation detail under Code X |
| Unobserved checkboxes | Emit `null` plus an `_unverified` marker -- never `false` |

### Why this lives in the repository

It previously lived outside the repo, under an artifact directory. That
placement caused a real defect: `validate_otd.py` resolved its taxonomy by
walking ancestors of the input file looking for `taxonomies/`. A document
outside the repo has no such ancestor, so validation silently degraded to
"rules were not enforced" and **exited 0 on a document that should have
failed**.

Two things came out of that. The validator now falls back to the canonical
mirror shipped beside it and fails closed if no taxonomy can be resolved.
And this fixture lives here, where any reviewer can reproduce the result
without a machine-local path.

### Running it

```bash
python skills/k1-otd/scripts/phase4_assemble.py \
  --fragments tests/fixtures/hostile-k1 \
  --out /tmp/hostile.otd.yaml

python skills/k1-otd/scripts/validate_otd.py --input /tmp/hostile.otd.yaml
```

Expected: assembly succeeds (a document is produced), validation **fails**
with `item_k3_requires_box20_x_statement`. Assembly succeeding is not a
defect -- the pipeline's job is to represent what was extracted faithfully,
including its faults, and let validation adjudicate.
