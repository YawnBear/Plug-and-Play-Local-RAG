# Security policy

## Supported versions

Security fixes are provided for the latest published release. Older releases,
experimental hardware profiles, locally modified packages, and development
checkouts are not release-qualified.

The public repository currently distributes source code. Any separately
published Personal installer is supported only when its release notes identify
it as qualified and it includes the signed manifest, checksums, and SBOM.

## Reporting a vulnerability

Do not open a public issue for a suspected vulnerability. Use GitHub's private
vulnerability reporting for this repository:

https://github.com/YawnBear/Plug-and-Play-Local-RAG/security/advisories/new

Include the affected version, operating system, impact, and a minimal sanitized
reproduction. Never attach real documents, credentials, database dumps,
private keys, setup or session tokens, environment files, or unrestricted
logs.

The maintainer will review valid reports, coordinate an appropriate fix and
disclosure window, and publish a GitHub Security Advisory when needed. There is
currently no paid bug-bounty program.

## Release trust

Qualified Personal packages use a signed manifest, checksums, and an SBOM.
Private signing keys are never stored in this repository. A source archive
generated automatically by GitHub is not, by itself, a verified Local RAG
Personal package.
