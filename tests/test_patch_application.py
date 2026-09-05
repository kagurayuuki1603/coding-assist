import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch as mock_patch

from cd_assist.patch_application import apply_patch
from cd_assist.patches import (
    PatchApplicationError,
    PatchApplicationReason,
    PatchApplicationResult,
    PatchApplicationStatus,
    PatchOperation,
    PatchValidationPolicy,
    ProposedPatch,
    validate_patch,
)
from cd_assist.workspace import Workspace, WorkspaceError, WorkspaceErrorReason


class PatchApplicationErrorTests(unittest.TestCase):
    def test_retains_machine_readable_reason_and_message(self):
        error = PatchApplicationError(
            PatchApplicationReason.MODIFY_CONTENT_CHANGED,
            "The destination changed after validation.",
        )

        self.assertEqual(
            PatchApplicationReason.MODIFY_CONTENT_CHANGED,
            error.reason,
        )
        self.assertEqual(
            "The destination changed after validation.",
            str(error),
        )

    def test_application_reasons_are_stable_and_unique(self):
        self.assertEqual(
            len(PatchApplicationReason),
            len({reason.value for reason in PatchApplicationReason}),
        )
        self.assertEqual(
            "verification_failed",
            PatchApplicationReason.VERIFICATION_FAILED.value,
        )


class PatchApplicationResultTests(unittest.TestCase):
    def make_result(self, **overrides):
        resulting_content = overrides.pop("resulting_content", "class Service {}")
        values = {
            "operation": PatchOperation.CREATE,
            "path": "src/main/java/com/example/Service.java",
            "status": PatchApplicationStatus.APPLIED,
            "bytes_written": len(resulting_content.encode("utf-8")),
            "previous_content": None,
            "resulting_content": resulting_content,
        }
        values.update(overrides)
        return PatchApplicationResult(**values)

    def test_accepts_verified_create_result(self):
        result = self.make_result()

        self.assertEqual(PatchApplicationStatus.APPLIED, result.status)
        self.assertIsNone(result.previous_content)

    def test_accepts_verified_modify_result_with_previous_content(self):
        result = self.make_result(
            operation=PatchOperation.MODIFY,
            previous_content="class Service {}",
            resulting_content="class Service { int value; }",
            bytes_written=len("class Service { int value; }".encode("utf-8")),
        )

        self.assertEqual(PatchOperation.MODIFY, result.operation)
        self.assertEqual("class Service {}", result.previous_content)

    def test_rejects_negative_or_inaccurate_byte_count(self):
        for byte_count in (-1, 1):
            with self.subTest(bytes_written=byte_count):
                with self.assertRaisesRegex(ValueError, "Bytes written"):
                    self.make_result(bytes_written=byte_count)

    def test_counts_utf8_bytes_instead_of_characters(self):
        result = self.make_result(resulting_content="class Café {}")

        self.assertEqual(
            len("class Café {}".encode("utf-8")),
            result.bytes_written,
        )

    def test_rejects_empty_resulting_content(self):
        with self.assertRaisesRegex(ValueError, "resulting content"):
            self.make_result(resulting_content="", bytes_written=0)

    def test_create_rejects_previous_content(self):
        with self.assertRaisesRegex(ValueError, "CREATE"):
            self.make_result(previous_content="class Service {}")

    def test_modify_requires_previous_content(self):
        with self.assertRaisesRegex(ValueError, "MODIFY"):
            self.make_result(operation=PatchOperation.MODIFY)

    def test_formats_application_summary_for_console(self):
        result = self.make_result()

        self.assertEqual(
            "Patch Applied\n"
            "Operation: create\n"
            "Path: src/main/java/com/example/Service.java\n"
            "Status: applied\n"
            "Bytes Written: 16",
            result.to_console_string(),
        )

    def test_console_summary_does_not_print_file_contents(self):
        result = self.make_result(
            operation=PatchOperation.MODIFY,
            previous_content="class OldSecret {}",
            resulting_content="class NewSecret {}",
            bytes_written=len("class NewSecret {}".encode("utf-8")),
        )

        output = result.to_console_string()

        self.assertNotIn("OldSecret", output)
        self.assertNotIn("NewSecret", output)


