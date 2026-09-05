from enum import Enum
from pathlib import Path, PurePosixPath, PureWindowsPath


class WorkspaceErrorReason(str, Enum):
    INVALID_ROOT = "invalid_root"
    INVALID_PATH = "invalid_path"
    OUTSIDE_WORKSPACE = "outside_workspace"
    MISSING = "missing"
    NOT_FILE = "not_file"
    CONTENT_CHANGED = "content_changed"
    FILE_TOO_LARGE = "file_too_large"
    INVALID_UTF8 = "invalid_utf8"
    IO_ERROR = "io_error"


class WorkspaceError(ValueError):
    def __init__(self, reason: WorkspaceErrorReason, message: str):
        self.reason = reason
        super().__init__(message)


class Workspace:
    """A live, workspace-scoped view of safe filesystem operations.

    Only the canonical root is cached. File existence, contents, symlinks, and
    directory listings are inspected again for every operation.
    """

    def __init__(self, root: str | Path):
        if not isinstance(root, (str, Path)):
            raise TypeError("workspace must be a string or Path")
        if isinstance(root, str) and not root.strip():
            raise WorkspaceError(
                WorkspaceErrorReason.INVALID_ROOT,
                "Workspace must not be empty.",
            )

        resolved_root = Path(root).resolve()
        if not resolved_root.is_dir():
            raise WorkspaceError(
                WorkspaceErrorReason.INVALID_ROOT,
                "Workspace does not exist or is not a directory.",
            )
        self.root = resolved_root

    def resolve(self, relative_path: str | Path) -> Path:
        normalized = self.normalize_relative_path(relative_path)
        destination = (self.root / normalized).resolve()
        if not destination.is_relative_to(self.root):
            raise WorkspaceError(
                WorkspaceErrorReason.OUTSIDE_WORKSPACE,
                "Path is outside the workspace.",
            )
        return destination

    def relative_path(self, path: str | Path) -> str:
        resolved = Path(path).resolve()
        if not resolved.is_relative_to(self.root):
            raise WorkspaceError(
                WorkspaceErrorReason.OUTSIDE_WORKSPACE,
                "Path is outside the workspace.",
            )
        return resolved.relative_to(self.root).as_posix()

    def exists(self, relative_path: str | Path) -> bool:
        return self.resolve(relative_path).exists()

    def is_file(self, relative_path: str | Path) -> bool:
        return self.resolve(relative_path).is_file()

    def is_dir(self, relative_path: str | Path) -> bool:
        return self.resolve(relative_path).is_dir()

    def require_file(self, relative_path: str | Path) -> Path:
        path = self.resolve(relative_path)
        if not path.exists():
            raise WorkspaceError(
                WorkspaceErrorReason.MISSING,
                f"File does not exist: {self.normalize_relative_path(relative_path)}",
            )
        if not path.is_file():
            raise WorkspaceError(
                WorkspaceErrorReason.NOT_FILE,
                f"Path is not a file: {self.normalize_relative_path(relative_path)}",
            )
        return path

    def read_exact(self, relative_path: str | Path, *, max_bytes: int) -> str:
        if not isinstance(max_bytes, int) or max_bytes <= 0:
            raise ValueError("Maximum file size must be a positive integer.")
        path = self.require_file(relative_path)
        try:
            if path.stat().st_size > max_bytes:
                raise WorkspaceError(
                    WorkspaceErrorReason.FILE_TOO_LARGE,
                    f"File exceeds the maximum size of {max_bytes} bytes.",
                )
            with path.open("r", encoding="utf-8", newline="") as file:
                return file.read()
        except UnicodeDecodeError as error:
            raise WorkspaceError(
                WorkspaceErrorReason.INVALID_UTF8,
                f"File is not valid UTF-8: {self.normalize_relative_path(relative_path)}",
            ) from error
        except OSError as error:
            raise WorkspaceError(
                WorkspaceErrorReason.IO_ERROR,
                f"Could not read file: {self.normalize_relative_path(relative_path)}: {error}",
            ) from error

    def read_bounded(self, relative_path: str | Path, *, max_chars: int) -> tuple[str, bool]:
        if not isinstance(max_chars, int) or max_chars <= 0:
            raise ValueError("Maximum character count must be a positive integer.")
        path = self.require_file(relative_path)
        try:
            with path.open("r", encoding="utf-8") as file:
                content = file.read(max_chars + 1)
        except UnicodeDecodeError as error:
            raise WorkspaceError(
                WorkspaceErrorReason.INVALID_UTF8,
                f"File is not valid UTF-8: {self.normalize_relative_path(relative_path)}",
            ) from error
        except OSError as error:
            raise WorkspaceError(
                WorkspaceErrorReason.IO_ERROR,
                f"Could not read file: {self.normalize_relative_path(relative_path)}: {error}",
            ) from error
        return content[:max_chars], len(content) > max_chars

    def files(self, *, under: str | Path = ".", suffix: str | None = None) -> list[Path]:
        root = self.root if str(under).strip() == "." else self.resolve(under)
        if not root.is_dir():
            return []
        normalized_suffix = suffix.lower() if suffix is not None else None
        try:
            candidates = sorted(
                (path for path in root.rglob("*") if path.is_file()),
                key=lambda path: path.relative_to(self.root).as_posix(),
            )
        except OSError as error:
            raise WorkspaceError(
                WorkspaceErrorReason.IO_ERROR,
                f"Could not search workspace: {error}",
            ) from error

        results = []
        for candidate in candidates:
            resolved = candidate.resolve()
            if not resolved.is_relative_to(self.root):
                raise WorkspaceError(
                    WorkspaceErrorReason.OUTSIDE_WORKSPACE,
                    "Search path is outside the workspace.",
                )
            if normalized_suffix is None or resolved.suffix.lower() == normalized_suffix:
                results.append(resolved)
        return results

    @staticmethod
    def normalize_relative_path(raw_path: str | Path) -> str:
        if not isinstance(raw_path, (str, Path)):
            raise TypeError("Path must be a string or Path.")
        normalized = str(raw_path).strip().replace("\\", "/")
        if not normalized:
            raise WorkspaceError(
                WorkspaceErrorReason.INVALID_PATH,
                "Path must not be empty.",
            )
        posix_path = PurePosixPath(normalized)
        if (
            posix_path.is_absolute()
            or PureWindowsPath(normalized).is_absolute()
            or ".." in posix_path.parts
            or posix_path.as_posix() == "."
        ):
            raise WorkspaceError(
                WorkspaceErrorReason.INVALID_PATH,
                "Path must be relative and must not contain parent traversal.",
            )
        return posix_path.as_posix()

    def create_exclusive(self, relative_path: str, content: str) -> Path:
        destination = self.resolve(relative_path)
        parent = destination.parent

        parent.mkdir(parents=True, exist_ok=True)

        resolved_again = self.resolve(relative_path)

        if resolved_again != destination:
            raise WorkspaceError(
                WorkspaceErrorReason.OUTSIDE_WORKSPACE,
                "Destination changed while preparing it for creation.",
            )

        with destination.open(
            "x",
            encoding="utf-8",
            newline="",
        ) as file:
            file.write(content)

        return destination

    def replace_existing(
        self,
        relative_path: str,
        expected_content: str,
        replacement_content: str,
        *,
        max_bytes: int,
    ) -> Path:
        destination = self.require_file(relative_path)

        current_content = self.read_exact(
            relative_path,
            max_bytes=max_bytes,
        )

        if current_content != expected_content:
            raise WorkspaceError(
                WorkspaceErrorReason.CONTENT_CHANGED,
                "File content changed before replacement.",
            )

        with destination.open(
            "w",
            encoding="utf-8",
            newline="",
        ) as file:
            file.write(replacement_content)

        return destination

def as_workspace(workspace: Workspace | str | Path) -> Workspace:
    return workspace if isinstance(workspace, Workspace) else Workspace(workspace)
