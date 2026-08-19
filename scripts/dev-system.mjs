import { randomBytes } from "node:crypto";
import {
  accessSync,
  constants,
  existsSync,
  mkdirSync,
  readdirSync,
  readFileSync,
} from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawn } from "node:child_process";
import { createServer } from "node:net";
import { fileURLToPath, pathToFileURL } from "node:url";
import { createInterface } from "node:readline";

const scriptDirectory = path.dirname(fileURLToPath(import.meta.url));
export const repositoryRoot = path.resolve(scriptDirectory, "..");
const isWindows = process.platform === "win32";
const commandNames = {
  docker: isWindows ? "docker.exe" : "docker",
  ollama: isWindows ? "ollama.exe" : "ollama",
  pnpm: isWindows ? "pnpm.cmd" : "pnpm",
  uv: isWindows ? "uv.exe" : "uv",
};
export const developmentListenerSpecs = [
  { name: "web", host: "127.0.0.1", port: 3000 },
  { name: "API", host: "127.0.0.1", port: 8000 },
  { name: "inference coordinator", host: "127.0.0.1", port: 8100 },
  { name: "OCR service", host: "127.0.0.1", port: 8101 },
];

const sensitiveDatabaseVariables = [
  "DATABASE_URL",
  "WORKER_DATABASE_URL",
  "MAINTENANCE_DATABASE_URL",
  "MIGRATION_DATABASE_URL",
];
const privilegedVariables = [
  ...sensitiveDatabaseVariables,
  "POSTGRES_CLUSTER_ADMIN_PASSWORD",
  "RUSTFS_ROOT_ACCESS_KEY",
  "RUSTFS_ROOT_SECRET_KEY",
];
const apiOnlyVariables = ["ENABLE_V6_ADAPTIVE_PARSING"];
const activeOneShotChildren = new Set();
let stopRequested = false;
let shutdownController = new AbortController();

function timeoutSignal(milliseconds) {
  return AbortSignal.any([
    AbortSignal.timeout(milliseconds),
    shutdownController.signal,
  ]);
}

function stripInlineComment(value) {
  let quoted = false;
  let quote = "";
  for (let index = 0; index < value.length; index += 1) {
    const character = value[index];
    if ((character === '"' || character === "'") && value[index - 1] !== "\\") {
      if (!quoted) {
        quoted = true;
        quote = character;
      } else if (quote === character) {
        quoted = false;
      }
    }
    if (!quoted && character === "#" && /\s/.test(value[index - 1] ?? "")) {
      return value.slice(0, index).trimEnd();
    }
  }
  return value.trimEnd();
}

export function parseEnv(text) {
  const result = {};
  for (const rawLine of text.replace(/^\uFEFF/, "").split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith("#")) {
      continue;
    }
    const normalized = line.startsWith("export ") ? line.slice(7).trim() : line;
    const separator = normalized.indexOf("=");
    if (separator < 1) {
      throw new Error(`invalid environment line: ${rawLine}`);
    }
    const name = normalized.slice(0, separator).trim();
    if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(name)) {
      throw new Error(`invalid environment variable name: ${name}`);
    }
    let value = stripInlineComment(normalized.slice(separator + 1).trim());
    if (
      value.length >= 2 &&
      ((value.startsWith('"') && value.endsWith('"')) ||
        (value.startsWith("'") && value.endsWith("'")))
    ) {
      const quote = value[0];
      value = value.slice(1, -1);
      if (quote === '"') {
        value = value
          .replace(/\\n/g, "\n")
          .replace(/\\r/g, "\r")
          .replace(/\\t/g, "\t")
          .replace(/\\"/g, '"')
          .replace(/\\\\/g, "\\");
      }
    }
    result[name] = value;
  }
  return result;
}

function startupError(code, message) {
  const error = new Error(message);
  error.code = code;
  return error;
}

export function startupFailureMessage(error) {
  switch (error?.code) {
    case "LOCAL_RAG_MISSING_ENV_FILE":
      return "a required environment file is missing; copy its .env.example file and configure it";
    case "LOCAL_RAG_MISSING_ENV_VALUE":
      return "a required environment value is missing; complete the project .env files";
    case "LOCAL_RAG_PLACEHOLDER_ENV_VALUE":
      return "an environment value still contains an example placeholder; update the project .env files";
    case "LOCAL_RAG_PORTS_OCCUPIED":
      return "development listeners are already in use; stop the existing Local RAG instance before running pnpm dev";
    case "LOCAL_RAG_MANAGED_PRODUCTION_RUNNING":
      return "the managed RagSupervisor service is running; stop it before starting the development stack";
    case "LOCAL_RAG_UNKNOWN_OPTION":
      return "an unsupported command-line option was supplied; use pnpm dev or pnpm dev -- --check";
    default:
      return "startup failed. Review the component-prefixed diagnostics above";
  }
}

