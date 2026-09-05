from pathlib import Path

from cd_assist.patches import (
    JAVA_PATCH_POLICY,
    PatchApplicationError,
    PatchApplicationReason,
    PatchApplicationStatus,
    PatchOperation,
    ValidatedPatch,
    PatchApplicationResult,
)
from cd_assist.workspace import (
    Workspace,
    WorkspaceError,
    WorkspaceErrorReason,
    as_workspace,
)

def apply_patch(
    workspace: Workspace | str | Path,
    patch: ValidatedPatch,
) -> PatchApplicationResult:
    try:
        workspace = as_workspace(workspace)
    except WorkspaceError as error:
        raise PatchApplicationError(
            PatchApplicationReason.UNSAFE_DESTINATION,
            "Application workspace is not safe.",
        ) from error

    _check_application_preconditions(workspace, patch)

    if patch.operation == PatchOperation.MODIFY:
        return _apply_modify_patch(workspace, patch)

    if patch.operation == PatchOperation.CREATE:
        return _apply_create_patch(workspace, patch)

def _check_application_preconditions(workspace: Workspace | str | Path, patch: ValidatedPatch,) -> None:
    """Recheck a validated patch immediately before application.

    v0.5.3a deliberately stops after preflight. Controlled writes begin in
    v0.5.3b and v0.5.3c.
    """
    if not isinstance(patch, ValidatedPatch):
        raise TypeError("Patch application requires a ValidatedPatch.")

    try:
        workspace = as_workspace(workspace)
        destination = workspace.resolve(patch.path)
    except WorkspaceError as error:
        raise PatchApplicationError(
            PatchApplicationReason.UNSAFE_DESTINATION,
            "Patch destination is not safe in the application workspace.",
        ) from error

    if destination != patch.destination:
        raise PatchApplicationError(
            PatchApplicationReason.UNSAFE_DESTINATION,
            "Validated patch destination does not belong to this workspace.",
        )

    if patch.operation == PatchOperation.CREATE:
        if destination.exists():
            raise PatchApplicationError(
                PatchApplicationReason.CREATE_DESTINATION_EXISTS,
                "CREATE destination now exists.",
            )
    else:
        if not destination.exists():
            raise PatchApplicationError(
                PatchApplicationReason.MODIFY_DESTINATION_MISSING,
                "MODIFY destination no longer exists.",
            )
        if not destination.is_file():
            raise PatchApplicationError(
                PatchApplicationReason.MODIFY_DESTINATION_NOT_FILE,
                "MODIFY destination is no longer a regular file.",
            )

        try:
            current_content = workspace.read_exact(
                patch.path,
                max_bytes=JAVA_PATCH_POLICY.max_existing_bytes,
            )
        except WorkspaceError as error:
            raise PatchApplicationError(
                PatchApplicationReason.UNSAFE_DESTINATION,
                "MODIFY destination could not be read safely.",
            ) from error

        if current_content != patch.expected_existing_content:
            raise PatchApplicationError(
                PatchApplicationReason.MODIFY_CONTENT_CHANGED,
                "MODIFY destination content changed after validation.",
            )

def _apply_create_patch(
    workspace: Workspace,
    patch: ValidatedPatch,
) -> PatchApplicationResult:
    try:
        destination = workspace.create_exclusive(
            patch.path,
            patch.proposed_content,
        )
    except FileExistsError as error:
        raise PatchApplicationError(
            PatchApplicationReason.CREATE_DESTINATION_EXISTS,
            "CREATE destination appeared before application.",
        ) from error
    except WorkspaceError as error:
        raise PatchApplicationError(
            PatchApplicationReason.UNSAFE_DESTINATION,
            "CREATE destination is no longer safe.",
        ) from error
    except OSError as error:
        raise PatchApplicationError(
            PatchApplicationReason.WRITE_FAILED,
            "Could not create the destination file.",
        ) from error

    try:
        resulting_content = workspace.read_exact(
            patch.path,
            max_bytes=JAVA_PATCH_POLICY.max_existing_bytes,
        )
    except WorkspaceError as error:
        raise PatchApplicationError(
            PatchApplicationReason.VERIFICATION_FAILED,
            "Created file could not be verified.",
        ) from error

    if resulting_content != patch.proposed_content:
        raise PatchApplicationError(
            PatchApplicationReason.VERIFICATION_FAILED,
            "Created file does not contain the proposed content.",
        )

    return PatchApplicationResult(
        operation=PatchOperation.CREATE,
        path=patch.path,
        status=PatchApplicationStatus.APPLIED,
        bytes_written=len(resulting_content.encode("utf-8")),
        previous_content=None,
        resulting_content=resulting_content,
    )

def _apply_modify_patch(
    workspace: Workspace,
    patch: ValidatedPatch,
) -> PatchApplicationResult:
    previous_content = patch.expected_existing_content

    try:
        workspace.replace_existing(
            patch.path,
            expected_content=previous_content,
            replacement_content=patch.proposed_content,
            max_bytes=JAVA_PATCH_POLICY.max_existing_bytes,
        )
    except WorkspaceError as error:
        reason_map = {
            WorkspaceErrorReason.MISSING: PatchApplicationReason.MODIFY_DESTINATION_MISSING,
            WorkspaceErrorReason.NOT_FILE: PatchApplicationReason.MODIFY_DESTINATION_NOT_FILE,
            WorkspaceErrorReason.CONTENT_CHANGED: PatchApplicationReason.MODIFY_CONTENT_CHANGED,
        }
        raise PatchApplicationError(
            reason_map.get(error.reason, PatchApplicationReason.UNSAFE_DESTINATION),
            "MODIFY destination failed its final write precondition.",
        ) from error
    except OSError as error:
        raise PatchApplicationError(
            PatchApplicationReason.WRITE_FAILED,
            "Could not replace the destination file.",
        ) from error

    try:
        resulting_content = workspace.read_exact(
            patch.path,
            max_bytes=JAVA_PATCH_POLICY.max_existing_bytes,
        )
    except WorkspaceError as error:
        raise PatchApplicationError(
            PatchApplicationReason.VERIFICATION_FAILED,
            "Modified file could not be verified.",
        ) from error

    if resulting_content != patch.proposed_content:
        raise PatchApplicationError(
            PatchApplicationReason.VERIFICATION_FAILED,
            "Modified file does not contain the proposed content.",
        )

    return PatchApplicationResult(
        operation=PatchOperation.MODIFY,
        path=patch.path,
        status=PatchApplicationStatus.APPLIED,
        bytes_written=len(resulting_content.encode("utf-8")),
        previous_content=previous_content,
        resulting_content=resulting_content,
    )
