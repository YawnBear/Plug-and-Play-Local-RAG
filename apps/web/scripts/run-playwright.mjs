import { spawn } from "node:child_process";
import http from "node:http";
import { fileURLToPath } from "node:url";
import path from "node:path";

const webRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const nextCli = path.join(webRoot, "node_modules", "next", "dist", "bin", "next");
const playwrightCli = path.join(
  webRoot,
  "node_modules",
  "@playwright",
  "test",
  "cli.js",
);
const baseUrl = "http://localhost:3100";

function childEnvironment(extra = {}) {
  const environment = { ...process.env, ...extra };
  if (process.platform === "win32") {
    const executablePath = environment.PATH ?? environment.Path;
    delete environment.Path;
    if (executablePath) environment.PATH = executablePath;
  }
  return environment;
}

function run(command, args, options = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, {
      cwd: webRoot,
      env: childEnvironment(options.env),
      stdio: "inherit",
      windowsHide: true,
    });
    child.once("error", reject);
    child.once("exit", (code, signal) => {
      if (signal) {
        reject(new Error(`${path.basename(command)} exited via ${signal}.`));
        return;
      }
      resolve(code ?? 1);
    });
  });
}

async function waitForServer(server) {
  const deadline = Date.now() + 30_000;
  let lastError;
  while (Date.now() < deadline) {
    if (server.exitCode !== null) {
      throw new Error(
        `Next.js exited before becoming ready (code ${server.exitCode}).`,
      );
    }
    try {
      const status = await new Promise((resolve, reject) => {
        const request = http.get(
          baseUrl,
          { agent: false },
          (response) => {
            response.resume();
            response.once("end", () => resolve(response.statusCode ?? 500));
          },
        );
        request.setTimeout(1_000, () => {
          request.destroy(new Error("readiness request timed out"));
        });
        request.once("error", reject);
      });
      if (status < 500) return;
      lastError = new Error(`readiness returned HTTP ${status}`);
    } catch (error) {
      lastError = error;
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error(
    `Next.js did not become ready at ${baseUrl}: ${String(lastError)}`,
  );
}

async function waitForExit(child, timeoutMs) {
  if (child.exitCode !== null) return true;
  return new Promise((resolve) => {
    const timer = setTimeout(() => {
      child.off("exit", exited);
      resolve(false);
    }, timeoutMs);
    const exited = () => {
      clearTimeout(timer);
      resolve(true);
    };
    child.once("exit", exited);
  });
}

async function stopServer(server) {
  if (server.exitCode !== null || server.pid === undefined) return;
  if (process.platform === "win32") {
    server.kill();
    if (await waitForExit(server, 2_000)) return;
    await run("C:\\Windows\\System32\\taskkill.exe", [
      "/pid",
      String(server.pid),
      "/T",
      "/F",
    ]).catch(() => undefined);
    await waitForExit(server, 2_000);
    return;
  }
  server.kill("SIGTERM");
  if (!(await waitForExit(server, 3_000))) {
    server.kill("SIGKILL");
    await waitForExit(server, 2_000);
  }
}

async function main() {
  const buildCode = await run(process.execPath, [nextCli, "build"]);
  if (buildCode !== 0) return buildCode;

  const server = spawn(
    process.execPath,
    [nextCli, "start", "--hostname", "localhost", "--port", "3100"],
    {
      cwd: webRoot,
      env: childEnvironment(),
      stdio: "inherit",
      windowsHide: true,
    },
  );
  let interrupted = false;
  const interrupt = () => {
    interrupted = true;
    void stopServer(server);
  };
  process.once("SIGINT", interrupt);
  process.once("SIGTERM", interrupt);

  try {
    await waitForServer(server);
    if (interrupted) return 130;
    return await run(
      process.execPath,
      [playwrightCli, "test", ...process.argv.slice(2)],
      { env: { PLAYWRIGHT_EXTERNAL_SERVER: "1" } },
    );
  } finally {
    process.off("SIGINT", interrupt);
    process.off("SIGTERM", interrupt);
    await stopServer(server);
  }
}

main()
  .then((code) => {
    process.exitCode = code;
  })
  .catch((error) => {
    console.error(error);
    process.exitCode = 1;
  });