export function reportStartupFailure(error, write = console.error) {
  write(`[dev] ERROR: ${startupFailureMessage(error)}`);
}

export function readEnvFile(filePath) {
  if (!existsSync(filePath)) {
    throw startupError(
      "LOCAL_RAG_MISSING_ENV_FILE",
      `missing ${path.relative(repositoryRoot, filePath)}; copy its .env.example first`,
    );
  }
  return parseEnv(readFileSync(filePath, "utf8"));
}

function requireValue(environment, name) {
  const value = environment[name]?.trim();
  if (!value) {
    throw startupError("LOCAL_RAG_MISSING_ENV_VALUE", `${name} must be configured`);
  }
  if (/replace-with|replace_with|change-me/i.test(value)) {
    throw startupError(
      "LOCAL_RAG_PLACEHOLDER_ENV_VALUE",
      `${name} still contains an example placeholder`,
    );
  }
  return value;
}

function requireAbsolutePath(environment, name) {
  const value = requireValue(environment, name);
  if (!path.isAbsolute(value)) {
    throw new Error(`${name} must be an absolute path`);
  }
  return path.resolve(value);
}

function without(environment, names) {
  const result = { ...environment };
  for (const name of names) {
    delete result[name];
  }
  return result;
}

export function applyResolvedRerankerPath(configuration, resolvedPath) {
  configuration.rerankerPath = resolvedPath;
  configuration.environments.inference.RERANKER_MODEL_PATH = resolvedPath;
}

export function buildServiceEnvironments({
  inherited = {},
  rootFile = {},
  apiFile,
  webFile,
  runtimeRoot,
  coordinatorToken,
  ocrToken,
  rerankerPath,
  ocrModelPath,
}) {
  const rootConfigured = { ...rootFile, ...inherited };
  const configured = { ...apiFile, ...inherited };
  const common = without(configured, [
    ...privilegedVariables,
    "RERANKER_MODEL_PATH",
  ]);
  const nonApiCommon = without(common, apiOnlyVariables);
  const dataRoot = path.resolve(configured.DATA_ROOT);
  const cacheRoot = path.join(dataRoot, "cache");

  const api = {
    ...common,
    DATABASE_URL: configured.DATABASE_URL,
    COORDINATOR_BASE_URL: "http://127.0.0.1:8100",
    COORDINATOR_SERVICE_TOKEN: coordinatorToken,
    OCR_SERVICE_BASE_URL: "http://127.0.0.1:8101",
    OCR_SERVICE_TOKEN: ocrToken,
    RERANKER_MODEL: configured.RERANKER_MODEL,
    HF_HUB_OFFLINE: "1",
    TRANSFORMERS_OFFLINE: "1",
    TOKENIZERS_PARALLELISM: "false",
  };
  const worker = {
    ...nonApiCommon,
    WORKER_DATABASE_URL: configured.WORKER_DATABASE_URL,
    COORDINATOR_BASE_URL: "http://127.0.0.1:8100",
    COORDINATOR_SERVICE_TOKEN: coordinatorToken,
    OCR_SERVICE_BASE_URL: "http://127.0.0.1:8101",
    OCR_SERVICE_TOKEN: ocrToken,
    OBJECT_WORK_PATH: path.join(dataRoot, "object-work"),
    OCR_WORK_PATH: path.join(dataRoot, "ocr-work"),
    OBJECT_STORAGE_BLOCKING_CONCURRENCY: "4",
  };
  // Production receives a dedicated delete-only IAM identity from its protected
  // deletion.env. Development has no persisted scoped identities, so isolate the
  // already-required RustFS root credential to the deletion process only.
  const deletion = {
    ...nonApiCommon,
    WORKER_DATABASE_URL: configured.WORKER_DATABASE_URL,
    OBJECT_STORAGE_ACCESS_KEY_ID: rootConfigured.RUSTFS_ROOT_ACCESS_KEY,
    OBJECT_STORAGE_SECRET_ACCESS_KEY: rootConfigured.RUSTFS_ROOT_SECRET_KEY,
    OBJECT_STORAGE_BLOCKING_CONCURRENCY: "2",
  };
  const inference = {
    ...without(nonApiCommon, [
      "OBJECT_STORAGE_ACCESS_KEY_ID",
      "OBJECT_STORAGE_SECRET_ACCESS_KEY",
    ]),
    COORDINATOR_SERVICE_TOKEN: coordinatorToken,
    COORDINATOR_OWNERSHIP_PATH: path.join(runtimeRoot, "inference.owner"),
    RERANKER_MODEL_PATH: rerankerPath,
    HF_HOME: path.join(cacheRoot, "inference", "hf-home"),
    HF_HUB_CACHE: path.join(cacheRoot, "inference", "hf-hub"),
    TRANSFORMERS_CACHE: path.join(cacheRoot, "inference", "transformers"),
    XDG_CACHE_HOME: path.join(cacheRoot, "inference", "xdg"),
    HF_HUB_OFFLINE: "1",
    TRANSFORMERS_OFFLINE: "1",
    TOKENIZERS_PARALLELISM: "false",
  };
  const ocr = {
    ...without(nonApiCommon, [
      "OBJECT_STORAGE_ACCESS_KEY_ID",
      "OBJECT_STORAGE_SECRET_ACCESS_KEY",
    ]),
    OCR_SERVICE_TOKEN: ocrToken,
    OCR_OWNERSHIP_PATH: path.join(runtimeRoot, "ocr.owner"),
    OCR_WORKSPACE_ROOT: path.join(dataRoot, "ocr-work"),
    OCR_MODEL_ASSET_ROOT: ocrModelPath,
    PADDLE_HOME: ocrModelPath,
    HF_HOME: path.join(cacheRoot, "ocr", "hf-home"),
    HF_HUB_CACHE: path.join(cacheRoot, "ocr", "hf-hub"),
    TRANSFORMERS_CACHE: path.join(cacheRoot, "ocr", "transformers"),
    XDG_CACHE_HOME: path.join(cacheRoot, "ocr", "xdg"),
    HF_HUB_OFFLINE: "1",
    TRANSFORMERS_OFFLINE: "1",
  };
  const web = without({ ...webFile, ...inherited }, [
    ...privilegedVariables,
    ...apiOnlyVariables,
    "RERANKER_MODEL_PATH",
    "OBJECT_STORAGE_ACCESS_KEY_ID",
    "OBJECT_STORAGE_SECRET_ACCESS_KEY",
    "COORDINATOR_SERVICE_TOKEN",
    "OCR_SERVICE_TOKEN",
  ]);
  const maintenance = {
    ...nonApiCommon,
    MAINTENANCE_DATABASE_URL: configured.MAINTENANCE_DATABASE_URL,
  };
  const migration = {
    ...without(nonApiCommon, [
      "OBJECT_STORAGE_ACCESS_KEY_ID",
      "OBJECT_STORAGE_SECRET_ACCESS_KEY",
    ]),
    MIGRATION_DATABASE_URL: configured.MIGRATION_DATABASE_URL,
  };
  return {
    api,
    worker,
    deletion,
    inference,
    ocr,
    web,
    maintenance,
    migration,
  };
}

