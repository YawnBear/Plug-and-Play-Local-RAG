import hashlib
import json
import subprocess
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from apps.supervisor.backup import BackupPair
from apps.supervisor.updates import (
    UpdateError,
    build_admin_install_plan,
    verify_update,
)


class UpdateTests(unittest.TestCase):
    def _fixture(self, root: Path) -> tuple[Path, Path, Path, Path, str, Path]:
        openssh = root / "System32" / "OpenSSH"
        openssh.mkdir(parents=True, exist_ok=True)
        (openssh / "ssh-keygen.exe").write_bytes(b"synthetic system verifier")
        artifact = root / "release.zip"
        artifact.write_bytes(b"signed synthetic release")
        manifest = root / "update-manifest.json"
        manifest.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "version": "4.0.0-test",
                    "artifacts": [
                        {
                            "filename": artifact.name,
                            "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
                            "size": artifact.stat().st_size,
                        }
                    ],
                },
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        signature = root / "update-manifest.json.sig"
        signature.write_text("synthetic sshsig", encoding="utf-8")
        allowed = root / "allowed_signers"
        allowed.write_text(
            "rag-release ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAISyntheticTestOnly\n",
            encoding="utf-8",
        )
        stage = root / "stage"
        stage.mkdir(exist_ok=True)
        return (
            manifest,
            signature,
            root,
            allowed,
            hashlib.sha256(allowed.read_bytes()).hexdigest(),
            stage,
        )

    @staticmethod
    def _success(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(args, 0, b"Good signature", b"")

    def _verify(
        self,
        fixture: tuple[Path, Path, Path, Path, str],
        root: Path,
        *,
        run: object | None = None,
    ):
        selected_run = self._success if run is None else run
        with (
            patch(
                "apps.supervisor.updates._windows_system_directory",
                return_value=root / "System32",
            ),
            patch("apps.supervisor.updates._protect_stage"),
        ):
            return verify_update(*fixture, run=selected_run)

    def test_signature_and_artifact_verification_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self._fixture(Path(temporary))

            def failure(
                *args: object, **kwargs: object
            ) -> subprocess.CompletedProcess[bytes]:
                return subprocess.CompletedProcess(args, 1, b"", b"bad signature")

            with self.assertRaisesRegex(UpdateError, "signature"):
                self._verify(fixture, Path(temporary), run=failure)

    def test_verifier_uses_exact_system32_openssh_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = self._fixture(root)
            commands: list[list[str]] = []

            def success(
                command: list[str], **kwargs: object
            ) -> subprocess.CompletedProcess[bytes]:
                commands.append(command)
                return subprocess.CompletedProcess(command, 0, b"ok", b"")

            self._verify(fixture, root, run=success)
            self.assertEqual(
                Path(commands[0][0]),
                (root / "System32" / "OpenSSH" / "ssh-keygen.exe").resolve(),
            )
            allowed_index = commands[0].index("-f") + 1
            signature_index = commands[0].index("-s") + 1
            self.assertEqual(
                Path(commands[0][allowed_index]).parent,
                Path(commands[0][signature_index]).parent,
            )
            self.assertEqual(
                Path(commands[0][allowed_index]).parent.parent,
                fixture[5].resolve(),
            )

    def test_source_swaps_after_staging_do_not_change_verified_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = self._fixture(root)

            def mutate_sources(
                command: list[str], **kwargs: object
            ) -> subprocess.CompletedProcess[bytes]:
                fixture[0].write_bytes(b"replaced source manifest")
                (root / "release.zip").write_bytes(b"replaced source artifact")
                staged_manifest = kwargs["input"]
                self.assertIsInstance(staged_manifest, bytes)
                self.assertIn(b"4.0.0-test", staged_manifest)
                return subprocess.CompletedProcess(command, 0, b"ok", b"")

            verified = self._verify(fixture, root, run=mutate_sources)
            self.assertEqual(verified.version, "4.0.0-test")
            self.assertTrue(
                verified.stage_directory.is_relative_to(fixture[5].resolve())
            )

    def test_admin_install_stays_blocked_without_restore_verified_evidence(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            verified = self._verify(self._fixture(Path(temporary)), Path(temporary))
            with self.assertRaisesRegex(UpdateError, "blocked"):
                build_admin_install_plan(verified, None)
            forged = BackupPair(
                "forged",
                datetime.now(UTC),
                Path(temporary),
                "a" * 64,
                "b" * 64,
                True,
            )
            with self.assertRaisesRegex(UpdateError, "verifier-controlled"):
                build_admin_install_plan(verified, forged)

    def test_wrong_pinned_key_hash_fails_before_signature(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = list(self._fixture(Path(temporary)))
            fixture[4] = "0" * 64
            with self.assertRaisesRegex(UpdateError, "pinned"):
                self._verify(tuple(fixture), Path(temporary))

    def test_tampered_artifact_fails_after_valid_signature(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self._fixture(Path(temporary))
            (Path(temporary) / "release.zip").write_bytes(b"tampered")
            with self.assertRaisesRegex(UpdateError, "artifact"):
                self._verify(fixture, Path(temporary))

    def test_unknown_artifact_field_and_duplicate_filename_fail(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = self._fixture(root)
            value = json.loads(fixture[0].read_text(encoding="utf-8"))
            value["artifacts"][0]["unknown"] = True
            fixture[0].write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(UpdateError, "fields"):
                self._verify(fixture, root)

            fixture = self._fixture(root)
            value = json.loads(fixture[0].read_text(encoding="utf-8"))
            value["artifacts"].append(dict(value["artifacts"][0]))
            fixture[0].write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(UpdateError, "duplicated"):
                self._verify(fixture, root)

    def test_manifest_and_signature_filenames_are_fixed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = list(self._fixture(Path(temporary)))
            wrong = Path(temporary) / "update.json"
            fixture[0].replace(wrong)
            fixture[0] = wrong
            with self.assertRaisesRegex(UpdateError, "filename"):
                self._verify(tuple(fixture), Path(temporary))

    def test_duplicate_json_field_and_schema_rejected_filename_fail(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = self._fixture(root)
            fixture[0].write_text(
                '{"schema_version":1,"schema_version":1,"version":"3","artifacts":[]}',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(UpdateError, "duplicate field"):
                self._verify(fixture, root)

            fixture = self._fixture(root)
            fixture[0].write_text(
                '{"schema_version":1,"version":"3","\\u0076ersion":"4","artifacts":[]}',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(UpdateError, "duplicate field"):
                self._verify(fixture, root)

            fixture = self._fixture(root)
            value = json.loads(fixture[0].read_text(encoding="utf-8"))
            value["artifacts"][0]["filename"] = "unsafe name.zip"
            fixture[0].write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(UpdateError, "unsafe"):
                self._verify(fixture, root)
