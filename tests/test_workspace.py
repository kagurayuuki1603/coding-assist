import tempfile
import unittest
from pathlib import Path

from cd_assist.workspace import (
    Workspace,
    WorkspaceError,
    WorkspaceErrorReason,
    as_workspace,
)


class WorkspaceTests(unittest.TestCase):
    def test_canonicalizes_root_without_caching_file_state(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Workspace(directory)
            destination = Path(directory) / "Example.java"

            self.assertFalse(workspace.exists("Example.java"))
            destination.write_text("class Example {}", encoding="utf-8")
            self.assertTrue(workspace.exists("Example.java"))
            self.assertEqual(Path(directory).resolve(), workspace.root)

    def test_resolves_relative_path_inside_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Workspace(directory)

            result = workspace.resolve(r"src\main\Example.java")

            self.assertEqual(
                (Path(directory) / "src/main/Example.java").resolve(),
                result,
            )

    def test_rejects_parent_traversal_and_symlink_escape(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            workspace = Workspace(directory)
            (Path(directory) / "linked").symlink_to(outside, target_is_directory=True)

            for path in ("../Example.java", "linked/Example.java"):
                with self.subTest(path=path), self.assertRaises(WorkspaceError):
                    workspace.resolve(path)

    def test_reads_exact_content_and_preserves_line_endings(self):
        content = "class Example {\r\n}\r\n"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "Example.java"
            with path.open("w", encoding="utf-8", newline="") as file:
                file.write(content)

            result = Workspace(directory).read_exact("Example.java", max_bytes=100)

            self.assertEqual(content, result)

    def test_reads_bounded_content_and_reports_truncation(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "Example.java").write_text("abcdef", encoding="utf-8")

            content, truncated = Workspace(directory).read_bounded(
                "Example.java",
                max_chars=3,
            )

            self.assertEqual("abc", content)
            self.assertTrue(truncated)

    def test_enumerates_supported_files_in_stable_order(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "src").mkdir()
            (root / "src/B.java").touch()
            (root / "src/A.JAVA").touch()
            (root / "src/ignored.txt").touch()

            results = Workspace(directory).files(under="src", suffix=".java")

            self.assertEqual(
                [(root / "src/A.JAVA").resolve(), (root / "src/B.java").resolve()],
                results,
            )

    def test_reports_structured_error_for_invalid_utf8(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "Example.java").write_bytes(b"\xff")

            with self.assertRaises(WorkspaceError) as raised:
                Workspace(directory).read_exact("Example.java", max_bytes=100)

            self.assertEqual(WorkspaceErrorReason.INVALID_UTF8, raised.exception.reason)

    def test_as_workspace_reuses_existing_instance(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Workspace(directory)

            self.assertIs(workspace, as_workspace(workspace))


if __name__ == "__main__":
    unittest.main()