export function resolveConfiguration(inherited = process.env) {
  const rootFile = readEnvFile(path.join(repositoryRoot, ".env"));
  const apiFile = readEnvFile(path.join(repositoryRoot, "apps", "api", ".env"));
  const webFile = readEnvFile(
    path.join(repositoryRoot, "apps", "web", ".env.local"),
  );
  const root = { ...rootFile, ...inherited };
  const configuredApi = { ...apiFile, ...inherited };

  for (const name of [
    "POSTGRES_DB",
    "POSTGRES_CLUSTER_ADMIN_PASSWORD",
    "RUSTFS_ROOT_ACCESS_KEY",
    "RUSTFS_ROOT_SECRET_KEY",
  ]) {
    requireValue(root, name);
  }
  for (const name of [
    "DATABASE_URL",
    "WORKER_DATABASE_URL",
    "MAINTENANCE_DATABASE_URL",
    "MIGRATION_DATABASE_URL",
    "DATA_ROOT",
    "OCR_PYTHON_EXECUTABLE",
    "RERANKER_MODEL",
    "OBJECT_STORAGE_ACCESS_KEY_ID",
    "OBJECT_STORAGE_SECRET_ACCESS_KEY",
  ]) {
    requireValue(configuredApi, name);
  }
  const dataRoot = requireAbsolutePath(configuredApi, "DATA_ROOT");
  const ocrPython = requireAbsolutePath(configuredApi, "OCR_PYTHON_EXECUTABLE");
  try {
    accessSync(ocrPython, constants.X_OK);
  } catch {
    throw new Error(`OCR_PYTHON_EXECUTABLE does not exist: ${ocrPython}`);
  }

  const runtimeRoot = path.join(repositoryRoot, "runtime", "dev");
  const rerankerPath = path.resolve(
    configuredApi.RAG_DEV_RERANKER_PATH ||
      path.join(dataRoot, "models", "bge-reranker-v2-m3"),
  );
  const standardPaddleCache = path.join(os.homedir(), ".paddlex");
  const ocrModelPath = path.resolve(
    configuredApi.OCR_MODEL_ASSET_ROOT ||
      configuredApi.PADDLE_HOME ||
      (existsSync(standardPaddleCache) ? standardPaddleCache : undefined) ||
      path.join(dataRoot, "models", "paddleocr-vl-1.6"),
  );
  const coordinatorToken = randomBytes(32).toString("hex");
  const ocrToken = randomBytes(32).toString("hex");
  const environments = buildServiceEnvironments({
    inherited,
    rootFile,
    apiFile,
    webFile,
    runtimeRoot,
    coordinatorToken,
    ocrToken,
    rerankerPath,
    ocrModelPath,
  });
  return {
    root,
    apiConfigured: configuredApi,
    runtimeRoot,
    dataRoot,
    ocrPython,
    rerankerPath,
    ocrModelPath,
    coordinatorToken,
    ocrToken,
    environments,
  };
}

