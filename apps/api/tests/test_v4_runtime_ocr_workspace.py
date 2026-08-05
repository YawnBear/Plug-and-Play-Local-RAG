import os
from pathlib import Path

import pytest

from app.runtime.ocr_workspace import OcrWorkspaceManager, WorkspaceError


class Clock:
    value = 1_000.0

    def __call__(self) -> float:
        return self.value


@pytest.mark.parametrize(
    "job_id",
    ["../escape", "UPPER", "job_underscore", "-leading", "trailing-", ""],
)
def test_workspace_rejects_untrusted_job_ids(tmp_path: Path, job_id: str) -> None:
    manager = OcrWorkspaceManager(tmp_path.resolve())

    with pytest.raises(ValueError, match="job_id"):
        manager.create(job_id)


def test_workspace_is_exclusive_and_cleanup_is_confined(tmp_path: Path) -> None:
    root = (tmp_path / "ocr").resolve()
    manager = OcrWorkspaceManager(root)
    workspace = manager.create("job-123")
    (workspace / "result.json").write_text("{}", encoding="utf-8")

    with pytest.raises(WorkspaceError, match="already exists"):
        manager.create("job-123")

    manager.cleanup("job-123")
    assert not workspace.exists()
    manager.cleanup("job-123")


def test_abandoned_cleanup_removes_only_valid_old_directories(
    tmp_path: Path,
) -> None:
    root = (tmp_path / "ocr").resolve()
    clock = Clock()
    manager = OcrWorkspaceManager(root, clock=clock)
    old = manager.create("old-job")
    recent = manager.create("recent-job")
    invalid = root / "Invalid"
    invalid.mkdir()
    os.utime(old, (800, 800))
    os.utime(recent, (990, 990))

    report = manager.cleanup_abandoned(older_than_seconds=100)

    assert report.removed == ("old-job",)
    assert report.rejected == ("Invalid",)
    assert not old.exists()
    assert recent.exists()


def test_workspace_rejects_symlink_root_when_supported(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks require unavailable host permission")

    with pytest.raises(WorkspaceError, match="links"):
        OcrWorkspaceManager(link.absolute())
