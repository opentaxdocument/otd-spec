# Security and sensitive data

OTD is a public draft and reference toolkit, not a security-certified or
production-certified tax-processing service. No formal security support window
or response-time commitment has been established.

## Reporting a vulnerability

Contact the maintainer privately at [tom@crimsontreesoftware.com](mailto:tom@crimsontreesoftware.com)
before publishing security-sensitive details. Include the repository revision,
affected tool, impact, and a minimal synthetic reproduction when possible.

Do not send credentials, private keys, live taxpayer documents, or unredacted
taxpayer identifiers. Public issues, discussions, pull requests, and CI logs
must not contain sensitive source data.

## Processing documents safely

- Treat PDFs, YAML, JSON, taxonomies, and extensions as untrusted input.
- Use an isolated environment and operating-system resource limits for
  untrusted files. Parser validation is not a sandbox.
- Use reviewed, locally available taxonomies pinned to the document's version.
  Do not execute code embedded in taxonomies or extension files.
- Preserve originals and source hashes; keep outputs and logs in a controlled
  artifact directory with appropriate access and retention.
- Review dependency updates before deploying. Direct dependencies are pinned;
  the repository does not yet provide a fully locked transitive environment.
- Validation is read-only by default. An explicitly requested confidence update
  records a hash of the validated input; it is not a signature, an authenticity
  guarantee, or a lock against arbitrary concurrent writers.

A passing conformance check does not establish source authenticity, tax-law
accuracy, the safety of every parser dependency, or the correctness of an AI
system's conclusions.