function prefixStream(stream, label, target) {
  const lines = createInterface({ input: stream, crlfDelay: Infinity });
  lines.on("line", (line) => target.write(`[${label}] ${line}\n`));
}

function runCommand(command, args, options = {}) {
  const label = options.label ?? path.basename(command);
  if (stopRequested && !options.allowAfterStop) {
    return Promise.reject(new Error(`${label} cancelled by shutdown`));
  }
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, {
      cwd: options.cwd ?? repositoryRoot,
      env: options.env ?? process.env,
      windowsHide: true,
      stdio: ["ignore", "pipe", "pipe"],
    });
    activeOneShotChildren.add(child);
    prefixStream(child.stdout, label, process.stdout);
    prefixStream(child.stderr, label, process.stderr);
    child.once("error", (error) => {
      activeOneShotChildren.delete(child);
      reject(new Error(`${label} could not start: ${error.message}`));
    });
    child.once("exit", (code, signal) => {
      activeOneShotChildren.delete(child);
      if (code === 0) {
        resolve();
      } else {
        reject(
          new Error(
            `${label} failed${code === null ? ` with ${signal}` : ` with exit code ${code}`}`,
          ),
        );
      }
    });
  });
}

async function commandSucceeds(command, args, options = {}) {
  try {
    await new Promise((resolve, reject) => {
      const child = spawn(command, args, {
        cwd: options.cwd ?? repositoryRoot,
        env: options.env ?? process.env,
        windowsHide: true,
        stdio: "ignore",
      });
      child.once("error", reject);
      child.once("exit", (code) =>
        code === 0 ? resolve() : reject(new Error(`exit ${code}`)),
      );
    });
    return true;
  } catch {
    return false;
  }
}

function delay(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

function probeAvailableListener({ host, port }) {
  return new Promise((resolve, reject) => {
    const server = createServer();
    server.unref();
    server.once("error", reject);
    server.listen({ host, port, exclusive: true }, () => {
      server.close((error) => (error ? reject(error) : resolve()));
    });
  });
}

export async function assertDevelopmentPortsAvailable(
  listeners = developmentListenerSpecs,
) {
  const conflicts = [];
  for (const listener of listeners) {
    try {
      await probeAvailableListener(listener);
    } catch (error) {
      if (error?.code === "EADDRINUSE" || error?.code === "EACCES") {
        conflicts.push(listener);
        continue;
      }
      throw new Error(
        `could not check development listener ${listener.host}:${listener.port}: ${error.message}`,
        { cause: error },
      );
    }
  }
  if (conflicts.length > 0) {
    const occupied = conflicts
      .map(({ name, host, port }) => `${name} (${host}:${port})`)
      .join(", ");
    throw startupError(
      "LOCAL_RAG_PORTS_OCCUPIED",
      `development listeners are already in use: ${occupied}. ` +
        "Production and development processes cannot share these fixed listeners. " +
        "Stop the existing Local RAG instance (including RagSupervisor when production is running) before running pnpm dev.",
    );
  }
}

function queryWindowsService(serviceName) {
  return new Promise((resolve, reject) => {
    const child = spawn("sc.exe", ["query", serviceName], {
      cwd: repositoryRoot,
      env: process.env,
      windowsHide: true,
      stdio: ["ignore", "pipe", "pipe"],
    });
    let stdout = "";
    let stderr = "";
    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", (chunk) => {
      stdout += chunk;
    });
    child.stderr.on("data", (chunk) => {
      stderr += chunk;
    });
    child.once("error", reject);
    child.once("exit", (code) => resolve({ code, stdout, stderr }));
  });
}

