from __future__ import annotations

import os
import socket
import subprocess
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
WINDOWS = ROOT / "ops" / "windows"
STANDALONE = ROOT / "apps" / "web" / ".next" / "standalone"


class WindowsReleaseWebPackagingTests(unittest.TestCase):
    def test_builder_materializes_traced_next_tree_without_reparse_points(self) -> None:
        builder = (WINDOWS / "New-RagReleaseArtifact.ps1").read_text(encoding="utf-8")
        self.assertIn("Copy-RagMaterializedTree", builder)
        self.assertIn("Copy-RagMaterializedTreeInner", builder)
        source = STANDALONE
        if not source.exists():
            self.skipTest("production Next standalone output is not built")
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            destination = Path(temporary) / "materialized"
            command = (
                f"$null=$null; $tokens=$null; $errors=$null; "
                f"$ast=[System.Management.Automation.Language.Parser]::ParseFile("
                f"'{WINDOWS / 'New-RagReleaseArtifact.ps1'}',"
                f"[ref]$tokens,[ref]$errors); "
                "$denied=$ast.Find({param($n) $n -is "
                "[System.Management.Automation.Language.FunctionDefinitionAst] -and "
                "$n.Name -eq 'Test-RagDeniedReleaseName'},$true); "
                ". ([scriptblock]::Create($denied.Extent.Text)); "
                "$material=$ast.Find({param($n) $n -is "
                "[System.Management.Automation.Language.FunctionDefinitionAst] -and "
                "$n.Name -eq 'Copy-RagMaterializedTree'},$true); "
                ". ([scriptblock]::Create($material.Extent.Text)); "
                f"Copy-RagMaterializedTree -Source '{source}' "
                f"-Destination '{destination}'"
            )
            result = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    command,
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            reparses = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    f"@(Get-ChildItem -LiteralPath '{destination}' -Recurse -Force | "
                    "Where-Object { $_.Attributes -band "
                    "[IO.FileAttributes]::ReparsePoint }).Count",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(reparses.returncode, 0, reparses.stderr)
            self.assertEqual(reparses.stdout.strip(), "0")

            node = Path(r"C:\tmp\rag-v4-release-inputs\runtimes\node\node.exe")
            server = destination / "apps" / "web" / "server.js"
            if not node.exists() or not server.exists():
                self.skipTest(
                    "packaged Node runtime or standalone server is unavailable"
                )
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                port = probe.getsockname()[1]
            environment = os.environ.copy()
            environment.update(
                {
                    "PATH": os.environ.get("SystemRoot", r"C:\Windows")
                    + r"\System32;"
                    + os.environ.get("SystemRoot", r"C:\Windows"),
                    "NODE_ENV": "production",
                    "HOSTNAME": "127.0.0.1",
                    "PORT": str(port),
                }
            )
            process = subprocess.Popen(
                [str(node), str(server)],
                cwd=server.parent,
                env=environment,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        self.fail(process.stderr.read())
                    try:
                        with urllib.request.urlopen(
                            f"http://127.0.0.1:{port}/", timeout=1
                        ) as response:
                            self.assertEqual(response.status, 200)
                            break
                    except Exception:
                        time.sleep(0.25)
                else:
                    self.fail("materialized standalone server did not become ready")
            finally:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)


if __name__ == "__main__":
    unittest.main()
