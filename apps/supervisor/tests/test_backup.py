import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from apps.supervisor.backup import (
    SYNTHETIC_GUARD,
    SYNTHETIC_MARKER,
    BackupError,
    BackupPair,
    assert_synthetic_store,
    backup_status,
    fixture_marker,
    load_captured_pair,
    restore_verification_status,
    retention_plan,
    write_synthetic_pair_ledger,
)

RUN_TOKEN = "test_run_token_0123456789abcdef0"


class BackupTests(unittest.TestCase):
    def _mark_fixture(self, root: Path, token: str = RUN_TOKEN) -> None:
        (root / SYNTHETIC_MARKER).write_text(
            json.dumps(fixture_marker(token)),
            encoding="utf-8",
        )

    def test_backup_is_hard_disabled_without_destination(self) -> None:
        status = backup_status(None)
        self.assertEqual(status["state"], "not_configured")
        self.assertFalse(status["schedule_enabled"])
        self.assertIsNone(status["schedule"])

    def test_synthetic_guard_requires_token_marker_and_temp_containment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temp_root = Path(temporary)
            root = temp_root / "isolated"
            root.mkdir()
            self._mark_fixture(root)
            with self.assertRaises(BackupError):
                assert_synthetic_store(
                    root,
                    "almost",
                    run_token=RUN_TOKEN,
                    allowed_temp_root=temp_root,
                )
            with self.assertRaisesRegex(BackupError, "run token"):
                assert_synthetic_store(
                    root,
                    SYNTHETIC_GUARD,
                    run_token="different_run_token_0123456789abc",
                    allowed_temp_root=temp_root,
                )
            self.assertEqual(
                assert_synthetic_store(
                    root,
                    SYNTHETIC_GUARD,
                    run_token=RUN_TOKEN,
                    allowed_temp_root=temp_root,
                ),
                root.resolve(),
            )

    def test_allowed_temp_root_itself_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._mark_fixture(root)
            with self.assertRaisesRegex(BackupError, "child"):
                assert_synthetic_store(
                    root,
                    SYNTHETIC_GUARD,
                    run_token=RUN_TOKEN,
                    allowed_temp_root=root,
                )

    def test_capture_ledger_cannot_self_certify_verification(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temp_root = Path(temporary)
            root = temp_root / "pair"
            root.mkdir()
            self._mark_fixture(root)
            (root / "database.dump").write_bytes(b"synthetic database")
            (root / "manifest.json").write_text('{"synthetic":true}', encoding="utf-8")
            ledger = write_synthetic_pair_ledger(
                root,
                SYNTHETIC_GUARD,
                run_token=RUN_TOKEN,
                allowed_temp_root=temp_root,
                backup_id="synthetic-pair",
                captured_at=datetime(2026, 7, 24, tzinfo=UTC),
            )
            value = json.loads(ledger.read_text(encoding="utf-8"))
            self.assertFalse(value["verified"])
            self.assertEqual(value["state"], "captured")
            self.assertIsNone(value["restore_verification"])
            pair = load_captured_pair(root)
            self.assertFalse(pair.verified)

            value["verified"] = True
            ledger.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(BackupError, "cannot claim"):
                load_captured_pair(root)

    def test_retention_blocks_rotation_when_pairs_are_unverified(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temp_root = Path(temporary)
            root = temp_root / "fixture"
            root.mkdir()
            self._mark_fixture(root)
            start = datetime(2026, 7, 24, tzinfo=UTC)
            pairs = [
                BackupPair(
                    f"pair-{day}",
                    start - timedelta(days=day),
                    root / f"pair-{day}",
                    "a" * 64,
                    "b" * 64,
                    False,
                )
                for day in range(12)
            ]
            for pair in pairs:
                pair.directory.mkdir()
            plan = retention_plan(
                pairs,
                fixture_root=root,
                confirmation=SYNTHETIC_GUARD,
                run_token=RUN_TOKEN,
                allowed_temp_root=temp_root,
            )
            self.assertEqual(plan["state"], "blocked_unverified")
            self.assertEqual(plan["retain"], [item.backup_id for item in pairs])
            self.assertEqual(plan["eligible_for_atomic_rotation"], [])

    def test_retention_rejects_duplicate_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temp_root = Path(temporary)
            root = temp_root / "fixture"
            root.mkdir()
            self._mark_fixture(root)
            pair_root_a = root / "a"
            pair_root_b = root / "b"
            pair_root_a.mkdir()
            pair_root_b.mkdir()
            pairs = [
                BackupPair(
                    "duplicate",
                    datetime.now(UTC),
                    pair_root,
                    "a" * 64,
                    "b" * 64,
                    False,
                )
                for pair_root in (pair_root_a, pair_root_b)
            ]
            with self.assertRaisesRegex(BackupError, "unique"):
                retention_plan(
                    pairs,
                    fixture_root=root,
                    confirmation=SYNTHETIC_GUARD,
                    run_token=RUN_TOKEN,
                    allowed_temp_root=temp_root,
                )

    def test_retention_rejects_caller_asserted_verified_pair(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temp_root = Path(temporary)
            root = temp_root / "fixture"
            pair_root = root / "pair"
            pair_root.mkdir(parents=True)
            self._mark_fixture(root)
            pair = BackupPair(
                "forged-verified",
                datetime.now(UTC),
                pair_root,
                "a" * 64,
                "b" * 64,
                True,
            )
            with self.assertRaisesRegex(BackupError, "caller-asserted"):
                retention_plan(
                    [pair],
                    fixture_root=root,
                    confirmation=SYNTHETIC_GUARD,
                    run_token=RUN_TOKEN,
                    allowed_temp_root=temp_root,
                )

    def test_retention_rejects_pair_path_outside_guarded_fixture(self) -> None:
        with (
            tempfile.TemporaryDirectory() as fixture_temporary,
            tempfile.TemporaryDirectory() as outside_temporary,
        ):
            temp_root = Path(fixture_temporary)
            root = temp_root / "fixture"
            root.mkdir()
            self._mark_fixture(root)
            pair = BackupPair(
                "outside",
                datetime.now(UTC),
                Path(outside_temporary),
                "a" * 64,
                "b" * 64,
                False,
            )
            with self.assertRaisesRegex(BackupError, "outside"):
                retention_plan(
                    [pair],
                    fixture_root=root,
                    confirmation=SYNTHETIC_GUARD,
                    run_token=RUN_TOKEN,
                    allowed_temp_root=temp_root,
                )

    def test_restore_verification_remains_blocked_without_authenticated_run(
        self,
    ) -> None:
        status = restore_verification_status()
        self.assertEqual(status["state"], "blocked")
        self.assertFalse(status["verified"])
        self.assertGreaterEqual(len(status["requirements"]), 8)