export async function assertManagedProductionStopped({
  platform = process.platform,
  queryService = queryWindowsService,
} = {}) {
  if (platform !== "win32") {
    return;
  }
  const result = await queryService("RagSupervisor");
  if (result.code === 1060) {
    return;
  }
  if (result.code !== 0) {
    const detail = (result.stderr || result.stdout).trim();
    throw new Error(
      `could not determine RagSupervisor state (sc.exe exit ${result.code})${
        detail ? `: ${detail}` : ""
      }`,
    );
  }
  const match = result.stdout.match(
    /^\s*STATE\s*:\s*\d+\s+([A-Z_]+)\s*$/im,
  );
  if (!match) {
    throw new Error("could not determine RagSupervisor state from sc.exe output");
  }
  const state = match[1].toUpperCase();
  if (state !== "STOPPED") {
    throw startupError(
      "LOCAL_RAG_MANAGED_PRODUCTION_RUNNING",
      `managed production service RagSupervisor is ${state}. ` +
        "Development must run separately from the managed production graph. " +
        "From an elevated PowerShell, run Stop-Service -Name RagSupervisor and verify it is stopped before running pnpm dev; the development launcher will not stop production automatically.",
    );
  }
}

async function waitFor(check, description, timeoutMilliseconds = 120_000) {
  const deadline = Date.now() + timeoutMilliseconds;
  let lastError;
  while (Date.now() < deadline) {
    if (stopRequested) {
      throw new Error(`cancelled while waiting for ${description}`);
    }
    try {
      if (await check()) {
        return;
      }
    } catch (error) {
      lastError = error;
    }
    await delay(1_000);
  }
  const detail = lastError ? `: ${lastError.message}` : "";
  throw new Error(`timed out waiting for ${description}${detail}`);
}

async function ensureDocker(configuration) {
  const composeEnvironment = configuration.root;
  if (
    !(await commandSucceeds(commandNames.docker, ["info"], {
      env: composeEnvironment,
    }))
  ) {
    if (!isWindows) {
      throw new Error("Docker daemon is not running");
    }
    const desktop = path.join(
      process.env.ProgramFiles ?? "C:\\Program Files",
      "Docker",
      "Docker",
      "Docker Desktop.exe",
    );
    if (!existsSync(desktop)) {
      throw new Error("Docker Desktop is not installed or could not be found");
    }
    console.log("[dev] Starting Docker Desktop...");
    const child = spawn(desktop, [], {
      detached: true,
      stdio: "ignore",
      windowsHide: true,
    });
    child.unref();
    await waitFor(
      () =>
        commandSucceeds(commandNames.docker, ["info"], {
          env: composeEnvironment,
        }),
      "Docker Desktop",
      180_000,
    );
  }
  await runCommand(
    commandNames.docker,
    ["compose", "up", "-d", "--wait", "postgres", "rustfs"],
    { label: "docker", env: composeEnvironment },
  );
}

async function ollamaAvailable(baseUrl) {
  const response = await fetch(`${baseUrl}/api/tags`, {
    signal: timeoutSignal(3_000),
  });
  return response.ok;
}

async function ensureOllama(configuration, supervisor) {
  const baseUrl = (
    configuration.apiConfigured.OLLAMA_BASE_URL ?? "http://127.0.0.1:11434"
  ).replace(/\/$/, "");
  try {
    if (await ollamaAvailable(baseUrl)) {
      return baseUrl;
    }
  } catch {
    // Start the local server below.
  }
  console.log("[dev] Starting Ollama...");
  supervisor.startDependency("ollama", commandNames.ollama, ["serve"], {
    env: configuration.environments.inference,
  });
  await waitFor(() => ollamaAvailable(baseUrl), "Ollama", 90_000);
  return baseUrl;
}

