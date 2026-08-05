import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
VERIFIER_PATH = ROOT / "ops" / "windows" / "verify_dependencies.py"
SPEC = importlib.util.spec_from_file_location("rag_dependency_verifier", VERIFIER_PATH)
assert SPEC is not None and SPEC.loader is not None
VERIFIER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERIFIER)


class DependencyVerifierTests(unittest.TestCase):
    def test_rustfs_endpoint_rejects_non_loopback_before_client_creation(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "loopback"):
            VERIFIER.verify_rustfs(
                endpoint="http://192.168.1.20:9000",
                bucket="test",
                key="probe",
                expected_sha256="0" * 64,
                credentials=Path("not-read.env"),
            )
        with self.assertRaisesRegex(RuntimeError, "loopback"):
            VERIFIER.verify_rustfs(
                endpoint="http://localhost:9000",
                bucket="test",
                key="probe",
                expected_sha256="0" * 64,
                credentials=Path("not-read.env"),
            )

    def test_structured_ocr_semantics_bind_page_and_text(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = {
                "pages": [
                    {
                        "page_index": 0,
                        "blocks": [{"block_content": "Expected signed text"}],
                        "layout_det_res": {"page_index": None},
                    },
                    {
                        "page_index": 1,
                        "blocks": [{"text": "Second page"}],
                    },
                ]
            }
            (root / "fixture_res.json").write_text(
                json.dumps(payload),
                encoding="utf-8",
            )
            result = VERIFIER.structured_ocr_semantics(root)
            self.assertEqual(result["page_count"], 2)
            self.assertRegex(result["structured_sha256"], r"^[0-9a-f]{64}$")
            self.assertRegex(result["text_sha256"], r"^[0-9a-f]{64}$")
            payload["pages"][1]["blocks"][0]["text"] = "Tampered"
            (root / "fixture_res.json").write_text(
                json.dumps(payload),
                encoding="utf-8",
            )
            changed = VERIFIER.structured_ocr_semantics(root)
            self.assertNotEqual(result["text_sha256"], changed["text_sha256"])

    def test_structured_ocr_semantics_rejects_invalid_page_identifiers(self) -> None:
        for invalid in (True, "0", 0.0):
            with (
                self.subTest(invalid=invalid),
                tempfile.TemporaryDirectory() as temporary,
            ):
                root = Path(temporary)
                (root / "fixture_res.json").write_text(
                    json.dumps(
                        {
                            "page_index": invalid,
                            "blocks": [{"block_content": "Text is present"}],
                        }
                    ),
                    encoding="utf-8",
                )

                with self.assertRaisesRegex(RuntimeError, "identifier is invalid"):
                    VERIFIER.structured_ocr_semantics(root)

    def test_structured_ocr_semantics_accepts_single_image_page(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "fixture_res.json").write_text(
                json.dumps(
                    {
                        "input_path": "fixture.png",
                        "page_index": None,
                        "width": 1200,
                        "height": 800,
                        "blocks": [{"block_content": "Text is present"}],
                        "layout_det_res": {"page_index": None},
                    }
                ),
                encoding="utf-8",
            )

            result = VERIFIER.structured_ocr_semantics(root)

            self.assertEqual(result["page_count"], 1)

    def test_structured_ocr_semantics_rejects_null_only_page_identifiers(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "fixture_res.json").write_text(
                json.dumps(
                    {
                        "page_index": None,
                        "nested": {"page_id": None, "page_num": None},
                        "blocks": [{"block_content": "Text is present"}],
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(RuntimeError, "no page semantics"):
                VERIFIER.structured_ocr_semantics(root)


if __name__ == "__main__":
    unittest.main()
