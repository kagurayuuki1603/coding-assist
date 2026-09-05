from dataclasses import dataclass
from enum import Enum
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator, StringConstraints


class PatchOperation(str, Enum):
    CREATE = "create"
    MODIFY = "modify"


class PatchValidationReason(str, Enum):
    INVALID_WORKSPACE = "invalid_workspace"
    OUTSIDE_WORKSPACE = "outside_workspace"
    UNSUPPORTED_FILE_TYPE = "unsupported_file_type"
    DESTINATION_EXISTS = "destination_exists"
    DESTINATION_MISSING = "destination_missing"
    DESTINATION_NOT_FILE = "destination_not_file"
    STALE_CONTENT = "stale_content"
    NO_CONTENT_CHANGE = "no_content_change"
    FILE_TOO_LARGE = "file_too_large"
    INVALID_UTF8 = "invalid_utf8"


class PatchValidationError(ValueError):
    def __init__(self, reason: PatchValidationReason, message: str):
        self.reason = reason
        super().__init__(message)


BoundedPatchText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=2_000),
]


class ProposedPatch(BaseModel):
    """Untrusted structured patch proposed by the model."""

    model_config = ConfigDict(extra="forbid")

    operation: PatchOperation
    path: str
    expected_existing_content: str | None
    proposed_content: BoundedPatchText
    rationale: BoundedPatchText
    applied: Literal[False]

    @model_validator(mode="after")
    def validate_operation_precondition(self):
        if self.operation == PatchOperation.CREATE and self.expected_existing_content is not None:
            raise ValueError("CREATE patch should not have any existing content.")
        if self.operation == PatchOperation.MODIFY and not self.expected_existing_content:
            raise ValueError("MODIFY patch must have existing content.")
        return self

    @field_validator("path")
    @classmethod
    def validate_relative_path(cls, path: str) -> str:
        return normalize_patch_path(path)

    @field_validator("proposed_content")
    @classmethod
    def reject_markdown_fences(cls, content: str) -> str:
        if "```" in content:
            raise ValueError("Proposed content must not contain Markdown fences.")
        return content

    def to_console_string(self) -> str:
        expected = self.expected_existing_content or "None"
        return (
            "Proposed Patch\n"
            f"Operation: {self.operation.value}\n"
            f"Path: {self.path}\n"
            f"Expected Existing Content: {expected}\n"
            f"Applied: {self.applied}\n\n"
            "Rationale\n"
            f"{self.rationale}\n\n"
            "Proposed Content\n"
            f"{self.proposed_content}"
        )


@dataclass(frozen=True)
class PatchValidationPolicy:
    allowed_extensions: frozenset[str]
    max_existing_bytes: int = 100_000

    def __post_init__(self):
        if self.max_existing_bytes <= 0:
            raise ValueError("Maximum existing file size must be positive.")
        normalized = frozenset(
            extension.lower() if extension.startswith(".") else f".{extension.lower()}"
            for extension in self.allowed_extensions
        )
        if not normalized:
            raise ValueError("At least one file extension must be allowed.")
        object.__setattr__(self, "allowed_extensions", normalized)


@dataclass(frozen=True)
class ValidatedPatch:
    """A patch that has passed deterministic workspace-policy validation."""

    operation: PatchOperation
    path: str
    destination: Path
    expected_existing_content: str | None
    proposed_content: str
    rationale: str
    applied: Literal[False] = False

    def to_console_string(self) -> str:
        return ProposedPatch(
            operation=self.operation,
            path=self.path,
            expected_existing_content=self.expected_existing_content,
            proposed_content=self.proposed_content,
            rationale=self.rationale,
            applied=False,
        ).to_console_string()


JAVA_PATCH_POLICY = PatchValidationPolicy(allowed_extensions=frozenset({".java"}))


def validate_patch(
    workspace: str | Path,
    patch: ProposedPatch,
    policy: PatchValidationPolicy,
) -> ValidatedPatch:
    workspace_path = Path(workspace).resolve()
    if not workspace_path.is_dir():
        raise PatchValidationError(
            PatchValidationReason.INVALID_WORKSPACE,
            "Workspace does not exist or is not a directory.",
        )

    destination = (workspace_path / patch.path).resolve()
    if not destination.is_relative_to(workspace_path):
        raise PatchValidationError(
            PatchValidationReason.OUTSIDE_WORKSPACE,
            "Patch destination is outside the workspace.",
        )

    if destination.suffix.lower() not in policy.allowed_extensions:
        raise PatchValidationError(
            PatchValidationReason.UNSUPPORTED_FILE_TYPE,
            f"Unsupported file type: {destination.suffix or '(none)'}",
        )

    if patch.operation == PatchOperation.CREATE:
        if destination.exists():
            raise PatchValidationError(
                PatchValidationReason.DESTINATION_EXISTS,
                "CREATE patch destination already exists.",
            )
    else:
        if not destination.exists():
            raise PatchValidationError(
                PatchValidationReason.DESTINATION_MISSING,
                "MODIFY patch destination does not exist.",
            )
        if not destination.is_file():
            raise PatchValidationError(
                PatchValidationReason.DESTINATION_NOT_FILE,
                "MODIFY patch destination is not a file.",
            )

        current_content = read_workspace_file_exact(
            workspace_path,
            patch.path,
            max_bytes=policy.max_existing_bytes,
        )
        if current_content != patch.expected_existing_content:
            raise PatchValidationError(
                PatchValidationReason.STALE_CONTENT,
                "File content has changed since the patch was generated.",
            )
        if patch.proposed_content == current_content:
            raise PatchValidationError(
                PatchValidationReason.NO_CONTENT_CHANGE,
                "Proposed content should be different from the existing content.",
            )

    return ValidatedPatch(
        operation=patch.operation,
        path=patch.path,
        destination=destination,
        expected_existing_content=patch.expected_existing_content,
        proposed_content=patch.proposed_content,
        rationale=patch.rationale,
    )


def normalize_patch_path(raw_path: str) -> str:
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ValueError("File path must be a non-empty string.")
    normalized = raw_path.strip().replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or PureWindowsPath(normalized).is_absolute() or ".." in path.parts:
        raise ValueError("File path must be relative to the workspace.")
    return path.as_posix()


def read_workspace_file_exact(
    workspace: str | Path,
    requested_path: str,
    *,
    max_bytes: int,
) -> str:
    workspace_path = Path(workspace).resolve()
    file_path = (workspace_path / normalize_patch_path(requested_path)).resolve()

    if not file_path.is_relative_to(workspace_path):
        raise PatchValidationError(
            PatchValidationReason.OUTSIDE_WORKSPACE,
            "File is outside the workspace.",
        )
    if file_path.stat().st_size > max_bytes:
        raise PatchValidationError(
            PatchValidationReason.FILE_TOO_LARGE,
            f"File exceeds the maximum size of {max_bytes} bytes.",
        )
    try:
        with file_path.open("r", encoding="utf-8", newline="") as file:
            return file.read()
    except UnicodeDecodeError as error:
        raise PatchValidationError(
            PatchValidationReason.INVALID_UTF8,
            f"File is not valid UTF-8: {requested_path}",
        ) from error