async function ensureOllamaModels(configuration, baseUrl) {
  const response = await fetch(`${baseUrl}/api/tags`, {
    signal: timeoutSignal(5_000),
  });
  if (!response.ok) {
    throw new Error(`Ollama model list returned HTTP ${response.status}`);
  }
  const payload = await response.json();
  const installed = new Set(
    (payload.models ?? []).flatMap((model) => [model.name, model.model]),
  );
  for (const model of [
    configuration.apiConfigured.GENERATION_MODEL ?? "qwen3:8b",
    configuration.apiConfigured.EMBEDDING_MODEL ?? "qwen3-embedding:0.6b",
  ]) {
    if (!installed.has(model)) {
      await runCommand(commandNames.ollama, ["pull", model], {
        label: `ollama:${model}`,
        env: configuration.environments.inference,
      });
    }
  }

  const embeddingModel =
    configuration.apiConfigured.EMBEDDING_MODEL ?? "qwen3-embedding:0.6b";
  const embedResponse = await fetch(`${baseUrl}/api/embed`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      model: embeddingModel,
      input: "Local RAG development startup check.",
    }),
    signal: timeoutSignal(120_000),
  });
  if (!embedResponse.ok) {
    throw new Error(`Ollama embedding check returned HTTP ${embedResponse.status}`);
  }
  const embedPayload = await embedResponse.json();
  const dimension = embedPayload.embeddings?.[0]?.length;
  if (dimension !== 1024) {
    throw new Error(`expected a 1024-dimensional embedding, received ${dimension}`);
  }
}

function rerankerPrepared(modelPath) {
  if (!existsSync(path.join(modelPath, "config.json"))) {
    return false;
  }
  if (!existsSync(path.join(modelPath, "tokenizer_config.json"))) {
    return false;
  }
  try {
    return readdirSync(modelPath, { withFileTypes: true }).some((entry) =>
      entry.name.endsWith(".safetensors"),
    );
  } catch {
    return false;
  }
}

async function ensureReranker(configuration) {
  if (rerankerPrepared(configuration.rerankerPath)) {
    console.log("[dev] Reranker ready.");
    return;
  }
  const resultFile = path.join(configuration.runtimeRoot, "reranker-path.txt");
  const preparationEnvironment = without(configuration.apiConfigured, [
    ...privilegedVariables,
    "OBJECT_STORAGE_ACCESS_KEY_ID",
    "OBJECT_STORAGE_SECRET_ACCESS_KEY",
    "COORDINATOR_SERVICE_TOKEN",
    "OCR_SERVICE_TOKEN",
    "RERANKER_MODEL_PATH",
    "HF_HUB_OFFLINE",
    "TRANSFORMERS_OFFLINE",
  ]);
  await runCommand(
    commandNames.uv,
    [
      "--directory",
      "apps/api",
      "run",
      "python",
      path.join(repositoryRoot, "scripts", "prepare-dev-reranker.py"),
      "--model",
      requireValue(configuration.apiConfigured, "RERANKER_MODEL"),
      "--preferred-path",
      configuration.rerankerPath,
      "--result-file",
      resultFile,
    ],
    { label: "reranker", env: preparationEnvironment },
  );
  const resolvedPath = readFileSync(resultFile, "utf8").trim();
  if (!path.isAbsolute(resolvedPath) || !rerankerPrepared(resolvedPath)) {
    throw new Error("reranker preparation did not return a complete absolute path");
  }
  applyResolvedRerankerPath(configuration, resolvedPath);
}

async function prepareInfrastructure(configuration) {
  await runCommand(
    commandNames.uv,
    [
      "--directory",
      "apps/api",
      "run",
      "python",
      "-m",
      "app.maintenance_cli",
      "storage-bootstrap",
    ],
    { label: "storage", env: configuration.environments.maintenance },
  );
  await runCommand(
    commandNames.uv,
    ["--directory", "apps/api", "run", "alembic", "upgrade", "head"],
    { label: "database", env: configuration.environments.migration },
  );
}

async function checkOcr(configuration) {
  await runCommand(
    configuration.ocrPython,
    [
      "-c",
      "import paddleocr; print('PaddleOCR environment ready; model assets load on the first scanned document')",
    ],
    { label: "ocr-check", env: configuration.environments.ocr },
  );
}

async function validateBackendEnvironments(configuration) {
  const checks = [
    {
      name: "api-config",
      environment: configuration.environments.api,
      expression:
        "from app.config import Settings; Settings(); print('API configuration valid')",
    },
    {
      name: "worker-config",
      environment: configuration.environments.worker,
      expression:
        "from app.processes.settings import ObjectWorkerSettings, WorkerProcessSettings; ObjectWorkerSettings(); WorkerProcessSettings(); print('worker configuration valid')",
    },
    {
      name: "deletion-config",
      environment: configuration.environments.deletion,
      expression:
        "from app.processes.settings import ObjectWorkerSettings; ObjectWorkerSettings(); print('deletion configuration valid')",
    },
    {
      name: "inference-config",
      environment: configuration.environments.inference,
      expression:
        "from app.processes.settings import CoordinatorProcessSettings; CoordinatorProcessSettings(); print('inference configuration valid')",
    },
    {
      name: "ocr-config",
      environment: configuration.environments.ocr,
      expression:
        "from app.processes.settings import OcrProcessSettings; OcrProcessSettings(); print('OCR configuration valid')",
    },
  ];
  for (const check of checks) {
    await runCommand(
      commandNames.uv,
      [
        "--directory",
        "apps/api",
        "run",
        "python",
        "-c",
        check.expression,
      ],
      { label: check.name, env: check.environment },
    );
  }
}

