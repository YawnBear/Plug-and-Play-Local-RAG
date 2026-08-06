# Model and runtime license inventory

This inventory covers the fixed V8F Personal catalog. The signed release
manifest and `SBOM.cdx.json` are authoritative for the exact artifact versions
included in a particular package. Upstream licenses remain controlling.

| Component | Release use | Upstream license | Distribution |
| --- | --- | --- | --- |
| Qwen3 8B | Answer generation through Ollama | Apache-2.0 | Fetched by Ollama from the pinned allowlist |
| Qwen3 Embedding 0.6B | 1,024-dimensional embeddings through Ollama | Apache-2.0 | Fetched by Ollama from the pinned allowlist |
| BAAI bge-reranker-v2-m3 | CPU reranking | Apache-2.0 | Included in the signed package |
| PaddleOCR-VL 1.6 | CPU document parsing/OCR | Apache-2.0 | Included in the signed package |
| Ollama | Local model runtime; separately installed prerequisite | MIT | Installed by the user |
| pgvector/PostgreSQL image | Database and vector search | PostgreSQL license plus image component licenses | Pulled by immutable image digest |
| RustFS | S3-compatible local object storage | Apache-2.0 | Pulled by immutable image digest |
| MinIO Client (`mc`) | Object-storage provisioning tool | AGPL-3.0 | Included as an unmodified executable with source/license offer information |

Upstream license locations:

- https://huggingface.co/Qwen/Qwen3-8B
- https://huggingface.co/Qwen/Qwen3-Embedding-0.6B
- https://huggingface.co/BAAI/bge-reranker-v2-m3
- https://huggingface.co/PaddlePaddle/PaddleOCR-VL-1.6
- https://github.com/ollama/ollama/blob/main/LICENSE
- https://github.com/pgvector/pgvector/blob/master/LICENSE
- https://github.com/rustfs/rustfs/blob/main/LICENSE
- https://github.com/minio/mc/blob/master/LICENSE

The Local RAG Apache-2.0 license does not replace any license above. Release
packaging must include the exact upstream license and notice files required by
every redistributed component. Model outputs are not guaranteed accurate and
must be checked against the cited source pages.
