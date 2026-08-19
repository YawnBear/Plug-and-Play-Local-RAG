from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SETUP = ROOT / "ops" / "windows" / "v8a" / "Setup-RagFromSource.ps1"


class SourcePersonalAssetPreparationTests(unittest.TestCase):
    def test_setup_script_parses_and_plan_lists_exact_asset_preparation(self) -> None:
        quoted = str(SETUP).replace("'", "''")
        parse = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                "[void][scriptblock]::Create((Get-Content -Raw "
                f"-LiteralPath '{quoted}'))",
            ],
            capture_output=True,
            check=False,
            text=True,
        )
        self.assertEqual(parse.returncode, 0, parse.stderr)

        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(SETUP),
                "-Plan",
            ],
            capture_output=True,
            check=False,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = json.loads(result.stdout)
        self.assertFalse(plan["mutations_performed"])
        self.assertIn("prepare the exact local BGE reranker model assets", plan["automated"])
        self.assertIn(
            "prepare and smoke-test the exact local PaddleOCR-VL 1.6 assets",
            plan["automated"],
        )
        self.assertTrue(plan["model_roots"]["reranker"].endswith(
            r"models\bge-reranker-v2-m3"
        ))
        self.assertTrue(plan["model_roots"]["ocr"].endswith(
            r"models\paddleocr-vl-1.6"
        ))

    def test_source_asset_contract_has_exact_paths_and_transactional_guards(self) -> None:
        source = SETUP.read_text(encoding="utf-8")
        for required in (
            "scripts\\prepare-dev-reranker.py",
            "--preferred-path",
            "bge-reranker-v2-m3.pending",
            "paddleocr-vl-1.6.pending",
            "official_models\\PaddleOCR-VL-1.6\\inference.yml",
            "official_models\\PP-DocLayoutV3\\inference.yml",
            "fonts\\PingFang-SC-Regular.ttf",
            "--pipeline_version",
            "v1.6",
            "--device",
            "cpu",
            "PADDLE_PDX_CACHE_HOME",
            "PADDLE_HOME",
            "Path(sys.argv[1]).write_bytes(system_ocr_fixture())",
            "Protect-RagPersonalPath -Path $rerankerPath -Directory",
            "Protect-RagPersonalPath -Path $ocrPath -Directory",
        ):
            self.assertIn(required, source)
        self.assertIn("asset root is incomplete", source)
        self.assertIn("partial BGE reranker acquisition", source)
        self.assertIn("partial PaddleOCR-VL acquisition", source)
        self.assertIn("Move-Item -LiteralPath $rerankerPending", source)
        self.assertIn("Move-Item -LiteralPath $ocrPending", source)


if __name__ == "__main__":
    unittest.main()
