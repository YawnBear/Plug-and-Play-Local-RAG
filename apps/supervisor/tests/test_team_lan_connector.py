from __future__ import annotations

import base64
import hashlib
import json
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CONNECTOR = ROOT / "ops" / "windows" / "team_preview"
MODULE = CONNECTOR / "RagTeamLanPreview.psm1"


class TeamLanConnectorTests(unittest.TestCase):
    def _powershell(self, command: str) -> subprocess.CompletedProcess[str]:
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
        )

    def _parse(self, script: Path) -> subprocess.CompletedProcess[str]:
        command = (
            "$tokens=$null;$errors=$null;"
            "[void][System.Management.Automation.Language.Parser]::ParseFile("
            f"'{str(script).replace(chr(39), chr(39) * 2)}',[ref]$tokens,[ref]$errors);"
            "if($errors.Count){$errors | ForEach-Object { Write-Error $_ }; exit 1}"
        )
        return self._powershell(command)

    @staticmethod
    def _quoted(path: Path) -> str:
        return "'" + str(path).replace("'", "''") + "'"

    def _write_test_ca(self, path: Path) -> None:
        target = self._quoted(path)
        command = (
            "$rsa=[Security.Cryptography.RSA]::Create(2048);"
            "$request=[Security.Cryptography.X509Certificates.CertificateRequest]::new("
            "'CN=Local RAG Private CA',$rsa,"
            "[Security.Cryptography.HashAlgorithmName]::SHA256,"
            "[Security.Cryptography.RSASignaturePadding]::Pkcs1);"
            "$request.CertificateExtensions.Add("
            "[Security.Cryptography.X509Certificates.X509BasicConstraintsExtension]::new("
            "$true,$false,0,$true));"
            "$certificate=$request.CreateSelfSigned("
            "[DateTimeOffset]::UtcNow.AddMinutes(-1),"
            "[DateTimeOffset]::UtcNow.AddDays(30));"
            f"[IO.File]::WriteAllBytes({target},$certificate.Export("
            "[Security.Cryptography.X509Certificates.X509ContentType]::Cert));"
            "$certificate.Dispose();$rsa.Dispose()"
        )
        result = self._powershell(command)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_connector_scripts_parse_and_launchers_elevate(self) -> None:
        for script in CONNECTOR.glob("*.ps1"):
            result = self._parse(script)
            self.assertEqual(result.returncode, 0, f"{script}: {result.stderr}")
        install_launcher = (CONNECTOR / "Install-RagTeamConnector.cmd").read_text()
        uninstall_launcher = (CONNECTOR / "Uninstall-RagTeamConnector.cmd").read_text()
        self.assertIn("-Verb RunAs", install_launcher)
        self.assertIn("-Verb RunAs", uninstall_launcher)
        for launcher in (
            "Connect-to-Local-RAG.cmd",
            "Disconnect-from-Local-RAG.cmd",
            "Install-RagTeamConnector.cmd",
            "Uninstall-RagTeamConnector.cmd",
        ):
            source = (CONNECTOR / launcher).read_text()
            self.assertIn(
                "Start-Process -FilePath $env:TEAM_CONNECTOR_LAUNCHER", source
            )
            self.assertIn('-File "%~dp0', source)
            self.assertNotIn("-ArgumentList", source)
        self.assertIn(
            "Connect-to-Local-RAG.cmd",
            (CONNECTOR / "New-RagTeamConnector.ps1").read_text(),
        )
        self.assertIn(
            "Disconnect-from-Local-RAG.cmd",
            (CONNECTOR / "New-RagTeamConnector.ps1").read_text(),
        )

    def test_contract_rejects_private_material_and_unsafe_paths(self) -> None:
        generator = (CONNECTOR / "New-RagTeamConnector.ps1").read_text()
        installer = (CONNECTOR / "Install-RagTeamConnector.ps1").read_text()
        self.assertIn("private material is not allowed", generator.lower())
        self.assertIn("reparse point", generator.lower())
        self.assertIn("case-colliding", generator.lower())
        self.assertIn("tree_sha256", generator)
        self.assertIn("inventory mismatch", installer.lower())
        self.assertIn("unmanaged rag.home.arpa", installer.lower())
        self.assertIn("https://$HostName", installer)

    def test_generator_exposes_core_integration_parameter_aliases(self) -> None:
        source = (CONNECTOR / "New-RagTeamConnector.ps1").read_text()
        wrapper = (CONNECTOR / "New-RagTeamLanConnector.ps1").read_text()
        for parameter in ("OutputRoot", "CaCertificate", "LocalAddress"):
            self.assertIn(parameter, source)
            self.assertIn(parameter, wrapper)

    def test_real_bundle_plan_and_tamper_refusal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            certificate = root / "ca.cer"
            connector = root / "connector"
            hosts = root / "hosts"
            state = root / "state"
            self._write_test_ca(certificate)
            hosts.write_text("127.0.0.1 localhost\n", encoding="utf-8")

            generate = self._powershell(
                "& "
                + self._quoted(CONNECTOR / "New-RagTeamLanConnector.ps1")
                + " -LocalAddress 192.168.50.10 -CaCertificate "
                + self._quoted(certificate)
                + " -OutputRoot "
                + self._quoted(connector)
            )
            self.assertEqual(generate.returncode, 0, generate.stderr)
            expected = {
                "connector.json",
                "inventory.json",
                "rag-local-ca.cer",
                "Install-RagTeamConnector.ps1",
                "Uninstall-RagTeamConnector.ps1",
                "Install-RagTeamConnector.cmd",
                "Uninstall-RagTeamConnector.cmd",
                "Connect-to-Local-RAG.cmd",
                "Disconnect-from-Local-RAG.cmd",
            }
            self.assertEqual({path.name for path in connector.iterdir()}, expected)

            host_output = root / "host-connector"
            integrated = self._powershell(
                "Import-Module "
                + self._quoted(MODULE)
                + " -Force; Invoke-RagTeamPreviewConnector"
                + " -LocalAddress 192.168.50.10 -CaCertificate "
                + self._quoted(certificate)
                + " -OutputRoot "
                + self._quoted(host_output)
                + " -InstallationId 11111111-1111-1111-1111-111111111111"
                + " -ConnectorGeneration 1"
            )
            self.assertEqual(integrated.returncode, 0, integrated.stderr)
            archive = host_output / "Local-RAG-LAN-Connector.zip"
            self.assertTrue(archive.is_file())
            with zipfile.ZipFile(archive) as connector_zip:
                self.assertEqual(set(connector_zip.namelist()), expected)

            plan = self._powershell(
                "& "
                + self._quoted(connector / "Install-RagTeamConnector.ps1")
                + " -ConnectorRoot "
                + self._quoted(connector)
                + " -HostsPath "
                + self._quoted(hosts)
                + " -StateRoot "
                + self._quoted(state)
                + " -Plan"
            )
            self.assertEqual(plan.returncode, 0, plan.stderr)
            self.assertIn('"LanIPv4":  "192.168.50.10"', plan.stdout)
            self.assertFalse(state.exists())

            provider_plan = self._powershell(
                "$drive='RagConnectorTest';"
                + "New-PSDrive -Name $drive -PSProvider FileSystem -Root "
                + self._quoted(root)
                + "|Out-Null;try{$connectorRoot=Join-Path ($drive+':') 'connector';"
                + "& (Join-Path $connectorRoot 'Install-RagTeamConnector.ps1')"
                + " -ConnectorRoot $connectorRoot -HostsPath "
                + self._quoted(hosts)
                + " -StateRoot "
                + self._quoted(state)
                + " -Plan}finally{Remove-PSDrive -Name $drive}"
            )
            self.assertEqual(provider_plan.returncode, 0, provider_plan.stderr)

            (connector / "connector.json").write_text("{}\n", encoding="utf-8")
            tampered = self._powershell(
                "& "
                + self._quoted(connector / "Install-RagTeamConnector.ps1")
                + " -ConnectorRoot "
                + self._quoted(connector)
                + " -HostsPath "
                + self._quoted(hosts)
                + " -StateRoot "
                + self._quoted(state)
                + " -Plan"
            )
            self.assertNotEqual(tampered.returncode, 0)
            self.assertIn("inventory mismatch", tampered.stderr.lower())

    def test_plan_preserves_unrelated_hosts_edits_and_rejects_block_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            certificate = root / "ca.cer"
            connector = root / "connector"
            hosts = root / "hosts with spaces"
            state = root / "state with spaces"
            self._write_test_ca(certificate)
            hosts.write_text("# unrelated one\n127.0.0.1 localhost\n", encoding="utf-8")
            generated = self._powershell(
                f"& {self._quoted(CONNECTOR / 'New-RagTeamConnector.ps1')} "
                f"-OutputRoot {self._quoted(connector)} "
                f"-CaCertificate {self._quoted(certificate)} "
                "-LocalAddress 192.168.50.10 "
                "-InstallationId 11111111-1111-1111-1111-111111111111"
            )
            self.assertEqual(generated.returncode, 0, generated.stderr)
            metadata = json.loads(
                (connector / "connector.json").read_text(encoding="utf-8-sig")
            )
            block = (
                "# BEGIN LOCAL-RAG TEAM CONNECTOR\n"
                "192.168.50.10\trag.home.arpa\n"
                "# END LOCAL-RAG TEAM CONNECTOR"
            )
            hosts.write_text(
                hosts.read_text() + "\n" + block + "\n# unrelated two\n",
                encoding="utf-8",
            )
            state.mkdir()
            prior = b"# unrelated one\n127.0.0.1 localhost\n"
            state.joinpath("state.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "installation_id": metadata["installation_id"],
                        "connector_generation": 1,
                        "hosts_path": str(hosts.resolve()),
                        "prior_sha256": hashlib.sha256(prior).hexdigest(),
                        "prior_bytes_base64": base64.b64encode(prior).decode(),
                        "hosts_post_sha256": hashlib.sha256(
                            hosts.read_bytes()
                        ).hexdigest(),
                        "certificate_sha256": metadata["ca_sha256"],
                        "certificate_thumbprint": metadata["ca_thumbprint"],
                        "certificate_subject": metadata["ca_subject"],
                        "block": block,
                    }
                )
            )
            planned = self._powershell(
                f"& {self._quoted(connector / 'Install-RagTeamConnector.ps1')} "
                f"-ConnectorRoot {self._quoted(connector)} "
                f"-HostsPath {self._quoted(hosts)} "
                f"-StateRoot {self._quoted(state)} -Plan"
            )
            self.assertEqual(planned.returncode, 0, planned.stderr)
            hosts.write_text(
                hosts.read_text().replace(
                    "192.168.50.10\trag.home.arpa", "192.168.50.11\trag.home.arpa"
                ),
                encoding="utf-8",
            )
            drifted = self._powershell(
                f"& {self._quoted(connector / 'Install-RagTeamConnector.ps1')} "
                f"-ConnectorRoot {self._quoted(connector)} "
                f"-HostsPath {self._quoted(hosts)} "
                f"-StateRoot {self._quoted(state)} -Plan"
            )
            self.assertNotEqual(drifted.returncode, 0)
            self.assertIn("drift", drifted.stderr.lower())


if __name__ == "__main__":
    unittest.main()
