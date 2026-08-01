import tempfile
import unittest
from pathlib import Path

from cd_assist.errors import FileParseError
from cd_assist.tools import (
    LineSnippet,
    MatchType,
    SearchResult,
    read_file,
    search_files,
)


class ReadFileTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.temporary_root = Path(self.temporary_directory.name)
        self.workspace = self.temporary_root / "workspace"
        self.workspace.mkdir()

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_reads_java_file(self):
        java_file = self.workspace / "Example.java"
        java_file.write_text("public class Example {}", encoding="utf-8")

        result = read_file(self.workspace, "Example.java")

        self.assertEqual("public class Example {}", result["content"])
        self.assertFalse(result["truncated"])

    def test_accepts_uppercase_java_extension(self):
        java_file = self.workspace / "Example.JAVA"
        java_file.write_text("public class Example {}", encoding="utf-8")

        result = read_file(self.workspace, "Example.JAVA")

        self.assertEqual("public class Example {}", result["content"])

    def test_rejects_path_outside_workspace(self):
        outside_file = self.temporary_root / "Outside.java"
        outside_file.write_text("public class Outside {}", encoding="utf-8")

        with self.assertRaisesRegex(FileParseError, "outside the workspace"):
            read_file(self.workspace, "../Outside.java")

    def test_rejects_missing_file(self):
        with self.assertRaisesRegex(FileParseError, "not a file"):
            read_file(self.workspace, "Missing.java")

    def test_rejects_directory(self):
        directory = self.workspace / "Directory.java"
        directory.mkdir()

        with self.assertRaisesRegex(FileParseError, "not a file"):
            read_file(self.workspace, "Directory.java")

    def test_rejects_non_java_file(self):
        text_file = self.workspace / "notes.txt"
        text_file.write_text("not Java", encoding="utf-8")

        with self.assertRaisesRegex(FileParseError, "not a .java file"):
            read_file(self.workspace, "notes.txt")

    def test_rejects_invalid_utf8(self):
        java_file = self.workspace / "Invalid.java"
        java_file.write_bytes(b"\xff\xfe")

        with self.assertRaisesRegex(FileParseError, "not valid UTF-8"):
            read_file(self.workspace, "Invalid.java")

    def test_truncates_content_above_limit(self):
        java_file = self.workspace / "Large.java"
        java_file.write_text("abcdef", encoding="utf-8")

        result = read_file(self.workspace, "Large.java", max_char=5)

        self.assertEqual("abcde", result["content"])
        self.assertTrue(result["truncated"])

    def test_does_not_truncate_content_at_limit(self):
        java_file = self.workspace / "Exact.java"
        java_file.write_text("abcde", encoding="utf-8")

        result = read_file(self.workspace, "Exact.java", max_char=5)

        self.assertEqual("abcde", result["content"])
        self.assertFalse(result["truncated"])


class SearchFilesTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temporary_directory.name) / "workspace"
        self.workspace.mkdir()

    def tearDown(self):
        self.temporary_directory.cleanup()

    def write_java_file(self, relative_path, content):
        target = self.workspace / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def test_searches_relative_file_paths_case_insensitively(self):
        self.write_java_file(
            "service/UserService.java",
            "public class AccountManager {}",
        )

        results = search_files(self.workspace, "userservice")

        self.assertIn(
            SearchResult(
                path="service/UserService.java",
                match_type=MatchType.PATH,
            ),
            results,
        )

    def test_accepts_workspace_as_a_string_path(self):
        self.write_java_file("UserService.java", "public class Example {}")

        results = search_files(str(self.workspace), "UserService")

        self.assertEqual(1, len(results))
        self.assertEqual("UserService.java", results[0].path)

    def test_searches_file_contents_case_insensitively(self):
        self.write_java_file(
            "service/AccountManager.java",
            "public class AccountManager {\n    void validateUser() {}\n}",
        )

        results = search_files(self.workspace, "VALIDATEUSER")

        self.assertEqual(
            [
                SearchResult(
                    path="service/AccountManager.java",
                    match_type=MatchType.CONTENT,
                    line_snippet=LineSnippet(
                        line=2,
                        snippet="    void validateUser() {}\n",
                    ),
                )
            ],
            results,
        )

    def test_returns_path_and_content_matches_for_the_same_file(self):
        self.write_java_file(
            "UserService.java",
            "public class UserService {}\n",
        )

        results = search_files(self.workspace, "UserService")

        self.assertEqual(
            [MatchType.PATH, MatchType.CONTENT],
            [result.match_type for result in results],
        )

    def test_returns_results_in_stable_path_order(self):
        self.write_java_file("Zebra.java", "class Zebra { void match() {} }")
        self.write_java_file("Alpha.java", "class Alpha { void match() {} }")

        results = search_files(self.workspace, "match")

        self.assertEqual(
            ["Alpha.java", "Zebra.java"],
            [result.path for result in results],
        )

    def test_caps_combined_search_results(self):
        self.write_java_file(
            "Match.java",
            "class Match {\n    // match one\n    // match two\n}\n",
        )

        results = search_files(self.workspace, "match", max_results=2)

        self.assertEqual(2, len(results))

    def test_caps_content_snippet_length(self):
        self.write_java_file("Example.java", "prefix-search-query-suffix\n")

        results = search_files(
            self.workspace,
            "search-query",
            max_snippet_chars=10,
        )

        self.assertEqual("prefix-sea", results[0].line_snippet.snippet)

    def test_ignores_non_java_files(self):
        text_file = self.workspace / "notes.txt"
        text_file.write_text("search-query", encoding="utf-8")

        results = search_files(self.workspace, "search-query")

        self.assertEqual([], results)

    def test_searches_uppercase_java_extension(self):
        self.write_java_file("Uppercase.JAVA", "class Uppercase {}")

        results = search_files(self.workspace, "Uppercase")

        self.assertEqual(
            [MatchType.PATH, MatchType.CONTENT],
            [result.match_type for result in results],
        )

    def test_rejects_missing_workspace(self):
        missing_workspace = self.workspace / "missing"

        with self.assertRaisesRegex(FileParseError, "not a directory"):
            search_files(missing_workspace, "Example")

    def test_rejects_empty_query(self):
        with self.assertRaisesRegex(FileParseError, "cannot be empty"):
            search_files(self.workspace, "   ")

    def test_rejects_non_positive_result_limit(self):
        with self.assertRaisesRegex(FileParseError, "max_results"):
            search_files(self.workspace, "Example", max_results=0)

    def test_rejects_non_positive_snippet_limit(self):
        with self.assertRaisesRegex(FileParseError, "max_snippet_chars"):
            search_files(self.workspace, "Example", max_snippet_chars=0)

    def test_reports_invalid_utf8_file(self):
        invalid_file = self.workspace / "Invalid.java"
        invalid_file.write_bytes(b"\xff\xfe")

        with self.assertRaisesRegex(FileParseError, "not valid UTF-8: Invalid.java"):
            search_files(self.workspace, "Example")


if __name__ == "__main__":
    unittest.main()
