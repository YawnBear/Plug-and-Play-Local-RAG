import ast
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from apps.supervisor.release import (
    ALEMBIC_REVISION,
    RLS_TABLES,
    ReleaseError,
    load_release_pins,
)
from apps.supervisor.updates import UpdateArtifact, VerifiedUpdate


class ReleaseEvidenceTests(unittest.TestCase):
    def test_release_rls_set_matches_migration_policy_set(self) -> None:
        migration_path = (
            Path(__file__).parents[2]
            / "api"
            / "alembic"
            / "versions"
            / "0001_v4_baseline.py"
        )
        module = ast.parse(migration_path.read_text(encoding="utf-8"))
        assignment = next(
            node
            for node in module.body
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "_EXPECTED_POLICIES"
                for target in node.targets
            )
        )
        policies = ast.literal_eval(assignment.value)
        self.assertEqual(
            RLS_TABLES, tuple(sorted((*policies, "folder_create_grants")))
        )

    def _fixture(self, root: Path) -> tuple[Path, VerifiedUpdate]:
        path = root / "release-evidence.json"
        value = {
            "schema_version": 1,
            "alembic_revision": ALEMBIC_REVISION,
            "force_rls_tables": list(RLS_TABLES),
            "containers": {
                "postgres_image_digest": "sha256:" + "a" * 64,
                "rustfs_image_digest": "sha256:" + "b" * 64,
            },
            "rustfs": {
                "bucket": "rag-originals",
                "probe_object_key": "dependency-probe.bin",
                "probe_object_sha256": "c" * 64,
            },
            "ollama_models": {
                "qwen3:8b": "d" * 64,
                "qwen3-embedding:0.6b": "e" * 64,
            },
            "reranker": {
                "identity": "BAAI/bge-reranker-v2-m3",
                "device": "cpu",
                "model_assets_sha256": "d" * 64,
            },
            "ocr": {
                "paddleocr_version": "3.7.0",
                "pipeline_version": "1.6",
                "fixture_sha256": "f" * 64,
                "expected_output_sha256": "5" * 64,
                "expected_structured_sha256": "6" * 64,
                "expected_text_sha256": "7" * 64,
                "expected_page_count": 1,
                "model_assets_sha256": "8" * 64,
            },
            "runtimes": {
                "api_python_sha256": "3" * 64,
                "ocr_python_sha256": "4" * 64,
                "docker_executable_sha256": "9" * 64,
                "api_python_tree_sha256": "a" * 64,
                "ocr_python_tree_sha256": "b" * 64,
                "node_tree_sha256": "c" * 64,
                "openssl_tree_sha256": "d" * 64,
            },
            "verifier_sha256": "1" * 64,
            "max_evidence_age_seconds": 900,
        }
        path.write_text(json.dumps(value), encoding="utf-8")
        artifact = UpdateArtifact(
            path.name,
            hashlib.sha256(path.read_bytes()).hexdigest(),
            path.stat().st_size,
        )
        return path, VerifiedUpdate("4.0.0-test", "2" * 64, (artifact,), root)

    def test_release_pins_are_bound_to_signed_artifact_and_fixed_baseline(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path, update = self._fixture(Path(temporary))
            pins = load_release_pins(path, update)
            self.assertEqual(pins.manifest_sha256, update.artifacts[0].sha256)
            self.assertEqual(pins.rustfs_bucket, "rag-originals")
            self.assertEqual(pins.ocr_expected_page_count, 1)
            self.assertEqual(pins.node_tree_sha256, "c" * 64)
            self.assertEqual(
                set(pins.ollama_models),
                {
                    "qwen3:8b",
                    "qwen3-embedding:0.6b",
                },
            )

    def test_wrong_revision_or_incomplete_rls_set_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path, _ = self._fixture(root)
            value = json.loads(path.read_text(encoding="utf-8"))
            value["alembic_revision"] = "0001_v3_baseline"
            path.write_text(json.dumps(value), encoding="utf-8")
            artifact = UpdateArtifact(
                path.name,
                hashlib.sha256(path.read_bytes()).hexdigest(),
                path.stat().st_size,
            )
            with self.assertRaisesRegex(ReleaseError, "revision"):
                load_release_pins(
                    path,
                    VerifiedUpdate("test", "2" * 64, (artifact,), root),
                )

            path, _ = self._fixture(root)
            value = json.loads(path.read_text(encoding="utf-8"))
            value["force_rls_tables"].pop()
            path.write_text(json.dumps(value), encoding="utf-8")
            artifact = UpdateArtifact(
                path.name,
                hashlib.sha256(path.read_bytes()).hexdigest(),
                path.stat().st_size,
            )
            with self.assertRaisesRegex(ReleaseError, "RLS"):
                load_release_pins(
                    path,
                    VerifiedUpdate("test", "2" * 64, (artifact,), root),
                )
