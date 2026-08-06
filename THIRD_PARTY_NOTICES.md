# Third-party notices

Local RAG combines third-party libraries, runtimes, container images, and
machine-learning models. Each remains under its upstream license. The complete
machine-readable inventory for the release is `SBOM.cdx.json`; the fixed model
catalog and upstream license links are in `MODEL_LICENSES.md`.

Redistributed release packages must include the license/notice material shipped
by each component. In particular, the bundled unmodified MinIO Client (`mc`) is
AGPL-3.0. The release must include its license, notices, and a durable link to
the corresponding source for the exact distributed build:
https://github.com/minio/mc.

The PostgreSQL/pgvector and RustFS services are pulled as immutable container
image digests. Docker Desktop and Ollama are separately installed prerequisites
and are not relicensed or redistributed by this repository.

This document is informational and does not replace any upstream license.
