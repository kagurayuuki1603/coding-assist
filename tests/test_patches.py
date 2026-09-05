import tempfile
import unittest
from pathlib import Path

from cd_assist.patches import (
    PatchOperation,
    PatchValidationError,
    PatchValidationPolicy,
    PatchValidationReason,
    ProposedPatch,
    ValidatedPatch,
    validate_patch,
)


class WorkspacePatchValidationTests(unittest.TestCase):
    def make_patch(self, **overrides):
        values = {
            "operation": PatchOperation.CREATE,
            "path": "src/main/java/com/example/Service.java",
            "expected_existing_content": None,
            "proposed_content": "class Service {}",
            "rationale": "Adds a small production class.",
            "applied": False,
        }
        values.update(overrides)
        return ProposedPatch(**values)

    def setUp(self):
        self.policy = PatchValidationPolicy(frozenset({"java"}), max_existing_bytes=100)

    def assert_reason(self, expected_reason, callback):
        with self.assertRaises(PatchValidationError) as raised:
            callback()
        self.assertEqual(expected_reason, raised.exception.reason)

    def test_validates_generic_production_file_create_without_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            patch = self.make_patch()

            result = validate_patch(directory, patch, self.policy)

            self.assertIsInstance(result, ValidatedPatch)
            self.assertEqual(patch.path, result.path)
            self.assertFalse(result.destination.exists())

    def test_validates_modify_with_exact_content_and_preserves_line_endings(self):
        existing = "class Service {\r\n}\r\n"
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "src/main/java/com/example/Service.java"
            destination.parent.mkdir(parents=True)
            with destination.open("w", encoding="utf-8", newline="") as file:
                file.write(existing)
            patch = self.make_patch(
                operation=PatchOperation.MODIFY,
                expected_existing_content=existing,
                proposed_content="class Service {\r\n    int value;\r\n}",
            )

            result = validate_patch(directory, patch, self.policy)

            self.assertEqual(existing, result.expected_existing_content)

    def test_reports_structured_reason_for_stale_content(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "src/main/java/com/example/Service.java"
            destination.parent.mkdir(parents=True)
            destination.write_text("class Service {}", encoding="utf-8")
            patch = self.make_patch(
                operation=PatchOperation.MODIFY,
                expected_existing_content="class Service { int oldValue; }",
                proposed_content="class Service { int newValue; }",
            )

            self.assert_reason(
                PatchValidationReason.STALE_CONTENT,
                lambda: validate_patch(directory, patch, self.policy),
            )

    def test_rejects_unsupported_extension_before_task_semantics(self):
        with tempfile.TemporaryDirectory() as directory:
            patch = self.make_patch(path="src/main/resources/config.xml")

            self.assert_reason(
                PatchValidationReason.UNSUPPORTED_FILE_TYPE,
                lambda: validate_patch(directory, patch, self.policy),
            )

    def test_rejects_existing_create_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "src/main/java/com/example/Service.java"
            destination.parent.mkdir(parents=True)
            destination.touch()

            self.assert_reason(
                PatchValidationReason.DESTINATION_EXISTS,
                lambda: validate_patch(directory, self.make_patch(), self.policy),
            )

    def test_rejects_missing_modify_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            patch = self.make_patch(
                operation=PatchOperation.MODIFY,
                expected_existing_content="class Service {}",
            )

            self.assert_reason(
                PatchValidationReason.DESTINATION_MISSING,
                lambda: validate_patch(directory, patch, self.policy),
            )

    def test_rejects_oversized_and_invalid_utf8_files(self):
        cases = (
            (b"x" * 101, PatchValidationReason.FILE_TOO_LARGE),
            (b"\xff", PatchValidationReason.INVALID_UTF8),
        )
        for content, reason in cases:
            with self.subTest(reason=reason), tempfile.TemporaryDirectory() as directory:
                destination = Path(directory) / "src/main/java/com/example/Service.java"
                destination.parent.mkdir(parents=True)
                destination.write_bytes(content)
                patch = self.make_patch(
                    operation=PatchOperation.MODIFY,
                    expected_existing_content="old",
                )

                self.assert_reason(
                    reason,
                    lambda: validate_patch(directory, patch, self.policy),
                )

    def test_rejects_symlink_escape(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            link = Path(directory) / "src"
            link.symlink_to(outside, target_is_directory=True)
            patch = self.make_patch(path="src/Service.java")

            self.assert_reason(
                PatchValidationReason.OUTSIDE_WORKSPACE,
                lambda: validate_patch(directory, patch, self.policy),
            )


if __name__ == "__main__":
    unittest.main()
