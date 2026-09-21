# Contributing to Open Tax Document

OTD is a public draft. Tax practitioners, software developers, and implementers
are welcome to challenge the specification, test the tools, and propose
improvements. Start with the [README](README.md) and [roadmap](docs/OTD-v03-Roadmap.md).

## Useful contributions

- Reconcile taxonomy fields and codes against the applicable IRS instructions.
- Test whether the footnote catalog captures information needed by preparers.
- Build an independent emitter or parser and report interoperability differences.
- Add authorized synthetic fixtures for unsupported layouts and edge cases.
- Reproduce a mismatch between a documented contract and the reference tools.

Use [Issues](https://github.com/opentaxdocument/otd-spec/issues) for concrete
defects and [Discussions](https://github.com/opentaxdocument/otd-spec/discussions)
for design questions. Include the form, year, taxonomy version, repository
revision, reproduction command, expected behavior, and actual result.

**Do not upload taxpayer documents, identifying information, credentials, or
private firm material.** Use a minimal synthetic example. Follow
[SECURITY.md](SECURITY.md) for security-sensitive reports.

## Taxonomy and specification changes

An OTD taxonomy governs OTD validation; it does not supersede tax law or IRS
instructions. Cite the source, year, and relevant section for tax changes.
AI-assisted derivations require qualified professional review.

Published taxonomy versions are immutable. Corrections need a new version and
changelog, with older versions retained for documents that name them. Explain
whether a proposal changes labels, data shape, semantics, or validation rules.
Do not silently reinterpret existing document versions.

Edit authoritative specifications in `spec/` and taxonomies in `taxonomies/`.
Refresh the bundled reference copies using the existing maintenance command:

```bash
python -B tests/check_mirrors.py --sync
python -B tests/check_mirrors.py
```

## Implementation changes

1. Follow the [quick start](README.md#quick-start) to install dependencies.
2. Add a focused regression demonstrating the defect or new contract.
3. Make the smallest change that addresses it.
4. Run the test programs listed in the README, the proof, and grammar validation.
5. Check `git diff --check` and inspect the complete diff.

Keep financial values exact across actual input/output boundaries. Preserve
unknown data, explicit zero, null, and unverified observations distinctly.
Never remove a source fact or weaken an assertion merely to obtain a pass.

The tests cover declared profiles and known counterexamples. A passing suite
is not proof of tax correctness, broad PDF support, or production readiness.

## Generated examples

Do not hand-edit generated snapshots to satisfy tests. Run the proof or bounded
demonstration into a separate output directory, inspect the differences and
provenance, then update the intended snapshot files. Keep source PDFs immutable.
The synthetic package contains mixed-year and intentionally inconsistent test
material; it is not a model return.

All current repository material is offered under the terms in [LICENSE](LICENSE).
Changes to licensing, governance, or the conformance contract require explicit
maintainer review.
