import assert from "node:assert/strict";
import { createServer } from "node:net";
import path from "node:path";
import test from "node:test";

import {
  assertDevelopmentPortsAvailable,
  assertManagedProductionStopped,
  applyResolvedRerankerPath,
  buildServiceEnvironments,
  developmentListenerSpecs,
  parseEnv,
  reportStartupFailure,
  serviceSpecs,
  startupFailureMessage,
} from "./dev-system.mjs";

test("parseEnv supports comments, export, quotes, and Windows paths", () => {
  assert.deepEqual(
    parseEnv(`
# comment
export SIMPLE=value
QUOTED="hello world"
WINDOWS=C:\\local-rag\\data
INLINE=value # comment
HASH="value # retained"
`),
    {
      SIMPLE: "value",
      QUOTED: "hello world",
      WINDOWS: "C:\\local-rag\\data",
      INLINE: "value",
      HASH: "value # retained",
    },
  );
});

test("parseEnv rejects malformed names", () => {
  assert.throws(() => parseEnv("NOT-VALID=value"), /invalid environment/);
});

test("startup failures preserve safe guidance without logging tainted details", () => {
  const missing = Object.assign(new Error("C:\\private\\secret.env"), {
    code: "LOCAL_RAG_MISSING_ENV_FILE",
  });
  assert.match(startupFailureMessage(missing), /environment file is missing/);

  const occupied = Object.assign(new Error("attacker\r\ninjected"), {
    code: "LOCAL_RAG_PORTS_OCCUPIED",
  });
  const output = [];
  reportStartupFailure(occupied, (line) => output.push(line));
  assert.deepEqual(output, [
    "[dev] ERROR: development listeners are already in use; stop the existing Local RAG instance before running pnpm dev",
  ]);
  assert.doesNotMatch(output[0], /attacker|injected|[\r\n]/);
});

test("development listener preflight reports occupied ports and accepts released ports", async () => {
  const blocker = createServer();
  await new Promise((resolve, reject) => {
    blocker.once("error", reject);
    blocker.listen({ host: "127.0.0.1", port: 0, exclusive: true }, resolve);
  });
  const address = blocker.address();
  assert.ok(address && typeof address === "object");
  const listener = {
    name: "test service",
    host: "127.0.0.1",
    port: address.port,
  };

  await assert.rejects(
    assertDevelopmentPortsAvailable([listener]),
    new RegExp(
      `development listeners are already in use: test service \\(127\\.0\\.0\\.1:${address.port}\\)`,
    ),
  );

  await new Promise((resolve, reject) => {
    blocker.close((error) => (error ? reject(error) : resolve()));
  });
  await assertDevelopmentPortsAvailable([listener]);
});

test("development listener preflight covers every fixed dev service port", () => {
  assert.deepEqual(developmentListenerSpecs, [
    { name: "web", host: "127.0.0.1", port: 3000 },
    { name: "API", host: "127.0.0.1", port: 8000 },
    { name: "inference coordinator", host: "127.0.0.1", port: 8100 },
    { name: "OCR service", host: "127.0.0.1", port: 8101 },
  ]);
});

test("managed production preflight rejects a running Windows service", async () => {
  await assert.rejects(
    assertManagedProductionStopped({
      platform: "win32",
      queryService: async () => ({
        code: 0,
        stdout: `
SERVICE_NAME: RagSupervisor
        STATE              : 4  RUNNING
`,
        stderr: "",
      }),
    }),
    /RagSupervisor is RUNNING.*Stop-Service -Name RagSupervisor/,
  );
});

test("managed production preflight accepts a stopped or absent service", async () => {
  await assertManagedProductionStopped({
    platform: "win32",
    queryService: async () => ({
      code: 0,
      stdout: `
SERVICE_NAME: RagSupervisor
        STATE              : 1  STOPPED
`,
      stderr: "",
    }),
  });
  await assertManagedProductionStopped({
    platform: "win32",
    queryService: async () => ({ code: 1060, stdout: "", stderr: "" }),
  });
});

test("managed production preflight does not query services off Windows", async () => {
  let queried = false;
  await assertManagedProductionStopped({
    platform: "linux",
    queryService: async () => {
      queried = true;
      throw new Error("must not be called");
    },
  });
  assert.equal(queried, false);
});