class ApplyPatchPreconditionTests(unittest.TestCase):
    def make_proposed_patch(self, **overrides):
        values = {
            "operation": PatchOperation.CREATE,
            "path": "src/Service.java",
            "expected_existing_content": None,
            "proposed_content": "class Service {}",
            "rationale": "Adds a service.",
            "applied": False,
        }
        values.update(overrides)
        return ProposedPatch(**values)

    def validate(self, workspace, patch):
        return validate_patch(
            workspace,
            patch,
            PatchValidationPolicy(frozenset({".java"})),
        )

    def assert_application_reason(self, reason, callback):
        with self.assertRaises(PatchApplicationError) as raised:
            callback()
        self.assertEqual(reason, raised.exception.reason)

    def test_rejects_raw_model_patch(self):
        with tempfile.TemporaryDirectory() as directory:
            proposed = self.make_proposed_patch()

            with self.assertRaisesRegex(TypeError, "ValidatedPatch"):
                apply_patch(directory, proposed)

    def test_create_writes_and_verifies_nested_file(self):
        with tempfile.TemporaryDirectory() as directory:
            validated = self.validate(directory, self.make_proposed_patch())

            result = apply_patch(directory, validated)

            self.assertEqual("class Service {}", validated.destination.read_text(encoding="utf-8"))
            self.assertEqual(PatchOperation.CREATE, result.operation)
            self.assertEqual(PatchApplicationStatus.APPLIED, result.status)
            self.assertEqual(validated.path, result.path)
            self.assertEqual(len(b"class Service {}"), result.bytes_written)
            self.assertIsNone(result.previous_content)
            self.assertEqual("class Service {}", result.resulting_content)

    def test_create_counts_utf8_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            content = "class Caf\u00e9 {}"
            validated = self.validate(
                directory,
                self.make_proposed_patch(proposed_content=content),
            )

            result = apply_patch(Path(directory), validated)

            self.assertEqual(len(content.encode("utf-8")), result.bytes_written)

    def test_rejects_patch_validated_for_another_workspace(self):
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            validated = self.validate(first, self.make_proposed_patch())

            self.assert_application_reason(
                PatchApplicationReason.UNSAFE_DESTINATION,
                lambda: apply_patch(second, validated),
            )

    def test_create_rejects_destination_that_appeared_after_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            validated = self.validate(directory, self.make_proposed_patch())
            validated.destination.parent.mkdir(parents=True)
            validated.destination.write_text("other content", encoding="utf-8")

            self.assert_application_reason(
                PatchApplicationReason.CREATE_DESTINATION_EXISTS,
                lambda: apply_patch(directory, validated),
            )
            self.assertEqual(
                "other content",
                validated.destination.read_text(encoding="utf-8"),
            )

    def test_modify_replaces_current_content_and_returns_evidence(self):
        existing = "class Service {}"
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "src/Service.java"
            destination.parent.mkdir()
            destination.write_text(existing, encoding="utf-8")
            proposed = self.make_proposed_patch(
                operation=PatchOperation.MODIFY,
                expected_existing_content=existing,
                proposed_content="class Service { int value; }",
            )
            validated = self.validate(directory, proposed)

            result = apply_patch(directory, validated)

            self.assertEqual(
                "class Service { int value; }",
                destination.read_text(encoding="utf-8"),
            )
            self.assertEqual(PatchOperation.MODIFY, result.operation)
            self.assertEqual(PatchApplicationStatus.APPLIED, result.status)
            self.assertEqual(existing, result.previous_content)
            self.assertEqual("class Service { int value; }", result.resulting_content)
            self.assertEqual(
                len("class Service { int value; }".encode("utf-8")),
                result.bytes_written,
            )

    def test_modify_rejects_destination_removed_after_validation(self):
        existing = "class Service {}"
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "Service.java"
            destination.write_text(existing, encoding="utf-8")
            validated = self.validate(
                directory,
                self.make_proposed_patch(
                    operation=PatchOperation.MODIFY,
                    path="Service.java",
                    expected_existing_content=existing,
                    proposed_content="class Service { int value; }",
                ),
            )
            destination.unlink()

            self.assert_application_reason(
                PatchApplicationReason.MODIFY_DESTINATION_MISSING,
                lambda: apply_patch(directory, validated),
            )

    def test_modify_rejects_directory_replacement_after_validation(self):
        existing = "class Service {}"
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "Service.java"
            destination.write_text(existing, encoding="utf-8")
            validated = self.validate(
                directory,
                self.make_proposed_patch(
                    operation=PatchOperation.MODIFY,
                    path="Service.java",
                    expected_existing_content=existing,
                    proposed_content="class Service { int value; }",
                ),
            )
            destination.unlink()
            destination.mkdir()

            self.assert_application_reason(
                PatchApplicationReason.MODIFY_DESTINATION_NOT_FILE,
                lambda: apply_patch(directory, validated),
            )

    def test_modify_rejects_content_changed_after_validation(self):
        existing = "class Service {}"
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "Service.java"
            destination.write_text(existing, encoding="utf-8")
            validated = self.validate(
                directory,
                self.make_proposed_patch(
                    operation=PatchOperation.MODIFY,
                    path="Service.java",
                    expected_existing_content=existing,
                    proposed_content="class Service { int value; }",
                ),
            )
            destination.write_text("class Service { int other; }", encoding="utf-8")

            self.assert_application_reason(
                PatchApplicationReason.MODIFY_CONTENT_CHANGED,
                lambda: apply_patch(directory, validated),
            )

    def test_rejects_symlink_escape_introduced_after_validation(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            source_directory = Path(directory) / "src"
            source_directory.mkdir()
            validated = self.validate(directory, self.make_proposed_patch())
            source_directory.rmdir()
            source_directory.symlink_to(outside, target_is_directory=True)

            self.assert_application_reason(
                PatchApplicationReason.UNSAFE_DESTINATION,
                lambda: apply_patch(directory, validated),
            )


class ApplyCreatePatchFailureTests(unittest.TestCase):
    def make_proposed_patch(self, **overrides):
        values = {
            "operation": PatchOperation.CREATE,
            "path": "src/Service.java",
            "expected_existing_content": None,
            "proposed_content": "class Service {}",
            "rationale": "Adds a service.",
            "applied": False,
        }
        values.update(overrides)
        return ProposedPatch(**values)

    def validate(self, workspace, proposed):
        return validate_patch(
            workspace,
            proposed,
            PatchValidationPolicy(frozenset({".java"})),
        )

    def assert_application_reason(self, reason, callback):
        with self.assertRaises(PatchApplicationError) as raised:
            callback()
        self.assertEqual(reason, raised.exception.reason)

    def make_validated_create(self, directory):
        workspace = Workspace(directory)
        validated = self.validate(workspace, self.make_proposed_patch())
        return workspace, validated

    def test_maps_exclusive_create_race_to_destination_exists(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace, validated = self.make_validated_create(directory)
            with mock_patch.object(
                workspace,
                "create_exclusive",
                side_effect=FileExistsError,
            ):
                self.assert_application_reason(
                    PatchApplicationReason.CREATE_DESTINATION_EXISTS,
                    lambda: apply_patch(workspace, validated),
                )

    def test_maps_unsafe_create_to_unsafe_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace, validated = self.make_validated_create(directory)
            error = WorkspaceError(
                WorkspaceErrorReason.OUTSIDE_WORKSPACE,
                "Destination escaped.",
            )
            with mock_patch.object(workspace, "create_exclusive", side_effect=error):
                self.assert_application_reason(
                    PatchApplicationReason.UNSAFE_DESTINATION,
                    lambda: apply_patch(workspace, validated),
                )

    def test_maps_operating_system_write_error_to_write_failed(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace, validated = self.make_validated_create(directory)
            with mock_patch.object(
                workspace,
                "create_exclusive",
                side_effect=OSError("disk full"),
            ):
                self.assert_application_reason(
                    PatchApplicationReason.WRITE_FAILED,
                    lambda: apply_patch(workspace, validated),
                )

    def test_rejects_content_that_does_not_match_after_write(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace, validated = self.make_validated_create(directory)
            with mock_patch.object(
                workspace,
                "read_exact",
                return_value="different content",
            ):
                self.assert_application_reason(
                    PatchApplicationReason.VERIFICATION_FAILED,
                    lambda: apply_patch(workspace, validated),
                )

    def test_maps_verification_read_error_to_verification_failed(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace, validated = self.make_validated_create(directory)
            error = WorkspaceError(
                WorkspaceErrorReason.IO_ERROR,
                "Could not read file.",
            )
            with mock_patch.object(workspace, "read_exact", side_effect=error):
                self.assert_application_reason(
                    PatchApplicationReason.VERIFICATION_FAILED,
                    lambda: apply_patch(workspace, validated),
                )


class ApplyModifyPatchTests(unittest.TestCase):
    def make_validated_modify(
        self,
        directory,
        *,
        existing="class Service {}",
        proposed="class Service { int value; }",
    ):
        workspace = Workspace(directory)
        destination = Path(directory) / "src/Service.java"
        destination.parent.mkdir()
        destination.write_text(existing, encoding="utf-8")
        patch = ProposedPatch(
            operation=PatchOperation.MODIFY,
            path="src/Service.java",
            expected_existing_content=existing,
            proposed_content=proposed,
            rationale="Adds the missing behavior.",
            applied=False,
        )
        validated = validate_patch(
            workspace,
            patch,
            PatchValidationPolicy(frozenset({".java"})),
        )
        return workspace, destination, validated

    def assert_application_reason(self, reason, callback):
        with self.assertRaises(PatchApplicationError) as raised:
            callback()
        self.assertEqual(reason, raised.exception.reason)

    def test_modify_replaces_complete_unicode_content_and_leaves_other_files_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            proposed = "class Caf\u00e9 { int value; }"
            workspace, destination, validated = self.make_validated_modify(
                directory,
                proposed=proposed,
            )
            unrelated = Path(directory) / "README.md"
            unrelated.write_text("leave me alone", encoding="utf-8")

            result = apply_patch(workspace, validated)

            self.assertEqual(proposed, destination.read_text(encoding="utf-8"))
            self.assertEqual(len(proposed.encode("utf-8")), result.bytes_written)
            self.assertEqual("leave me alone", unrelated.read_text(encoding="utf-8"))

    def test_content_changed_during_final_recheck_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace, destination, validated = self.make_validated_modify(directory)
            original_read = workspace.read_exact
            read_count = 0

            def change_before_final_recheck(path, *, max_bytes):
                nonlocal read_count
                read_count += 1
                if read_count == 2:
                    destination.write_text("changed concurrently", encoding="utf-8")
                return original_read(path, max_bytes=max_bytes)

            with mock_patch.object(workspace, "read_exact", side_effect=change_before_final_recheck):
                self.assert_application_reason(
                    PatchApplicationReason.MODIFY_CONTENT_CHANGED,
                    lambda: apply_patch(workspace, validated),
                )

            self.assertEqual("changed concurrently", destination.read_text(encoding="utf-8"))

    def test_maps_destination_disappearing_during_write_to_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace, _, validated = self.make_validated_modify(directory)
            error = WorkspaceError(WorkspaceErrorReason.MISSING, "File disappeared.")
            with mock_patch.object(workspace, "replace_existing", side_effect=error):
                self.assert_application_reason(
                    PatchApplicationReason.MODIFY_DESTINATION_MISSING,
                    lambda: apply_patch(workspace, validated),
                )

    def test_maps_directory_replacement_during_write_to_not_file(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace, _, validated = self.make_validated_modify(directory)
            error = WorkspaceError(WorkspaceErrorReason.NOT_FILE, "Not a file.")
            with mock_patch.object(workspace, "replace_existing", side_effect=error):
                self.assert_application_reason(
                    PatchApplicationReason.MODIFY_DESTINATION_NOT_FILE,
                    lambda: apply_patch(workspace, validated),
                )

    def test_maps_operating_system_replacement_error_to_write_failed(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace, _, validated = self.make_validated_modify(directory)
            with mock_patch.object(
                workspace,
                "replace_existing",
                side_effect=OSError("disk full"),
            ):
                self.assert_application_reason(
                    PatchApplicationReason.WRITE_FAILED,
                    lambda: apply_patch(workspace, validated),
                )

    def test_rejects_verification_content_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace, _, validated = self.make_validated_modify(directory)
            original_read = workspace.read_exact
            read_count = 0

            def mismatch_after_write(path, *, max_bytes):
                nonlocal read_count
                read_count += 1
                if read_count == 3:
                    return "unexpected content"
                return original_read(path, max_bytes=max_bytes)

            with mock_patch.object(workspace, "read_exact", side_effect=mismatch_after_write):
                self.assert_application_reason(
                    PatchApplicationReason.VERIFICATION_FAILED,
                    lambda: apply_patch(workspace, validated),
                )

    def test_maps_verification_read_error_to_verification_failed(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace, _, validated = self.make_validated_modify(directory)
            original_read = workspace.read_exact
            read_count = 0

            def fail_after_write(path, *, max_bytes):
                nonlocal read_count
                read_count += 1
                if read_count == 3:
                    raise WorkspaceError(WorkspaceErrorReason.IO_ERROR, "Read failed.")
                return original_read(path, max_bytes=max_bytes)

            with mock_patch.object(workspace, "read_exact", side_effect=fail_after_write):
                self.assert_application_reason(
                    PatchApplicationReason.VERIFICATION_FAILED,
                    lambda: apply_patch(workspace, validated),
                )

if __name__ == "__main__":
    unittest.main()
