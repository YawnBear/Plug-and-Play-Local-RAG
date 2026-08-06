# Local RAG

Local RAG is a Windows-first, self-hosted application for searching private PDF
documents and asking questions about them. It uses Next.js, FastAPI,
PostgreSQL/pgvector, RustFS, Ollama, BAAI BGE reranking, and an isolated
PaddleOCR-VL service. Documents, prompts, retrieved passages, and answers stay
on the operator's PC or private LAN deployment.

This repository is the public source distribution. The optional signed Personal
installer bundle is separate and is not included in the source release.

**[Open the interactive frontend preview](https://yawnbear.github.io/Plug-and-Play-Local-RAG/)**

The preview uses fictional sample documents and runs entirely in the browser.
It demonstrates chat, citations, the Knowledge Base, system status, responsive
layout, and light/dark themes without connecting to a backend or uploading data.

## 1. Normal users

The easiest source-based installation is the included Windows setup launcher.
It installs the locked application dependencies, creates isolated local data
stores and secrets, downloads the approved models, builds the web application,
and opens the first-owner registration page.

### Requirements and downloads

Use 64-bit Windows 10 or 11. Install the following tools before running setup:

| Tool | Purpose | Official download or installation |
| --- | --- | --- |
| Docker Desktop | Runs PostgreSQL and RustFS | [Install Docker Desktop on Windows](https://docs.docker.com/desktop/setup/install/windows-install/) |
| Ollama | Runs the local generation and embedding models | [Download Ollama for Windows](https://ollama.com/download/windows) |
| Node.js | Builds and runs the web application; version 20+ is accepted and Node.js 24 LTS is recommended | [Download Node.js](https://nodejs.org/en/download) |
| uv | Installs the locked Python environments | [Install uv](https://docs.astral.sh/uv/getting-started/installation/) |
| Python | Needed for manual development; use Python 3.12+ | [Python releases for Windows](https://www.python.org/downloads/windows/) |

The official uv installer can also be run from PowerShell:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

After installing the prerequisites, restart the terminal so `node`, `corepack`,
`ollama`, and `uv` are available on `PATH`. Start Docker Desktop and Ollama
before continuing.

### Install Local RAG

1. Download and extract the [latest source release](https://github.com/YawnBear/Plug-and-Play-Local-RAG/releases/latest), or clone the repository.
2. Start Docker Desktop and Ollama.
3. Open the extracted Local RAG folder.
4. Double-click `Setup-Local-RAG.cmd`.
5. Keep the setup window open while dependencies, OCR packages, and Ollama
   models are downloaded. The first run can take a while.
6. When the browser opens, enter the one-time setup code shown by Local RAG.
7. Create your owner username, display name, and password, then sign in.

There is no default account or password. The setup code expires after 15
minutes and is used only for the first owner. If setup stops, read the error,
fix the prerequisite, and double-click `Setup-Local-RAG.cmd` again. The setup is
designed to resume safely without deleting existing data.

After installation, use the **Start Local RAG** Start-menu shortcut. Keep the
Local RAG window open while using the application.

## 2. Developers

Developers can use the same `Setup-Local-RAG.cmd` launcher for a complete
integrated source run. For hot reload, individual services, and tests, use the
development workflow below.

### Clone and install locked dependencies

```powershell
git clone https://github.com/YawnBear/Plug-and-Play-Local-RAG.git
Set-Location Plug-and-Play-Local-RAG

corepack enable
Copy-Item .env.example .env
Copy-Item apps\api\.env.example apps\api\.env
Copy-Item apps\web\.env.example apps\web\.env.local

pnpm.cmd install --frozen-lockfile
uv --directory apps/api sync --frozen
```

The repository pins pnpm through the `packageManager` field. Environment files
are ignored by Git; never commit passwords, tokens, documents, or local paths.

### Configure the development services

Edit the copied environment files and provide distinct credentials for:

- the PostgreSQL cluster administrator, migrator, API, worker, and maintenance
  roles;
- the RustFS root bootstrap account and the separately scoped API, ingestion,
  deletion, and maintenance accounts; and
- an absolute `OCR_PYTHON_EXECUTABLE` path.

Create the isolated OCR environment. Do not install PaddleOCR into the API
environment.

```powershell
uv venv --python 3.13 .venv-ocr
uv pip install --python .venv-ocr\Scripts\python.exe `
  --index-url https://www.paddlepaddle.org.cn/packages/stable/cpu/ `
  paddlepaddle==3.2.1
uv pip install --python .venv-ocr\Scripts\python.exe `
  "paddleocr[doc-parser]==3.7.0"
```

Set `OCR_PYTHON_EXECUTABLE` to the absolute path of
`.venv-ocr\Scripts\python.exe`. The checked-in examples contain the remaining
model, embedding-dimension, port, and OCR concurrency contracts.

Start the local data services:

```powershell
docker compose up -d --wait postgres rustfs
docker compose ps
```

Provision the least-privilege PostgreSQL roles with a trusted local `psql.exe`:

```powershell
$psql = "C:\path\to\psql.exe"
$psqlSha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $psql).Hash.ToLowerInvariant()

.\ops\security\provision-postgres-roles.ps1 `
  -DatabaseHost 127.0.0.1 `
  -DatabasePort 5432 `
  -DatabaseName rag `
  -ClusterAdministrator rag_cluster_admin `
  -PsqlPath $psql `
  -PsqlSha256 $psqlSha256
```

The role-provisioning script prompts for the distinct database passwords. Put
the same values into their matching local connection URLs, then initialize the
object store and database schema:

```powershell
uv --directory apps/api run python -m app.maintenance_cli storage-bootstrap
uv --directory apps/api run alembic upgrade head
uv --directory apps/api run alembic current

ollama pull qwen3:8b
ollama pull qwen3-embedding:0.6b
```

Create the first development administrator while the API and mutation workers
are stopped. The command interactively prompts for the username, display name,
and password:

```powershell
uv --directory apps/api run python -m app.maintenance_cli `
  --confirm-stopped bootstrap-admin
```

### Run in development

Check the complete development configuration without starting services:

```powershell
pnpm.cmd dev:check
```

Start the full loopback development graph:

```powershell
pnpm.cmd dev
```

The launcher supervises PostgreSQL, RustFS, Ollama, the web application, API,
ingestion worker, deletion worker, inference coordinator, and OCR service.
Press `Ctrl+C` once for a clean shutdown.

- Web application: `http://localhost:3000`
- API: `http://localhost:8000`
- API liveness: `http://localhost:8000/health`
- Dependency readiness: `http://localhost:8000/ready`

Useful focused commands:

```powershell
pnpm.cmd dev:web
pnpm.cmd dev:api
uv --directory apps/api run python -m app.dev_server --reload
uv --directory apps/api run python -m app.processes.ingestion_worker
uv --directory apps/api run python -m app.processes.deletion_worker
uv --directory apps/api run python -m app.coordinator_server
uv --directory apps/api run python -m app.ocr_service_server
```

Before opening a pull request, run:

```powershell
pnpm.cmd validate
```

This checks lint, launcher tests, API tests, web tests, TypeScript, and the
production web build. Hardware-dependent model, OCR, browser, backup, and
restore tests remain separate.

## Main functions

### Chatbot

- Ask questions in natural language against locally stored documents.
- Retrieve relevant chunks with vector search and rerank them before answer
  generation.
- Stream answers with document, page, section, and chunk citations.
- Open cited evidence in the Knowledge Base and inspect PDF highlights when
  available.
- Keep, search, rename, and delete conversation history.
- Limit each conversation to all ready files or selected folders/documents.
- State when the retrieved context is insufficient instead of inventing an
  unsupported answer.

### Knowledge Base

- Upload PDFs and track ingestion, parsing, OCR, chunking, and indexing status.
- Organize documents into folders.
- Keep private uploads private or grant team access where permitted.
- Open document details, inspect processing errors, retry failed ingestion, and
  safely rebuild documents when processing settings change.
- Preserve source filename, page, section, and chunk identities throughout the
  retrieval pipeline.

### Customization and system management

- Choose release-qualified answer-generation and reranking profiles.
- Select automatic, CPU, or qualified GPU OCR and tune allowed CPU threads and
  process counts.
- Choose the parser/chunking profile used for future uploads.
- Rebuild existing documents or the search index through guarded maintenance
  operations.
- View service health, configuration revisions, profile validation, backups,
  diagnostics, and maintenance state.

## General workflow

1. **Register the first administrator.** The normal setup opens `/setup`; enter
   the one-time code and create the owner account. Developers may use the
   interactive `bootstrap-admin` command before starting services.
2. **Open the Knowledge Base.** Sign in, optionally create folders, and choose
   **Upload PDF**.
3. **Add a document.** Select the destination folder and access scope, then
   choose the PDF and click **Upload here**.
4. **Wait for processing.** The ingestion worker stores the original file,
   parses or OCRs pages, creates chunks and embeddings, and marks the document
   **Ready** when it can be retrieved.
5. **Choose retrieval scope.** Open Chat and select **All ready files** or
   specific folders/documents from **Conversation scope**.
6. **Ask a question.** Local RAG embeds the question, retrieves candidate
   chunks, reranks them, and sends only the selected evidence to the local
   generation model.
7. **Verify the answer.** Use the citations to open the supporting page or PDF
   highlight in the Knowledge Base. If the evidence is insufficient, refine the
   question, change the conversation scope, or add more documents.
8. **Maintain the library.** Administrators can use System pages to change
   qualified models/OCR settings, select processing for new uploads, create
   verified backups, rebuild documents, or download privacy-safe diagnostics.

## How to customize Local RAG

### Change what a chat can retrieve

1. Open a conversation.
2. Select **Conversation scope**.
3. Choose **All ready files** or **Selected folders and files**.
4. Select at least one folder/document when using selected scope, then click
   **Save scope**.

### Change models or reranking

1. Sign in as an administrator and open **System > Models**.
2. Choose an available **Answer generation** profile and **Result reranking**
   profile.
3. Click **Review change**, confirm the affected services, enter the admin
   password, and click **Apply change**.

Only profiles qualified by the release and validated on the current computer
are selectable. Changes restart only the affected services and automatically
roll back if validation fails.

### Change OCR behavior

1. Open **System > OCR**.
2. Choose **Auto**, **CPU inference**, or a release-qualified **GPU inference**
   profile.
3. For explicit profiles, select allowed CPU threads and parallel OCR
   processes.
4. Review and apply the change with the administrator password.

### Change document processing

Open **System > Maintenance** to select the parser/chunking profile for future
uploads. Existing documents are not rewritten automatically. Use the guarded
rebuild actions when old documents must adopt new processing settings; data-
rewriting operations require the applicable backup and confirmation gates.

Developers adding a new model or device path must update the allowlisted
capability contracts in `ops/windows/v8a/capability-profiles.json`, add the
matching validation/tests, and preserve the embedding dimension and citation
identity contracts. Do not bypass profile validation with unverified fallback
values.

## Upcoming

- **Agentic RAG:** planned multi-step retrieval and tool-assisted workflows for
  questions that require decomposition, verification, or several evidence
  gathering passes.
- **GraphRAG:** planned entity and relationship graphs for navigating connected
  facts across documents and combining graph traversal with vector retrieval.

These capabilities are roadmap items and are not part of the current release.

## Privacy and security

Never submit real documents, filenames, prompts, answers, credentials, tokens,
environment files, private keys, database dumps, or unrestricted logs in a
public issue. Report suspected vulnerabilities privately through the
repository's GitHub Security Advisory page; see [SECURITY.md](SECURITY.md).

## License

Local RAG source code is licensed under the Apache License 2.0. Third-party
components and machine-learning models retain their upstream licenses. See
[LICENSE](LICENSE), [NOTICE](NOTICE),
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md),
[MODEL_LICENSES.md](MODEL_LICENSES.md), and [SBOM.cdx.json](SBOM.cdx.json).

## Contributing

Contributions are welcome. Please read [CONTRIBUTING.md](CONTRIBUTING.md) and
follow the [Code of Conduct](CODE_OF_CONDUCT.md) before opening an issue or
pull request.
