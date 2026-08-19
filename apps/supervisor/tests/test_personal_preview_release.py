from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
WINDOWS = ROOT / "ops" / "windows" / "v8a"
MODULE = WINDOWS / "RagPersonalPayload.psm1"
VERIFIER = WINDOWS / "Verify-and-Install-Local-RAG.ps1"
PACKAGER = WINDOWS / "New-RagPersonalPreviewRelease.ps1"


def _quote(value: Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


class PersonalPreviewReleaseTests(unittest.TestCase):
    def _run(
        self, command: str, *, environment: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        if environment:
            env.update(environment)
        return subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                command,
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )

    def _payload(self, root: Path) -> Path:
        payload = root / "payload"
        contract = payload / "ops/windows/v8a/personal-release.json"
        contract.parent.mkdir(parents=True)
        contract.write_text(
            json.dumps(
                {"profile_id": "personal", "payload_state": "assembled_unsigned"}
            ),
            encoding="utf-8",
        )
        (payload / "Install-Local-RAG.cmd").write_text(
            "@echo off\r\necho preview\r\n", encoding="utf-8"
        )
        (payload / "payload.txt").write_text("preview payload\n", encoding="utf-8")
        result = self._run(
            f"Import-Module {_quote(MODULE)} -Force; "
            f"New-RagPersonalPayloadInventory -Root {_quote(payload)} | Out-Null"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return payload

    def test_preview_plan_verifies_inventory_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            root = Path(temporary)
            payload = self._payload(root)
            local_app_data = root / "local-app-data"
            result = self._run(
                f"& {_quote(VERIFIER)} -AssetRoot {_quote(payload)} "
                "-UnsignedPreview -Plan",
                environment={"LOCALAPPDATA": str(local_app_data)},
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual(report["distribution"], "unsigned_preview")
            self.assertFalse(report["mutations_performed"])
            self.assertFalse(report["automatic_updates_available"])
            self.assertFalse(local_app_data.exists())

            (payload / "payload.txt").write_text("tampered\n", encoding="utf-8")
            rejected = self._run(
                f"& {_quote(VERIFIER)} -AssetRoot {_quote(payload)} "
                "-UnsignedPreview -Plan",
                environment={"LOCALAPPDATA": str(local_app_data)},
            )
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("inventory mismatch", rejected.stderr)

    def test_preview_packager_creates_single_extract_then_click_zip(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            root = Path(temporary)
            payload = self._payload(root)
            output = root / "release"
            result = self._run(
                f"& {_quote(PACKAGER)} -PayloadRoot {_quote(payload)} "
                f"-OutputRoot {_quote(output)} -Confirm:$false"
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual(report["result"], "packaged_unsigned_preview")
            self.assertFalse(report["signing_required"])
            self.assertFalse(report["automatic_updates_available"])
            archive = Path(report["archive"])
            self.assertTrue(archive.is_file())
            with zipfile.ZipFile(archive) as bundle:
                names = {name.replace("\\", "/") for name in bundle.namelist()}
            self.assertIn("Install-Local-RAG.cmd", names)
            self.assertIn("personal-payload-inventory.json", names)
            self.assertIn("ops/windows/v8a/personal-release.json", names)

    def test_preview_routing_is_explicit_and_updates_are_disabled(self) -> None:
        launcher = (WINDOWS / "Install-Local-RAG.cmd").read_text(encoding="utf-8")
        updates = (WINDOWS / "Check-for-Updates.cmd").read_text(encoding="utf-8")
        installer = (WINDOWS / "Install-RagPersonal.ps1").read_text(encoding="utf-8")
        updater = (WINDOWS / "Update-RagPersonal.ps1").read_text(encoding="utf-8")
        uninstall = (WINDOWS / "Uninstall-RagPersonal.ps1").read_text(
            encoding="utf-8"
        )
        module = (WINDOWS / "RagPersonal.psm1").read_text(encoding="utf-8")
        self.assertIn("-UnsignedPreview", launcher)
        self.assertIn('\\"assembled_unsigned\\"', updates)
        self.assertIn("Automatic updates are disabled", updates)
        self.assertIn("[switch]$UnsignedPreview", installer)
        self.assertIn("'assembled_unsigned'", installer)
        self.assertIn(
            "Automatic updates are unavailable for unsigned preview", updater
        )
        for source in (uninstall, module):
            self.assertIn(
                "payload_state -ceq 'development_template'", source
            )

    def test_preview_scripts_parse(self) -> None:
        for script in (VERIFIER, PACKAGER, WINDOWS / "Install-RagPersonal.ps1"):
            result = self._run(
                "$tokens=$null;$errors=$null;"
                "[void][System.Management.Automation.Language.Parser]::ParseFile("
                f"{_quote(script)},[ref]$tokens,[ref]$errors);"
                "if($errors.Count){$errors | ForEach-Object { Write-Error $_ }; exit 1}"
            )
            self.assertEqual(result.returncode, 0, f"{script}: {result.stderr}")


if __name__ == "__main__":
    unittest.main()