export function serviceSpecs(configuration) {
  const uv = commandNames.uv;
  const uvPrefix = ["--directory", "apps/api", "run", "python", "-m"];
  return [
    {
      name: "inference",
      command: uv,
      args: [...uvPrefix, "app.coordinator_server"],
      env: configuration.environments.inference,
    },
    {
      name: "ocr",
      command: uv,
      args: [...uvPrefix, "app.ocr_service_server"],
      env: configuration.environments.ocr,
    },
    {
      name: "ingestion",
      command: uv,
      args: [...uvPrefix, "app.processes.ingestion_worker"],
      env: configuration.environments.worker,
    },
    {
      name: "deletion",
      command: uv,
      args: [...uvPrefix, "app.processes.deletion_worker"],
      env: configuration.environments.deletion,
    },
    {
      name: "api",
      command: uv,
      args: [...uvPrefix, "app.dev_server", "--reload"],
      env: configuration.environments.api,
    },
    {
      name: "web",
      command: isWindows ? (process.env.ComSpec ?? "cmd.exe") : commandNames.pnpm,
      args: isWindows
        ? ["/d", "/s", "/c", "pnpm.cmd --dir apps/web dev"]
        : ["--dir", "apps/web", "dev"],
      env: configuration.environments.web,
    },
  ];
}

class Supervisor {
  constructor() {
    this.children = [];
    this.shuttingDown = false;
    this.failure = new Promise((_, reject) => {
      this.rejectFailure = reject;
    });
    this.failure.catch(() => {});
  }

  start(name, command, args, options = {}) {
    const child = spawn(command, args, {
      cwd: repositoryRoot,
      env: options.env ?? process.env,
      windowsHide: true,
      detached: !isWindows,
      stdio: ["ignore", "pipe", "pipe"],
    });
    prefixStream(child.stdout, name, process.stdout);
    prefixStream(child.stderr, name, process.stderr);
    this.children.push({ name, child, dependency: options.dependency ?? false });
    child.once("error", (error) => {
      if (!this.shuttingDown) {
        this.rejectFailure(new Error(`${name} could not start: ${error.message}`));
      }
    });
    child.once("exit", (code, signal) => {
      if (!this.shuttingDown) {
        this.rejectFailure(
          new Error(
            `${name} stopped unexpectedly${
              code === null ? ` with ${signal}` : ` with exit code ${code}`
            }`,
          ),
        );
      }
    });
    return child;
  }

  startDependency(name, command, args, options = {}) {
    return this.start(name, command, args, { ...options, dependency: true });
  }

  async stopProcess(child) {
    if (child.exitCode !== null || child.signalCode !== null) {
      return;
    }
    await terminateProcessTree(child);
  }

  stop() {
    if (this.stopPromise) {
      return this.stopPromise;
    }
    this.shuttingDown = true;
    this.stopPromise = Promise.allSettled(
      [...this.children].reverse().map(({ child }) => this.stopProcess(child)),
    );
    return this.stopPromise;
  }
}

async function terminateProcessTree(child) {
  if (!child.pid || child.exitCode !== null || child.signalCode !== null) {
    return;
  }
  if (isWindows) {
    await commandSucceeds("taskkill.exe", [
      "/PID",
      String(child.pid),
      "/T",
      "/F",
    ]);
    return;
  }
  try {
    process.kill(-child.pid, "SIGTERM");
  } catch {
    // The process already exited.
  }
}

async function stopActiveOneShotCommands() {
  await Promise.allSettled(
    [...activeOneShotChildren].map((child) => terminateProcessTree(child)),
  );
}

async function endpointReady(url, token) {
  const headers = token ? { authorization: `Bearer ${token}` } : {};
  const response = await fetch(url, {
    headers,
    redirect: "manual",
    signal: timeoutSignal(3_000),
  });
  return response.ok || (response.status >= 300 && response.status < 400);
}

async function waitForChecks(checks, supervisor) {
  for (const [check, description] of checks) {
    await Promise.race([
      waitFor(check, description, description === "web application" ? 120_000 : 90_000),
      supervisor.failure,
    ]);
    console.log(`[dev] ${description} ready`);
  }
}