test("managed production preflight fails closed when service state is unknown", async () => {
  await assert.rejects(
    assertManagedProductionStopped({
      platform: "win32",
      queryService: async () => ({
        code: 5,
        stdout: "",
        stderr: "Access is denied.",
      }),
    }),
    /could not determine RagSupervisor state.*Access is denied/,
  );
});

test("service environments isolate database credentials and service secrets", () => {
  const runtimeRoot = path.resolve("runtime-test");
  const dataRoot = path.resolve("data-test");
  const environments = buildServiceEnvironments({
    inherited: {
      RERANKER_MODEL_PATH: path.join(dataRoot, "models", "inherited-reranker"),
    },
    rootFile: {
      RUSTFS_ROOT_ACCESS_KEY: "root-access",
      RUSTFS_ROOT_SECRET_KEY: "root-secret",
    },
    apiFile: {
      DATA_ROOT: dataRoot,
      RERANKER_MODEL: "BAAI/bge-reranker-v2-m3",
      RERANKER_MODEL_PATH: path.join(dataRoot, "models", "api-reranker"),
      DATABASE_URL: "api-url",
      WORKER_DATABASE_URL: "worker-url",
      MAINTENANCE_DATABASE_URL: "maintenance-url",
      MIGRATION_DATABASE_URL: "migration-url",
      OBJECT_STORAGE_ACCESS_KEY_ID: "access",
      OBJECT_STORAGE_SECRET_ACCESS_KEY: "secret",
      OBJECT_STORAGE_BLOCKING_CONCURRENCY: "8",
      ENABLE_V6_ADAPTIVE_PARSING: "true",
      POSTGRES_CLUSTER_ADMIN_PASSWORD: "cluster-admin",
    },
    webFile: { INTERNAL_API_URL: "http://127.0.0.1:8000" },
    runtimeRoot,
    coordinatorToken: "coordinator-token",
    ocrToken: "ocr-token",
    rerankerPath: path.join(dataRoot, "models", "reranker"),
    ocrModelPath: path.join(dataRoot, "models", "ocr"),
  });

  assert.equal(environments.api.DATABASE_URL, "api-url");
  assert.equal(environments.api.RERANKER_MODEL, "BAAI/bge-reranker-v2-m3");
  assert.equal(
    environments.inference.RERANKER_MODEL_PATH,
    path.join(dataRoot, "models", "reranker"),
  );
  for (const [name, environment] of Object.entries(environments)) {
    if (name !== "inference") {
      assert.equal(environment.RERANKER_MODEL_PATH, undefined, name);
    }
  }
  assert.equal(environments.api.WORKER_DATABASE_URL, undefined);
  assert.equal(environments.worker.WORKER_DATABASE_URL, "worker-url");
  assert.equal(environments.worker.DATABASE_URL, undefined);
  assert.equal(environments.inference.DATABASE_URL, undefined);
  assert.equal(environments.inference.OBJECT_STORAGE_ACCESS_KEY_ID, undefined);
  assert.equal(environments.ocr.OBJECT_STORAGE_SECRET_ACCESS_KEY, undefined);
  assert.equal(environments.api.POSTGRES_CLUSTER_ADMIN_PASSWORD, undefined);
  assert.equal(environments.worker.RUSTFS_ROOT_SECRET_KEY, undefined);
  assert.equal(
    environments.deletion.OBJECT_STORAGE_ACCESS_KEY_ID,
    "root-access",
  );
  assert.equal(
    environments.deletion.OBJECT_STORAGE_SECRET_ACCESS_KEY,
    "root-secret",
  );
  assert.equal(environments.deletion.RUSTFS_ROOT_ACCESS_KEY, undefined);
  assert.equal(environments.deletion.RUSTFS_ROOT_SECRET_KEY, undefined);
  assert.equal(environments.deletion.COORDINATOR_SERVICE_TOKEN, undefined);
  assert.equal(environments.deletion.OCR_SERVICE_TOKEN, undefined);
  assert.equal(environments.deletion.WORKER_DATABASE_URL, "worker-url");
  assert.equal(environments.web.DATABASE_URL, undefined);
  assert.equal(environments.web.OBJECT_STORAGE_ACCESS_KEY_ID, undefined);
  assert.equal(environments.maintenance.MAINTENANCE_DATABASE_URL, "maintenance-url");
  assert.equal(environments.maintenance.MIGRATION_DATABASE_URL, undefined);
  assert.equal(environments.migration.MIGRATION_DATABASE_URL, "migration-url");
  assert.equal(environments.migration.MAINTENANCE_DATABASE_URL, undefined);
  assert.equal(environments.worker.COORDINATOR_SERVICE_TOKEN, "coordinator-token");
  assert.equal(environments.worker.OCR_SERVICE_TOKEN, "ocr-token");
  assert.equal(environments.api.OCR_SERVICE_BASE_URL, "http://127.0.0.1:8101");
  assert.equal(environments.api.OCR_SERVICE_TOKEN, "ocr-token");
  assert.equal(environments.inference.OCR_SERVICE_TOKEN, undefined);
  assert.equal(environments.web.OCR_SERVICE_TOKEN, undefined);
  assert.equal(environments.maintenance.OCR_SERVICE_TOKEN, undefined);
  assert.equal(environments.migration.OCR_SERVICE_TOKEN, undefined);
  assert.equal(environments.api.OBJECT_STORAGE_BLOCKING_CONCURRENCY, "8");
  assert.equal(environments.worker.OBJECT_STORAGE_BLOCKING_CONCURRENCY, "4");
  assert.equal(environments.api.ENABLE_V6_ADAPTIVE_PARSING, "true");
  assert.equal(environments.worker.ENABLE_V6_ADAPTIVE_PARSING, undefined);
  assert.equal(environments.deletion.ENABLE_V6_ADAPTIVE_PARSING, undefined);
  assert.equal(environments.inference.ENABLE_V6_ADAPTIVE_PARSING, undefined);
  assert.equal(environments.ocr.ENABLE_V6_ADAPTIVE_PARSING, undefined);
  assert.equal(environments.web.ENABLE_V6_ADAPTIVE_PARSING, undefined);
  assert.equal(environments.maintenance.ENABLE_V6_ADAPTIVE_PARSING, undefined);
  assert.equal(environments.migration.ENABLE_V6_ADAPTIVE_PARSING, undefined);
  assert.equal(environments.deletion.OBJECT_STORAGE_BLOCKING_CONCURRENCY, "2");
  assert.equal(
    environments.web.INTERNAL_API_URL,
    "http://127.0.0.1:8000",
  );
});

