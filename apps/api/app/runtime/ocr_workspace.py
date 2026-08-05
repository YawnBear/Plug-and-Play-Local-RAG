import os
import re
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

JOB_ID_PATTERN = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?")


class WorkspaceError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class CleanupReport:
    removed: tuple[str, ...]
    rejected: tuple[str, ...]


class OcrWorkspaceManager:
    def __init__(
        self,
        root: Path,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not root.expanduser().is_absolute():
            raise ValueError("OCR workspace root must be absolute")
        expanded = root.expanduser()
        expanded.mkdir(parents=True, exist_ok=True)
        self._reject_link(expanded)
        for parent in expanded.parents:
            if parent.exists():
                self._reject_link(parent)
        self.root = expanded.resolve(strict=True)
        self._clock = clock
        self._reject_link(self.root)

    def create(self, job_id: str) -> Path:
        self._validate_job_id(job_id)
        self._validate_root()
        workspace = self.root / job_id
        self._assert_direct_child(workspace)
        try:
            workspace.mkdir(mode=0o700)
        except FileExistsError as exc:
            raise WorkspaceError("OCR workspace already exists") from exc
        self._reject_link(workspace)
        return workspace

    def cleanup(self, job_id: str) -> None:
        self._validate_job_id(job_id)
        self._validate_root()
        workspace = self.root / job_id
        self._assert_direct_child(workspace)
        if not workspace.exists():
            return
        self._reject_link(workspace)
        self._reject_tree_links(workspace)
        shutil.rmtree(workspace)

    def get(self, job_id: str) -> Path:
        self._validate_job_id(job_id)
        self._validate_root()
        workspace = self.root / job_id
        self._assert_direct_child(workspace)
        if not workspace.is_dir():
            raise WorkspaceError("OCR workspace does not exist")
        self._reject_link(workspace)
        return workspace

    def cleanup_abandoned(self, *, older_than_seconds: float) -> CleanupReport:
        if older_than_seconds <= 0:
            raise ValueError("older_than_seconds must be positive")
        self._validate_root()
        cutoff = self._clock() - older_than_seconds
        removed: list[str] = []
        rejected: list[str] = []
        for candidate in self.root.iterdir():
            if (
                JOB_ID_PATTERN.fullmatch(candidate.name) is None
                or not candidate.is_dir()
                or self._is_link(candidate)
            ):
                rejected.append(candidate.name)
                continue
            if candidate.stat(follow_symlinks=False).st_mtime > cutoff:
                continue
            self.cleanup(candidate.name)
            removed.append(candidate.name)
        return CleanupReport(tuple(sorted(removed)), tuple(sorted(rejected)))

    def _validate_root(self) -> None:
        if not self.root.is_dir():
            raise WorkspaceError("OCR workspace root is missing")
        self._reject_link(self.root)

    def _assert_direct_child(self, path: Path) -> None:
        if path.parent != self.root or os.path.commonpath((self.root, path)) != str(
            self.root
        ):
            raise WorkspaceError("OCR workspace escaped its configured root")

    @staticmethod
    def _validate_job_id(job_id: str) -> None:
        if JOB_ID_PATTERN.fullmatch(job_id) is None:
            raise ValueError("invalid OCR job_id")

    @classmethod
    def _reject_link(cls, path: Path) -> None:
        if cls._is_link(path):
            raise WorkspaceError("links and reparse points are not allowed")

    @staticmethod
    def _is_link(path: Path) -> bool:
        return path.is_symlink() or (
            hasattr(path, "is_junction") and path.is_junction()
        )

    @classmethod
    def _reject_tree_links(cls, root: Path) -> None:
        for directory, directories, filenames in os.walk(
            root,
            topdown=True,
            followlinks=False,
        ):
            for name in (*directories, *filenames):
                cls._reject_link(Path(directory) / name)