async function waitForInternalServices(configuration, supervisor) {
  await waitForChecks([
    [
      () =>
        endpointReady(
          "http://127.0.0.1:8100/health",
          configuration.coordinatorToken,
        ),
      "inference coordinator",
    ],
    [
      () =>
        endpointReady("http://127.0.0.1:8101/health", configuration.ocrToken),
      "OCR service",
    ],
  ], supervisor);
}

async function waitForUserServices(supervisor) {
  await waitForChecks([
    [() => endpointReady("http://127.0.0.1:8000/health"), "API"],
    [() => endpointReady("http://127.0.0.1:3000"), "web application"],
  ], supervisor);
}

async function stopCompose(configuration) {
  if (/^(1|true|yes)$/i.test(configuration.root.RAG_DEV_KEEP_DEPENDENCIES ?? "")) {
    console.log("[dev] Leaving Docker services running (RAG_DEV_KEEP_DEPENDENCIES=1)");
    return;
  }
  await runCommand(
    commandNames.docker,
    ["compose", "stop", "postgres", "rustfs"],
    {
      label: "docker",
      env: configuration.root,
      allowAfterStop: true,
    },
  ).catch(() => console.error("[dev] Docker shutdown did not complete cleanly."));
}

export async function main(args = process.argv.slice(2)) {
  stopRequested = false;
  shutdownController = new AbortController();
  if (!args.includes("--check")) {
    await assertManagedProductionStopped();
  }
  const configuration = resolveConfiguration();
  mkdirSync(configuration.runtimeRoot, { recursive: true });
  for (const directory of [
    configuration.ocrModelPath,
    path.join(configuration.dataRoot, "cache", "inference"),
    path.join(configuration.dataRoot, "cache", "ocr"),
    path.join(configuration.dataRoot, "ocr-work"),
    path.join(configuration.dataRoot, "object-work"),
  ]) {
    mkdirSync(directory, { recursive: true });
  }

  console.log("[dev] Validating process configuration...");
  await validateBackendEnvironments(configuration);
  if (args.includes("--check")) {
    console.log("[dev] Configuration is complete and all required paths are accessible.");
    console.log("[dev] Run pnpm dev to start Docker, models, backend, workers, and web.");
    return;
  }
  if (args.length > 0) {
    throw startupError("LOCAL_RAG_UNKNOWN_OPTION", `unknown option: ${args[0]}`);
  }
  await assertDevelopmentPortsAvailable();

  const supervisor = new Supervisor();
  let composeStarted = false;
  let signalResolve;
  const signalReceived = new Promise((resolve) => {
    signalResolve = resolve;
  });
  const onSignal = (signal) => {
    console.log(`\n[dev] ${signal} received; stopping the development system...`);
    stopRequested = true;
    shutdownController.abort();
    signalResolve();
    void supervisor.stop();
    void stopActiveOneShotCommands();
  };
  process.once("SIGINT", () => onSignal("SIGINT"));
  process.once("SIGTERM", () => onSignal("SIGTERM"));

  try {
    console.log("[dev] Preparing Docker services...");
    await ensureDocker(configuration);
    composeStarted = true;
    console.log("[dev] Preparing Ollama and required models...");
    const ollamaBaseUrl = await ensureOllama(configuration, supervisor);
    await ensureOllamaModels(configuration, ollamaBaseUrl);
    console.log("[dev] Preparing the CPU reranker...");
    await ensureReranker(configuration);
    console.log("[dev] Checking the isolated OCR environment...");
    await checkOcr(configuration);
    console.log("[dev] Preparing object storage and database schema...");
    await prepareInfrastructure(configuration);

    const specs = serviceSpecs(configuration);
    for (const spec of specs.slice(0, 2)) {
      supervisor.start(spec.name, spec.command, spec.args, { env: spec.env });
    }
    await waitForInternalServices(configuration, supervisor);
    for (const spec of specs.slice(2)) {
      supervisor.start(spec.name, spec.command, spec.args, { env: spec.env });
    }
    await waitForUserServices(supervisor);
    console.log("");
    console.log("[dev] Local RAG is running:");
    console.log("[dev]   Web: http://localhost:3000");
    console.log("[dev]   API: http://localhost:8000");
    console.log("[dev] Press Ctrl+C once to stop all supervised processes.");
    await Promise.race([signalReceived, supervisor.failure]);
  } finally {
    await supervisor.stop();
    if (composeStarted) {
      await stopCompose(configuration);
    }
  }
}

const invokedDirectly =
  process.argv[1] &&
  pathToFileURL(path.resolve(process.argv[1])).href === import.meta.url;
if (invokedDirectly) {
  main().catch((error) => {
    reportStartupFailure(error);
    process.exitCode = 1;
  });
}
