# Contributing to Local RAG

Thank you for helping make private, local document search easier to operate.
Local RAG accepts contributions under Apache License 2.0. No contributor
license agreement is required at this time.

## Before you start

- Read `README.md` and the relevant source and test files for the area you plan
  to change.
- Search existing issues before opening a duplicate.
- For a large feature or public contract change, open a design issue before
  writing code.
- Never include document content, credentials, setup codes, production data,
  private model files, or identifying diagnostics in an issue or pull request.

## Development setup

The qualified developer platform is 64-bit Windows 10/11. Install Python 3.12,
`uv`, Node.js, pnpm 11.8, Docker Desktop, and Ollama. Then run:

```powershell
uv --directory apps/api sync
pnpm.cmd install --frozen-lockfile
pnpm.cmd validate
python ops/windows/v8a/validate_contracts.py
```

Integration and live hardware tests are opt-in. Their prerequisites and exact
commands are in `README.md` and `LOCAL_RAG_SETUP.md`.

## Pull requests

Keep changes narrowly scoped. Add a regression test for behavior changes when
practical, update user documentation when an operator-facing contract changes,
and report exactly which checks you ran. A successful build is not a substitute
for the relevant runtime or migration test.

By submitting a contribution, you agree that it is licensed under Apache
License 2.0 as described in section 5 of `LICENSE`. Please follow
`CODE_OF_CONDUCT.md`.
