from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PREVIEW = ROOT / "ops" / "windows" / "team_preview"
ENVIRONMENTS = ROOT / "ops" / "windows" / "environments"


def _parse(path: Path) -> subprocess.CompletedProcess[str]:
    quoted = str(path).replace("'", "''")
    return subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-Command",
            "[void][scriptblock]::Create((Get-Content -Raw "
            f"-LiteralPath '{quoted}'))",
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def test_provisioning_plan_is_read_only_and_host_contract_is_minimal() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        target = Path(temporary) / "program-data"
        command = (
            f"& '{PREVIEW / 'Prepare-RagTeamLanPreview.ps1'}' "
            f"-ReleaseRoot '{temporary}' -ProgramDataRoot '{target}' "
            "-LocalAddress '192.168.40.10' -Plan"
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
        assert result.returncode == 0, result.stderr
        plan = json.loads(result.stdout)
        assert plan["mutations_performed"] is False
        assert plan["expected_alembic_revision"] == "0014_restart_without_backup"
        assert plan["canonical_origin"] == "https://rag.home.arpa"
        assert plan["service_environment_files"] == [
            "caddy",
            "web",
            "api",
            "ingestion",
            "deletion",
            "inference",
            "ocr",
        ]
        assert set(plan["host_prerequisites"]) == {
            "windows_10_or_11_workstation",
            "elevated",
            "docker_desktop_running",
            "ollama_running",
        }
        assert set(plan["host_tools_not_required"]) == {
            "psql",
            "mc",
            "node",
            "python",
            "uv",
        }
        assert not target.exists()


def test_seven_environment_contracts_are_template_exact_and_placeholder_free() -> None:
    deployment = json.loads((ROOT / "ops" / "windows" / "deployment.json").read_text())
    manifest_keys = {
        service["name"]: set(service["environment_keys"])
        for service in deployment["services"]
    }
    for name in ("caddy", "web", "api", "ingestion", "deletion", "inference", "ocr"):
        lines = (ENVIRONMENTS / f"{name}.env.example").read_text().splitlines()
        keys = {line.split("=", 1)[0] for line in lines if line}
        expected = manifest_keys[name]
        if name == "caddy":
            expected.remove("RAG_LAN_IPV6")
            keys.remove("RAG_LAN_IPV6")
        if name == "api":
            expected.update({"PRODUCT_PROFILE", "RAG_LAN_IPV4"})
            keys.update({"PRODUCT_PROFILE", "RAG_LAN_IPV4"})
        assert keys == expected, name
    source = (PREVIEW / "Prepare-RagTeamLanPreview.ps1").read_text()
    assert "REPLACE|<[^>]+>" in source
    assert "Write-StrictEnvironment" in source
    assert "PRODUCT_PROFILE='team_lan_preview_unsigned'" in source
    assert "RAG_LAN_IPV4=$address" in source
    assert "HF_HUB_OFFLINE" in (ENVIRONMENTS / "inference.env.example").read_text()
    assert "TRANSFORMERS_OFFLINE" in (ENVIRONMENTS / "ocr.env.example").read_text()
    assert "OLLAMA_BASE_URL=http://127.0.0.1:11434" in (
        ENVIRONMENTS / "inference.env.example"
    ).read_text()


def test_provisioning_uses_packaged_tools_stdin_and_preserves_state() -> None:
    installer = (PREVIEW / "Install-RagTeamLanPreview.ps1").read_text()
    prepare = (PREVIEW / "Prepare-RagTeamLanPreview.ps1").read_text()
    postgres = (PREVIEW / "Initialize-RagTeamPreviewPostgres.ps1").read_text()
    rustfs = (PREVIEW / "Initialize-RagTeamPreviewRustfs.ps1").read_text()
    assert "LocalRAG-Preparation" not in installer
    assert "-LocalAddress $address -PullModels" in installer
    assert "Prepare-RagTeamLanPreview.ps1" in installer
    assert "if (-not $storesProvisioned)" in installer
    assert "resume journal were preserved" in installer
    assert "runtimes\\api-python\\python.exe" in prepare
    assert "tools\\mc\\mc.exe" in prepare
    assert "docker compose" in postgres
    assert "exec -T postgres psql" in postgres
    assert "$sql | & $docker" in postgres
    assert "& $mc admin user add $alias $access $password *> $null" in rustfs
    assert "local Administrators/SYSTEM" in rustfs
    assert "credentials_logged=$false" in rustfs
    assert "0014_restart_without_backup" in prepare
    assert "relforcerowsecurity" in prepare
    assert "storage-bootstrap" in prepare
    assert "setup-code-issue" in prepare


def test_compose_and_connector_install_state_are_isolated() -> None:
    compose = (PREVIEW / "compose.team-preview.yaml").read_text()
    installer = (PREVIEW / "Install-RagTeamLanPreview.ps1").read_text()
    assert "127.0.0.1:5432:5432" in compose
    assert "127.0.0.1:9000:9000" in compose
    assert "RAG_TEAM_POSTGRES_DATA" in compose
    assert "RAG_TEAM_RUSTFS_DATA" in compose
    assert "installation_id=$installationId" in installer
    assert "connector_generation=1" in installer
    assert "connectors\\generation-1" in installer
    assert "-InstallationId $installationId -ConnectorGeneration 1" in installer
    assert "https://rag.home.arpa/setup" in installer
    assert "'PRODUCT_PROFILE','RAG_LAN_IPV4'" in installer


def test_new_provisioning_powershell_parses() -> None:
    paths = [
        PREVIEW / "Prepare-RagTeamLanPreview.ps1",
        PREVIEW / "Initialize-RagTeamPreviewPostgres.ps1",
        PREVIEW / "Initialize-RagTeamPreviewRustfs.ps1",
        PREVIEW / "Install-RagTeamLanPreview.ps1",
    ]
    for path in paths:
        result = _parse(path)
        assert result.returncode == 0, f"{path.name}: {result.stderr}"


def test_rustfs_uses_supported_user_add_argument_shape_without_echo() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        mc = root / "tools" / "mc" / "mc.exe"
        mc.parent.mkdir(parents=True)
        log = root / "shape.log"
        source = root / "FakeMc.cs"
        source.write_text(
            """
using System;
using System.IO;
public static class FakeMc {
  public static int Main(string[] args) {
    var log = Environment.GetEnvironmentVariable("FAKE_MC_SHAPE_LOG");
    if (args.Length >= 3 && args[0] == "admin" &&
        args[1] == "user" && args[2] == "info") return 1;
    if (args.Length >= 3 && args[0] == "admin" &&
        args[1] == "user" && args[2] == "add") {
      File.AppendAllText(log, "admin-user-add:" + args.Length + Environment.NewLine);
      return args.Length == 6 ? 0 : 9;
    }
    return 0;
  }
}
""",
            encoding="utf-8",
        )
        source_ps = str(source).replace("'", "''")
        mc_ps = str(mc).replace("'", "''")
        compile_result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                    "Add-Type -TypeDefinition (Get-Content -Raw -LiteralPath "
                    f"'{source_ps}') -OutputAssembly '{mc_ps}' "
                    "-OutputType ConsoleApplication",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert compile_result.returncode == 0, compile_result.stderr
        values = {
            "rustfs_root_access": "test-root-access",
            "rustfs_root_secret": "test-root-secret-not-real",
        }
        for role in ("api", "ingestion", "deletion", "maintenance"):
            values[f"rustfs_{role}_access"] = f"test-{role}-access"
            values[f"rustfs_{role}_secret"] = f"test-{role}-secret-not-real"
        secret = root / "secrets.json"
        secret.write_text(json.dumps({"values": values}), encoding="utf-8")
        work = root / "work"
        work.mkdir()
        environment = dict(os.environ)
        environment["FAKE_MC_SHAPE_LOG"] = str(log)
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(PREVIEW / "Initialize-RagTeamPreviewRustfs.ps1"),
                "-Endpoint",
                "http://127.0.0.1:9000/",
                "-SecretDocument",
                str(secret),
                "-McPath",
                str(mc),
                "-WorkingRoot",
                str(work),
            ],
            capture_output=True,
            text=True,
            check=False,
            env=environment,
        )
        assert result.returncode == 0, result.stderr
        assert log.read_text().splitlines() == ["admin-user-add:6"] * 4
        combined = result.stdout + result.stderr
        assert "secret-not-real" not in combined
        assert "test-api-access" not in combined