test("resolved reranker snapshots update inference without changing API identity", () => {
  const identity = "BAAI/bge-reranker-v2-m3";
  const configuration = {
    rerankerPath: path.resolve("data-test", "models", "initial-reranker"),
    environments: {
      api: { RERANKER_MODEL: identity },
      inference: {},
    },
  };
  const resolvedPath = path.resolve(
    "data-test",
    "models",
    "snapshots",
    "resolved-reranker",
  );

  applyResolvedRerankerPath(configuration, resolvedPath);

  assert.equal(configuration.rerankerPath, resolvedPath);
  assert.equal(
    configuration.environments.inference.RERANKER_MODEL_PATH,
    resolvedPath,
  );
  assert.equal(configuration.environments.api.RERANKER_MODEL, identity);
  assert.equal(configuration.environments.api.RERANKER_MODEL_PATH, undefined);
});

test("Windows web process launches pnpm through cmd.exe", () => {
  const environments = {
    inference: {},
    ocr: {},
    worker: {},
    deletion: {},
    api: {},
    web: {},
  };
  const web = serviceSpecs({ environments }).find(
    (service) => service.name === "web",
  );
  assert.ok(web);
  if (process.platform === "win32") {
    assert.match(web.command.toLowerCase(), /cmd\.exe$/);
    assert.deepEqual(web.args, [
      "/d",
      "/s",
      "/c",
      "pnpm.cmd --dir apps/web dev",
    ]);
  } else {
    assert.equal(web.command, "pnpm");
  }
});

test("deletion process does not reuse the ingestion object-store identity", () => {
  const worker = { identity: "ingestion" };
  const deletion = { identity: "deletion" };
  const specs = serviceSpecs({
    environments: {
      inference: {},
      ocr: {},
      worker,
      deletion,
      api: {},
      web: {},
    },
  });

  assert.equal(
    specs.find((service) => service.name === "ingestion")?.env,
    worker,
  );
  assert.equal(
    specs.find((service) => service.name === "deletion")?.env,
    deletion,
  );
});
