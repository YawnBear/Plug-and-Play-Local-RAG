from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ASSEMBLER = ROOT / "ops" / "windows" / "v8a" / "New-RagPersonalPayload.ps1"
SIGNER = ROOT / "ops" / "windows" / "v8a" / "New-RagPersonalSignedRelease.ps1"


def _quote(path: Path) -> str:
    return "'" + str(path).replace("'", "''") + "'"


class PersonalPayloadAssemblerTests(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls) -> None:
        cls._runtime_directory = tempfile.TemporaryDirectory(dir=ROOT)
        runtime_root = Path(cls._runtime_directory.name)
        source = runtime_root / "FixtureRuntime.cs"
        source.write_text(
            """
using System;
using System.IO;
using System.Reflection;

public static class FixtureRuntime {
    public static int Main(string[] args) {
        string location = Assembly.GetExecutingAssembly().Location.ToLowerInvariant();
        string role = location.Contains("api-python") ? "api" :
            location.Contains("ocr-python") ? "ocr" :
            location.Contains("runtimes\\\\node") ? "node" : "mc";
        string markerRoot =
            Environment.GetEnvironmentVariable("RAG_TEST_PROBE_MARKERS");
        if (!String.IsNullOrEmpty(markerRoot)) {
            Directory.CreateDirectory(markerRoot);
            string environment =
                (Environment.GetEnvironmentVariable("PYTHONPATH") ?? "<null>") + "|" +
                (Environment.GetEnvironmentVariable("PYTHONHOME") ?? "<null>") + "|" +
                Environment.GetEnvironmentVariable("PYTHONNOUSERSITE") + "|" +
                Environment.GetEnvironmentVariable("PYTHONUTF8") + "|" +
                Environment.GetEnvironmentVariable("PATH");
            File.WriteAllText(Path.Combine(markerRoot, role + ".probe"), environment);
        }
        if (role == "api") {
            Console.WriteLine("{\\\"result\\\":\\\"pass\\\","
                + "\\\"payload_state\\\":\\\"assembled_unsigned\\\","
                + "\\\"mutations_performed\\\":false}");
        } else if (role == "node") {
            Console.WriteLine("v22.0.0");
        } else if (role == "mc") {
            Console.WriteLine("mc version RELEASE.TEST");
        } else if (role == "ocr") {
            Console.Error.WriteLine("INFO: optional runtime lookup returned no files");
        }
        return 0;
    }
}
""".strip(),
            encoding="utf-8",
        )
        cls._runtime_executable = runtime_root / "FixtureRuntime.exe"
        compile_result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                f"Add-Type -Path {_quote(source)} "
                f"-OutputAssembly {_quote(cls._runtime_executable)} "
                "-OutputType ConsoleApplication",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if compile_result.returncode != 0:
            cls._runtime_directory.cleanup()
            raise RuntimeError(compile_result.stderr)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._runtime_directory.cleanup()

    def _fixture(self, root: Path) -> dict[str, Path]:
        source = root / "source"
        files = [
            "LICENSE",
            "NOTICE",
            "THIRD_PARTY_NOTICES.md",
            "MODEL_LICENSES.md",
            "README.md",
            "SECURITY.md",
            "CONTRIBUTING.md",
            "CODE_OF_CONDUCT.md",
            "apps/api/alembic.ini",
            "apps/api/pyproject.toml",
            "apps/api/uv.lock",
            "apps/api/app/main.py",
            "apps/api/alembic/env.py",
            "apps/supervisor/__init__.py",
            "apps/supervisor/personal_runtime.py",
            "apps/web/.next/standalone/apps/web/server.js",
            "apps/web/.next/standalone/apps/web/.next/required-server-files.json",
            "apps/web/.next/static/app.js",
            "apps/web/public/icon.txt",
            "ops/windows/release-allowed-signers",
            "ops/windows/validate_json_schema.py",
            "ops/release/generate_v8f_artifacts.py",
            "SBOM.cdx.json",
        ]
        personal_files = [
            "capability-profiles.json",
            "capability-profiles.schema.json",
            "Check-for-Updates.cmd",
            "compose.personal.yaml",
            "compose.restore-verifier.yaml",
            "Initialize-RagPersonalPostgres.ps1",
            "Initialize-RagPersonalRustfs.ps1",
            "Install-Local-RAG.cmd",
            "Install-RagPersonal.ps1",
            "Issue-New-Setup-Code.cmd",
            "Issue-RagPersonalSetupCode.ps1",
            "personal-release.schema.json",
            "product-profiles.json",
            "product-profiles.schema.json",
            "RagPersonal.psm1",
            "release-trust-metadata.schema.json",
            "Show-RagPersonalSetupCode.ps1",
            "Start-Local-RAG.cmd",
            "Start-RagPersonal.ps1",
            "Test-RagPersonal.ps1",
            "trust-policy.json",
            "trust-policy.schema.json",
            "Uninstall-Local-RAG.cmd",
            "Uninstall-RagPersonal.ps1",
            "Update-RagPersonal.ps1",
            "validate_contracts.py",
            "Verify-and-Install-Local-RAG.ps1",
        ]
        files.extend(f"ops/windows/v8a/{name}" for name in personal_files)
        for relative in files:
            path = source / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"fixture:{relative}\n", encoding="utf-8")
        release = {
            "payload_state": "development_template",
            "artifacts": [
                {
                    "artifact_id": "runtime.api-python",
                    "relative_path": "runtimes/api-python/python.exe",
                    "kind": "executable",
                    "required": True,
                },
                {
                    "artifact_id": "runtime.ocr-python",
                    "relative_path": "runtimes/ocr-python/python.exe",
                    "kind": "executable",
                    "required": True,
                },
                {
                    "artifact_id": "runtime.node",
                    "relative_path": "runtimes/node/node.exe",
                    "kind": "executable",
                    "required": True,
                },
                {
                    "artifact_id": "tool.mc",
                    "relative_path": "tools/mc/mc.exe",
                    "kind": "executable",
                    "required": True,
                },
                {
                    "artifact_id": "application.api",
                    "relative_path": "apps/api",
                    "kind": "directory",
                    "required": True,
                },
                {
                    "artifact_id": "application.web",
                    "relative_path": "apps/web/.next/standalone/apps/web/server.js",
                    "kind": "file",
                    "required": True,
                },
                {
                    "artifact_id": "model.reranker",
                    "relative_path": "models/bge-reranker-v2-m3",
                    "kind": "directory",
                    "required": True,
                },
                {
                    "artifact_id": "model.ocr",
                    "relative_path": "models/paddleocr-vl-1.6",
                    "kind": "directory",
                    "required": True,
                },
            ],
        }
        (source / "ops/windows/v8a/personal-release.json").write_text(
            json.dumps(release), encoding="utf-8"
        )
        encoded_source = json.dumps(str(source))
        (source / "apps/web/.next/standalone/apps/web/server.js").write_text(
            "const nextConfig = {"
            f'"outputFileTracingRoot":{encoded_source},'
            f'"turbopack":{{"root":{encoded_source}}}'
            "};\n",
            encoding="utf-8",
        )
        required_manifest_path = (
            source
            / "apps/web/.next/standalone/apps/web/.next/required-server-files.json"
        )
        required_manifest_path.write_text(
            json.dumps(
                {
                    "config": {
                        "outputFileTracingRoot": str(source),
                        "turbopack": {"root": str(source)},
                    },
                    "appDir": str(source / "apps/web"),
                    "relativeAppDir": "apps\\web",
                    "files": ["server.js"],
                }
            ),
            encoding="utf-8",
        )
        (source / "unrelated-private-source.txt").write_text(
            "must not be packaged", encoding="utf-8"
        )

        result: dict[str, Path] = {"source": source}
        for name, executable in (
            ("api", "python.exe"),
            ("ocr", "python.exe"),
            ("node", "node.exe"),
            ("reranker", "config.json"),
            ("ocr-model", "config.json"),
        ):
            tree = root / name
            tree.mkdir()
            if executable.endswith(".exe"):
                shutil.copy2(self._runtime_executable, tree / executable)
            else:
                (tree / executable).write_bytes(f"fixture:{name}".encode())
            result[name] = tree
        mc = root / "mc.exe"
        shutil.copy2(self._runtime_executable, mc)
        validator = root / "validation.cmd"
        validator.write_text(
            "@echo off\r\n"
            'echo {"result":"pass","payload_state":"assembled_unsigned",'
            '"mutations_performed":false}\r\n'
            "exit /b 0\r\n",
            encoding="ascii",
        )
        result.update({"mc": mc, "validator": validator, "output": root / "payload"})
        self._probe_markers = root / "probe-markers"
        return result

    def _command(self, fixture: dict[str, Path], *, extra: str = "") -> str:
        return (
            f"& {_quote(ASSEMBLER)} "
            f"-SourceRoot {_quote(fixture['source'])} "
            f"-ApiRuntimeRoot {_quote(fixture['api'])} "
            f"-OcrRuntimeRoot {_quote(fixture['ocr'])} "
            f"-NodeRuntimeRoot {_quote(fixture['node'])} "
            f"-McExecutable {_quote(fixture['mc'])} "
            f"-RerankerModelRoot {_quote(fixture['reranker'])} "
            f"-OcrModelRoot {_quote(fixture['ocr-model'])} "
            f"-OutputRoot {_quote(fixture['output'])} "
            f"-ValidationPython {_quote(fixture['validator'])} "
            f"-Confirm:$false {extra}"
        )

    def _run(self, command: str) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        if hasattr(self, "_probe_markers"):
            environment["RAG_TEST_PROBE_MARKERS"] = str(self._probe_markers)
            environment["PYTHONPATH"] = r"C:\HOSTILE_PYTHONPATH"
            environment["PYTHONHOME"] = r"C:\HOSTILE_PYTHONHOME"
            environment["PYTHONNOUSERSITE"] = "0"
            environment["PYTHONUTF8"] = "0"
            environment["PATH"] = r"C:\HOSTILE_PATH;" + environment.get("PATH", "")
        return subprocess.run(
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
            env=environment,
        )

    def _sign_command(self, payload: Path, root: Path) -> str:
        key = root / "test-key"
        key.write_text("test only", encoding="ascii")
        validator = root / "validation.cmd"
        return (
            f"& {_quote(SIGNER)} -PayloadRoot {_quote(payload)} "
            f"-OutputRoot {_quote(root / 'signed')} -Version 'test-1' "
            f"-ReleaseSequence 1 -PrivateKeyPath {_quote(key)} "
            f"-ValidationPython {_quote(validator)} "
            f"-SignedArtifactStageRoot {_quote(root / 'sign-stage')} -Confirm:$false"
        )

    def test_assembles_exact_personal_layout_and_reports_evidence(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            fixture = self._fixture(Path(temporary))
            result = self._run(self._command(fixture))
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual(report["result"], "assembled_unsigned")
            self.assertEqual(report["contract_state"], "assembled_unsigned")
            self.assertTrue(report["contract_validated"])
            self.assertEqual(
                report["runtime_probes"],
                {
                    "api_contract": "pass",
                    "ocr_imports": "pass",
                    "node_version": "pass",
                    "mc_version": "pass",
                },
            )
            self.assertTrue(report["signing_required"])
            self.assertTrue(report["unsigned_preview_ready"])
            self.assertFalse(report["automatic_updates_available"])
            self.assertTrue(report["clean_machine_qualification_required"])
            self.assertGreater(report["file_count"], 40)
            self.assertGreater(report["byte_count"], 0)
            self.assertRegex(report["tree_sha256"], r"^[0-9a-f]{64}$")

            output = fixture["output"]
            self.assertEqual(
                {item.name for item in output.iterdir()},
                {
                    "LICENSE",
                    "NOTICE",
                    "THIRD_PARTY_NOTICES.md",
                    "MODEL_LICENSES.md",
                    "README.md",
                    "SECURITY.md",
                    "CONTRIBUTING.md",
                    "CODE_OF_CONDUCT.md",
                    "apps",
                    "ops",
                    "runtimes",
                    "models",
                    "tools",
                    "Install-Local-RAG.cmd",
                    "personal-payload-inventory.json",
                },
            )
            required = [
                "apps/api/app/main.py",
                "apps/api/alembic/env.py",
                "apps/supervisor/personal_runtime.py",
                "apps/web/.next/standalone/apps/web/server.js",
                "apps/web/.next/standalone/apps/web/.next/required-server-files.json",
                "apps/web/.next/standalone/apps/web/.next/static/app.js",
                "apps/web/.next/standalone/apps/web/public/icon.txt",
                "runtimes/api-python/python.exe",
                "runtimes/ocr-python/python.exe",
                "runtimes/node/node.exe",
                "tools/mc/mc.exe",
                "models/bge-reranker-v2-m3/config.json",
                "models/paddleocr-vl-1.6/config.json",
                "ops/windows/v8a/Install-RagPersonal.ps1",
                "Install-Local-RAG.cmd",
                "ops/windows/validate_json_schema.py",
            ]
            self.assertTrue(all((output / path).is_file() for path in required))
            self.assertFalse((output / "signed-assets").exists())
            self.assertFalse((output / "unrelated-private-source.txt").exists())
            server_text = (
                output / "apps/web/.next/standalone/apps/web/server.js"
            ).read_text(encoding="utf-8")
            self.assertIn('"outputFileTracingRoot":"."', server_text)
            self.assertIn('"turbopack":{"root":"."}', server_text)
            self.assertNotIn(str(fixture["source"]), server_text)
            self.assertNotIn(str(fixture["source"]).replace("\\", "\\\\"), server_text)
            required_manifest_path = (
                output
                / "apps/web/.next/standalone/apps/web/.next/required-server-files.json"
            )
            required_manifest = json.loads(
                required_manifest_path.read_text(encoding="utf-8")
            )
            self.assertEqual(required_manifest["config"]["outputFileTracingRoot"], ".")
            self.assertEqual(required_manifest["config"]["turbopack"]["root"], ".")
            self.assertEqual(required_manifest["appDir"], ".")
            self.assertEqual(required_manifest["relativeAppDir"], "apps\\web")
            self.assertNotIn(str(fixture["source"]), json.dumps(required_manifest))
            self.assertEqual(
                {path.name for path in self._probe_markers.iterdir()},
                {"api.probe", "ocr.probe", "node.probe", "mc.probe"},
            )
            for marker in self._probe_markers.iterdir():
                observed = marker.read_text(encoding="utf-8")
                self.assertNotIn("HOSTILE", observed)
                values = observed.split("|", 4)
                self.assertEqual(values[:4], ["<null>", "<null>", "1", "1"])
                self.assertIn("runtimes\\api-python", values[4])
                self.assertIn("runtimes\\ocr-python", values[4])
                self.assertIn("runtimes\\node", values[4])
                self.assertIn("tools\\mc", values[4])
                self.assertIn("system32", values[4].lower())
            manifest = json.loads(
                (output / "ops/windows/v8a/personal-release.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(manifest["payload_state"], "assembled_unsigned")

    def test_signer_rejects_wrong_state_and_post_assembly_extra_file(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            root = Path(temporary)
            fixture = self._fixture(root)
            assembled = self._run(self._command(fixture))
            self.assertEqual(assembled.returncode, 0, assembled.stderr)
            manifest_path = fixture["output"] / "ops/windows/v8a/personal-release.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["payload_state"] = "development_template"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            wrong_state = self._run(self._sign_command(fixture["output"], root))
            self.assertNotEqual(wrong_state.returncode, 0)
            self.assertIn("requires an assembled_unsigned payload", wrong_state.stderr)

            fixture = self._fixture(root / "second")
            assembled = self._run(self._command(fixture))
            self.assertEqual(assembled.returncode, 0, assembled.stderr)
            (fixture["output"] / "unexpected.txt").write_text("extra", encoding="utf-8")
            extra = self._run(self._sign_command(fixture["output"], root / "second"))
            self.assertNotEqual(extra.returncode, 0)
            self.assertIn("inventory file set is not exact", extra.stderr)

    def test_rejects_next_reparse_target_outside_source_pnpm_store(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            root = Path(temporary)
            fixture = self._fixture(root)
            outside = root / "outside-package"
            outside.mkdir()
            (outside / "index.js").write_text("module.exports = 1", encoding="utf-8")
            junction = (
                fixture["source"]
                / "apps/web/.next/standalone/apps/web/node_modules/foreign"
            )
            junction.parent.mkdir(parents=True)
            create = self._run(
                f"New-Item -ItemType Junction -Path {_quote(junction)} "
                f"-Target {_quote(outside)} | Out-Null"
            )
            if create.returncode != 0:
                self.skipTest(f"junction creation unavailable: {create.stderr}")
            result = self._run(self._command(fixture))
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("outside SourceRoot\\node_modules\\.pnpm", result.stderr)
            self.assertFalse(fixture["output"].exists())

    def test_materializes_only_declared_package_dependency_closure(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            root = Path(temporary)
            fixture = self._fixture(root)
            peer_environment = (
                fixture["source"] / "node_modules/.pnpm/main@1/node_modules"
            )

            def package(relative: str, manifest: dict[str, object]) -> Path:
                package_root = peer_environment / relative
                package_root.mkdir(parents=True)
                (package_root / "package.json").write_text(
                    json.dumps(manifest), encoding="utf-8"
                )
                (package_root / "index.js").write_text(
                    f"module.exports = {relative!r};\n", encoding="utf-8"
                )
                return package_root

            main = package(
                "main",
                {
                    "name": "main",
                    "dependencies": {"required-dep": "1.0.0"},
                    "optionalDependencies": {"optional-present": "1.0.0"},
                    "peerDependencies": {
                        "@scope/required-peer": "1.0.0",
                        "@playwright/test": "1.0.0",
                    },
                    "peerDependenciesMeta": {"@playwright/test": {"optional": True}},
                },
            )
            package("required-dep", {"name": "required-dep"})
            package("optional-present", {"name": "optional-present"})
            package("@scope/required-peer", {"name": "@scope/required-peer"})
            package("@playwright/test", {"name": "@playwright/test"})
            package("unrelated-sibling", {"name": "unrelated-sibling"})
            package_local_modules = main / "node_modules"
            (package_local_modules / ".bin").mkdir(parents=True)
            (package_local_modules / ".bin/source-bound.CMD").write_text(
                str(fixture["source"]), encoding="utf-8"
            )
            (package_local_modules / "undeclared/index.js").parent.mkdir()
            (package_local_modules / "undeclared/index.js").write_text(
                "module.exports = 'undeclared';\n", encoding="utf-8"
            )
            bin_root = peer_environment / ".bin"
            bin_root.mkdir()
            (bin_root / "playwright.CMD").write_text("playwright", encoding="utf-8")

            link = (
                fixture["source"]
                / "apps/web/.next/standalone/apps/web/node_modules/main"
            )
            link.parent.mkdir(parents=True)
            create = self._run(
                f"New-Item -ItemType Junction -Path {_quote(link)} "
                f"-Target {_quote(main)} | Out-Null"
            )
            if create.returncode != 0:
                self.skipTest(f"junction creation unavailable: {create.stderr}")
            result = self._run(self._command(fixture))
            self.assertEqual(result.returncode, 0, result.stderr)
            modules = (
                fixture["output"] / "apps/web/.next/standalone/apps/web/node_modules"
            )
            for included in (
                "main/index.js",
                "required-dep/index.js",
                "optional-present/index.js",
                "@scope/required-peer/index.js",
            ):
                self.assertTrue((modules / included).is_file(), included)
            for excluded in (
                "@playwright/test",
                "unrelated-sibling",
                ".bin/playwright.CMD",
                "main/node_modules/.bin/source-bound.CMD",
                "main/node_modules/undeclared",
            ):
                self.assertFalse((modules / excluded).exists(), excluded)
            reparse = self._run(
                f"@(Get-ChildItem -LiteralPath {_quote(fixture['output'])} "
                "-Recurse -Force | Where-Object { $_.Attributes -band "
                "[IO.FileAttributes]::ReparsePoint }).Count"
            )
            self.assertEqual(reparse.returncode, 0, reparse.stderr)
            self.assertEqual(reparse.stdout.strip(), "0")

    def test_rejects_secret_shaped_runtime_and_leaves_output_clean(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            fixture = self._fixture(Path(temporary))
            (fixture["api"] / "password.json").write_text("secret", encoding="utf-8")
            result = self._run(self._command(fixture))
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("denied secret-shaped filename", result.stderr)
            self.assertFalse(fixture["output"].exists())

            fixture = self._fixture(Path(temporary) / "uppercase-private-pem")
            (fixture["api"] / "PRIVATE.PEM").write_text(
                "-----BEGIN PRIVATE KEY-----\nnot-a-real-key\n",
                encoding="ascii",
            )
            result = self._run(self._command(fixture))
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("denied secret-shaped filename", result.stderr)
            self.assertFalse(fixture["output"].exists())

    def test_rejects_nonrelocatable_python_runtime(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            fixture = self._fixture(Path(temporary))
            (fixture["api"] / "pyvenv.cfg").write_text(
                "home=C:/Python", encoding="utf-8"
            )
            result = self._run(self._command(fixture))
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("non-relocatable Python runtime", result.stderr)
            self.assertFalse(fixture["output"].exists())

    def test_rejects_absolute_path_pth_entry(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            root = Path(temporary)
            fixture = self._fixture(root)
            (fixture["api"] / "external-base.pth").write_text(
                "C:\\external\\python\\site-packages\n", encoding="utf-8"
            )
            result = self._run(self._command(fixture))
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("absolute-path .pth entry", result.stderr)
            self.assertFalse(fixture["output"].exists())

            fixture = self._fixture(root / "invalid-pth-encoding")
            (fixture["api"] / "invalid.pth").write_bytes(b"relative-\xff-path\n")
            result = self._run(self._command(fixture))
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(".pth configuration is not valid UTF-8", result.stderr)
            self.assertFalse(fixture["output"].exists())

    def test_rejects_text_configuration_that_leaks_input_root(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            fixture = self._fixture(Path(temporary))
            (fixture["node"] / "runtime.json").write_text(
                json.dumps({"source": str(fixture["source"])}), encoding="utf-8"
            )
            result = self._run(self._command(fixture))
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("text configuration leaks an input root", result.stderr)
            self.assertFalse(fixture["output"].exists())

    def test_prunes_runtime_tests_and_scans_legacy_encoded_runtime_text(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            root = Path(temporary)
            fixture = self._fixture(root)
            upstream_test = (
                fixture["ocr"]
                / "Lib/site-packages/joblib/test/special_encoding.py"
            )
            upstream_test.parent.mkdir(parents=True)
            upstream_test.write_bytes(b"# coding: cp1252\nvalue = '\x96'\n")
            legacy_license = (
                fixture["ocr"]
                / "Lib/site-packages/example.dist-info/licenses/vendor.txt"
            )
            legacy_license.parent.mkdir(parents=True)
            legacy_license.write_bytes(b"vendor license \x96 terms\n")
            result = self._run(self._command(fixture))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(
                (
                    fixture["output"]
                    / "runtimes/ocr-python/Lib/site-packages/joblib/test"
                ).exists()
            )
            self.assertTrue(
                (
                    fixture["output"]
                    / "runtimes/ocr-python/Lib/site-packages/"
                    "example.dist-info/licenses/vendor.txt"
                ).is_file()
            )

            fixture = self._fixture(root / "legacy-path-leak")
            runtime_text = (
                fixture["ocr"] / "Lib/site-packages/joblib/runtime_text.txt"
            )
            runtime_text.parent.mkdir(parents=True)
            runtime_text.write_bytes(
                b"legacy \xff path=" + str(fixture["source"]).encode("ascii")
            )
            result = self._run(self._command(fixture))
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("text configuration leaks an input root", result.stderr)
            self.assertFalse(fixture["output"].exists())

    def test_rejects_output_overlap_and_nonempty_output(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            fixture = self._fixture(Path(temporary))
            fixture["output"] = fixture["source"] / "payload"
            overlap = self._run(self._command(fixture))
            self.assertNotEqual(overlap.returncode, 0)
            self.assertIn("outside every input", overlap.stderr)

            fixture["output"] = Path(temporary) / "payload"
            fixture["output"].mkdir()
            marker = fixture["output"] / "existing.txt"
            marker.write_text("preserve", encoding="utf-8")
            nonempty = self._run(self._command(fixture))
            self.assertNotEqual(nonempty.returncode, 0)
            self.assertIn("absent or an empty regular directory", nonempty.stderr)
            self.assertEqual(marker.read_text(encoding="utf-8"), "preserve")

    def test_what_if_does_not_mutate_output(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            fixture = self._fixture(Path(temporary))
            result = self._run(self._command(fixture, extra="-WhatIf"))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(fixture["output"].exists())
            self.assertFalse(any(Path(temporary).glob(".personal-payload-*")))

    def test_powershell_parses(self) -> None:
        command = (
            "$tokens=$null;$errors=$null;"
            f"[void][System.Management.Automation.Language.Parser]::ParseFile({_quote(ASSEMBLER)},"
            "[ref]$tokens,[ref]$errors);"
            "if($errors.Count -ne 0){$errors | ForEach-Object { "
            "Write-Error $_ }; exit 1}"
        )
        result = self._run(command)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_payload_source_dependencies_are_git_tracked(self) -> None:
        assembler = ASSEMBLER.read_text(encoding="utf-8")
        for forbidden in ("docs\\", "TRUST.md"):
            self.assertNotIn(forbidden, assembler)
        self.assertEqual(assembler.count("RagPersonalPayload.psm1"), 1)
        tracked_roots = [
            "apps/api/app",
            "apps/api/alembic",
            "apps/supervisor",
            "LICENSE",
            "NOTICE",
            "THIRD_PARTY_NOTICES.md",
            "MODEL_LICENSES.md",
            "README.md",
            "SECURITY.md",
            "CONTRIBUTING.md",
            "CODE_OF_CONDUCT.md",
            "apps/api/alembic.ini",
            "apps/api/pyproject.toml",
            "apps/api/uv.lock",
            "ops/windows/release-allowed-signers",
            "ops/windows/validate_json_schema.py",
            "ops/windows/v8a",
            "SBOM.cdx.json",
            "ops/release/generate_v8f_artifacts.py",
        ]
        result = subprocess.run(
            ["git", "ls-files", "--", *tracked_roots],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        tracked = set(result.stdout.splitlines())
        for path in tracked_roots:
            self.assertTrue(
                path in tracked or any(item.startswith(path + "/") for item in tracked),
                path,
            )


if __name__ == "__main__":
    unittest.main()
