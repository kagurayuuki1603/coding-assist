import json
import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from cd_assist.test_generation import (
    BuildTool,
    FrameworkDiscoveryError,
    MAX_DISCOVERY_EVIDENCE_PATHS,
    TestDiscoveryStatus,
    TestFramework,
    TestFrameworkDiscovery,
    contains_all,
    contains_any,
    discover_build_tool,
    discover_source_roots,
    discover_test_files,
    discover_test_framework,
    discover_test_roots,
    inspect_test_framework_evidence,
    read_discovery_file,
)


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "discovery"


class TestFrameworkDiscoveryTests(unittest.TestCase):
    def make_discovery(self, **overrides):
        values = {
            "test_framework": TestFramework.JUNIT5,
            "build_tool": BuildTool.MAVEN,
            "build_reason": None,
            "source_roots": ["src/main/java"],
            "test_roots": ["src/test/java"],
            "evidence_paths": ["pom.xml"],
            "test_status": TestDiscoveryStatus.DISCOVERED,
        }
        values.update(overrides)
        return TestFrameworkDiscovery(**values)

    def test_accepts_discovered_framework_with_supporting_evidence(self):
        discovery = self.make_discovery()

        self.assertEqual(TestFramework.JUNIT5, discovery.test_framework)
        self.assertEqual(BuildTool.MAVEN, discovery.build_tool)
        self.assertIsNone(discovery.test_reason)

    def test_rejects_unknown_framework_as_discovered(self):
        with self.assertRaisesRegex(ValueError, "Unknown Framework"):
            self.make_discovery(test_framework=TestFramework.UNKNOWN)

    def test_rejects_discovered_framework_without_evidence(self):
        with self.assertRaisesRegex(ValueError, "requires evidence paths"):
            self.make_discovery(evidence_paths=[])

    def test_known_framework_requires_a_test_root(self):
        with self.assertRaisesRegex(ValueError, "requires test roots"):
            self.make_discovery(test_roots=[])

    def test_insufficient_evidence_requires_a_reason(self):
        with self.assertRaisesRegex(ValueError, "requires a reason"):
            self.make_discovery(
                test_framework=TestFramework.UNKNOWN,
                build_tool=BuildTool.UNKNOWN,
                build_reason="No supported build configuration was found.",
                test_roots=[],
                evidence_paths=[],
                test_status=TestDiscoveryStatus.INSUFFICIENT_EVIDENCE,
            )

    def test_accepts_explicit_insufficient_evidence_result(self):
        discovery = self.make_discovery(
            test_framework=TestFramework.UNKNOWN,
            build_tool=BuildTool.UNKNOWN,
            build_reason="No supported build configuration was found.",
            test_roots=[],
            evidence_paths=[],
            test_status=TestDiscoveryStatus.INSUFFICIENT_EVIDENCE,
            test_reason="No supported build configuration or existing tests were found.",
        )

        self.assertEqual(TestDiscoveryStatus.INSUFFICIENT_EVIDENCE, discovery.test_status)

    def test_conflicting_evidence_requires_a_reason(self):
        with self.assertRaisesRegex(ValueError, "requires reason"):
            self.make_discovery(
                test_framework=TestFramework.UNKNOWN,
                evidence_paths=["pom.xml", "src/test/java/ExampleTest.java"],
                test_status=TestDiscoveryStatus.CONFLICTING_EVIDENCE,
            )

    def test_conflicting_evidence_requires_an_evidence_path(self):
        with self.assertRaisesRegex(ValueError, "requires evidence"):
            self.make_discovery(
                test_framework=TestFramework.UNKNOWN,
                evidence_paths=[],
                test_status=TestDiscoveryStatus.CONFLICTING_EVIDENCE,
                test_reason="The build file and test imports disagree.",
            )

    def test_accepts_conflicting_evidence_result(self):
        discovery = self.make_discovery(
            test_framework=TestFramework.UNKNOWN,
            evidence_paths=["pom.xml", "src/test/java/ExampleTest.java"],
            test_status=TestDiscoveryStatus.CONFLICTING_EVIDENCE,
            test_reason="The build file declares JUnit 5 but the test imports JUnit 4.",
        )

        self.assertEqual(TestDiscoveryStatus.CONFLICTING_EVIDENCE, discovery.test_status)

    def test_normalizes_whitespace_and_windows_separators(self):
        discovery = self.make_discovery(
            source_roots=["  src\\main\\java  "],
            test_roots=["src\\test\\java"],
        )

        self.assertEqual(["src/main/java"], discovery.source_roots)
        self.assertEqual(["src/test/java"], discovery.test_roots)

    def test_rejects_posix_absolute_path(self):
        with self.assertRaisesRegex(ValueError, "must be relative"):
            self.make_discovery(source_roots=["/repo/src/main/java"])

    def test_rejects_windows_absolute_path(self):
        with self.assertRaisesRegex(ValueError, "must be relative"):
            self.make_discovery(source_roots=[r"C:\repo\src\main\java"])

    def test_rejects_parent_path_traversal(self):
        with self.assertRaisesRegex(ValueError, "parent traversal"):
            self.make_discovery(source_roots=["../src/main/java"])

    def test_rejects_duplicate_paths_after_normalization(self):
        with self.assertRaisesRegex(ValueError, "must be unique"):
            self.make_discovery(
                evidence_paths=["src/test/ExampleTest.java", r"src\test\ExampleTest.java"]
            )

    def test_rejects_blank_and_overlong_paths(self):
        for invalid_path in ["   ", "a" * 501]:
            with self.subTest(invalid_path=invalid_path[:20]):
                with self.assertRaises(ValidationError):
                    self.make_discovery(source_roots=[invalid_path])

    def test_rejects_too_many_paths(self):
        with self.assertRaises(ValidationError):
            self.make_discovery(
                evidence_paths=[f"evidence-{index}.xml" for index in range(11)]
            )

    def test_rejects_blank_and_overlong_reasons(self):
        for invalid_reason in ["   ", "r" * 2_001]:
            with self.subTest(reason_length=len(invalid_reason)):
                with self.assertRaises(ValidationError):
                    self.make_discovery(
                        test_framework=TestFramework.UNKNOWN,
                        build_tool=BuildTool.UNKNOWN,
                        build_reason="No supported build configuration was found.",
                        test_roots=[],
                        evidence_paths=[],
                        test_status=TestDiscoveryStatus.INSUFFICIENT_EVIDENCE,
                        test_reason=invalid_reason,
                    )

    def test_rejects_extra_fields(self):
        with self.assertRaises(ValidationError):
            self.make_discovery(confidence="high")

    def test_unknown_build_tool_requires_a_reason(self):
        with self.assertRaisesRegex(ValueError, "build tool requires a reason"):
            self.make_discovery(
                build_tool=BuildTool.UNKNOWN,
                test_framework=TestFramework.UNKNOWN,
                test_roots=[],
                evidence_paths=[],
                test_status=TestDiscoveryStatus.INSUFFICIENT_EVIDENCE,
                test_reason="No test framework was found.",
            )

    def test_conflicting_build_tool_requires_a_reason(self):
        with self.assertRaisesRegex(ValueError, "build tool requires a reason"):
            self.make_discovery(build_tool=BuildTool.CONFLICTING)


class DiscoveryFixtureDefinitionTests(unittest.TestCase):
    EXPECTED_FIXTURES = {
        "maven-junit5": {
            "build_tool": "maven",
            "test_framework": "junit5",
            "status": "discovered",
            "required_files": ["pom.xml", "src/main/java/com/example/Calculator.java", "src/test/java/com/example/CalculatorTest.java"],
        },
        "gradle-junit4": {
            "build_tool": "gradle",
            "test_framework": "junit4",
            "status": "discovered",
            "required_files": ["build.gradle", "src/main/java/com/example/SlugFormatter.java", "src/test/java/com/example/SlugFormatterTest.java"],
        },
        "unknown": {
            "build_tool": "unknown",
            "test_framework": "unknown",
            "status": "insufficient_evidence",
            "required_files": ["src/main/java/com/example/UnconfiguredService.java"],
        },
        "conflicting": {
            "build_tool": "maven",
            "test_framework": "unknown",
            "status": "conflicting_evidence",
            "required_files": ["pom.xml", "src/main/java/com/example/Counter.java", "src/test/java/com/example/CounterTest.java"],
        },
    }

    def test_fixture_expectations_and_required_files_are_complete(self):
        self.assertEqual(set(self.EXPECTED_FIXTURES), {path.name for path in FIXTURE_ROOT.iterdir() if path.is_dir()})

        for fixture_name, expected in self.EXPECTED_FIXTURES.items():
            with self.subTest(fixture=fixture_name):
                fixture_path = FIXTURE_ROOT / fixture_name
                metadata = json.loads(
                    (fixture_path / "discovery_expected.json").read_text(encoding="utf-8")
                )

                self.assertEqual(expected["build_tool"], metadata["build_tool"])
                self.assertEqual(expected["test_framework"], metadata["test_framework"])
                self.assertEqual(expected["status"], metadata["status"])
                self.assertIn("source_roots", metadata)
                self.assertIn("test_roots", metadata)

                for relative_path in expected["required_files"]:
                    self.assertTrue(
                        (fixture_path / relative_path).is_file(),
                        f"Missing fixture file: {fixture_name}/{relative_path}",
                    )


class DiscoveryHelperTests(unittest.TestCase):
    def test_discovers_maven_build_file_as_evidence(self):
        result = discover_build_tool(FIXTURE_ROOT / "maven-junit5")

        self.assertEqual((BuildTool.MAVEN, ["pom.xml"], None), result)

    def test_discovers_gradle_build_file_as_evidence(self):
        result = discover_build_tool(FIXTURE_ROOT / "gradle-junit4")

        self.assertEqual((BuildTool.GRADLE, ["build.gradle"], None), result)

    def test_reports_unknown_build_tool_without_evidence(self):
        result = discover_build_tool(FIXTURE_ROOT / "unknown")

        self.assertEqual(
            (BuildTool.UNKNOWN, [], "Neither Gradle or Maven were found."),
            result,
        )

    def test_reports_conflicting_build_tools_in_stable_order(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / "pom.xml").touch()
            (workspace / "build.gradle.kts").touch()
            (workspace / "build.gradle").touch()

            result = discover_build_tool(workspace)

        self.assertEqual(
            (
                BuildTool.CONFLICTING,
                ["pom.xml", "build.gradle", "build.gradle.kts"],
                "Found both gradle and maven: ['pom.xml', 'build.gradle', 'build.gradle.kts']",
            ),
            result,
        )

    def test_non_directory_has_unknown_build_tool(self):
        with tempfile.TemporaryDirectory() as directory:
            missing_workspace = Path(directory) / "missing"

            result = discover_build_tool(missing_workspace)

        self.assertEqual(
            (BuildTool.UNKNOWN, [], "Workspace is not a directory."),
            result,
        )

    def test_discovers_only_existing_conventional_roots(self):
        self.assertEqual(
            ["src/main/java"],
            discover_source_roots(FIXTURE_ROOT / "maven-junit5"),
        )
        self.assertEqual(
            ["src/test/java"],
            discover_test_roots(FIXTURE_ROOT / "maven-junit5"),
        )
        self.assertEqual([], discover_test_roots(FIXTURE_ROOT / "unknown"))

    def test_does_not_treat_a_file_as_a_test_root(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            false_root = workspace / "src" / "test" / "java"
            false_root.parent.mkdir(parents=True)
            false_root.touch()

            result = discover_test_roots(workspace)

        self.assertEqual([], result)

    def test_keyword_matching_is_case_insensitive(self):
        self.assertTrue(contains_any("ORG.JUNIT.JUPITER.API.TEST", ["org.junit.jupiter"]))
        self.assertFalse(contains_any("org.testng.annotations.Test", ["org.junit"]))

    def test_all_keyword_matching_requires_every_indicator(self):
        self.assertTrue(
            contains_all(
                "<groupId>junit</groupId><artifactId>junit</artifactId>",
                ["<groupId>junit</groupId>", "<artifactId>junit</artifactId>"],
            )
        )
        self.assertFalse(
            contains_all(
                "<artifactId>junit</artifactId>",
                ["<groupId>junit</groupId>", "<artifactId>junit</artifactId>"],
            )
        )

    def test_discovers_java_test_files_in_stable_order(self):
        workspace = FIXTURE_ROOT / "maven-junit5"

        results = discover_test_files(workspace, "src/test/java")

        self.assertEqual(
            [workspace / "src/test/java/com/example/CalculatorTest.java"],
            results,
        )

    def test_missing_test_root_has_no_test_files(self):
        results = discover_test_files(FIXTURE_ROOT / "unknown", "src/test/java")

        self.assertEqual([], results)

    def test_discovers_uppercase_java_test_file(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            test_root = workspace / "src" / "test" / "java"
            test_root.mkdir(parents=True)
            uppercase_test = test_root / "ExampleTest.JAVA"
            uppercase_test.touch()

            results = discover_test_files(workspace, "src/test/java")

        self.assertEqual([uppercase_test], results)

    def test_limits_number_of_discovered_test_files(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            test_root = workspace / "src" / "test" / "java"
            test_root.mkdir(parents=True)
            for index in range(5):
                (test_root / f"Test{index}.java").touch()

            results = discover_test_files(workspace, "src/test/java", max_files=2)

        self.assertEqual(2, len(results))

    def test_rejects_invalid_test_file_limit(self):
        with self.assertRaisesRegex(ValueError, "positive integer"):
            discover_test_files(FIXTURE_ROOT / "unknown", "src/test/java", max_files=0)


class DiscoveryFileReadTests(unittest.TestCase):
    def test_reads_utf8_file_within_limit(self):
        workspace = FIXTURE_ROOT / "maven-junit5"

        content = read_discovery_file(workspace, workspace / "pom.xml")

        self.assertIn("junit-jupiter", content)

    def test_rejects_invalid_utf8(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            path = workspace / "pom.xml"
            path.write_bytes(b"\xff")

            with self.assertRaisesRegex(FrameworkDiscoveryError, "not valid UTF-8"):
                read_discovery_file(workspace, path)

    def test_rejects_oversized_file(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            path = workspace / "pom.xml"
            path.write_bytes(b"x" * 11)

            with self.assertRaisesRegex(FrameworkDiscoveryError, "exceeds 10 bytes"):
                read_discovery_file(workspace, path, max_bytes=10)

    def test_rejects_file_outside_workspace(self):
        with tempfile.TemporaryDirectory() as workspace_directory:
            with tempfile.TemporaryDirectory() as outside_directory:
                workspace = Path(workspace_directory)
                outside = Path(outside_directory) / "pom.xml"
                outside.touch()

                with self.assertRaisesRegex(FrameworkDiscoveryError, "outside"):
                    read_discovery_file(workspace, outside)


class TestFrameworkEvidenceTests(unittest.TestCase):
    def assert_relative_string_paths(self, paths):
        self.assertTrue(paths, "expected at least one evidence path")
        for path in paths:
            with self.subTest(path=path):
                self.assertIsInstance(path, str)
                self.assertFalse(Path(path).is_absolute())

    def test_detects_junit5_from_maven_and_existing_test(self):
        workspace = FIXTURE_ROOT / "maven-junit5"

        framework, evidence_paths, status, reason = inspect_test_framework_evidence(
            workspace,
            ["src/test/java"],
        )

        self.assertEqual(TestFramework.JUNIT5, framework)
        self.assertEqual(TestDiscoveryStatus.DISCOVERED, status)
        self.assertIsNone(reason)
        self.assert_relative_string_paths(evidence_paths)
        self.assertIn("pom.xml", evidence_paths)
        self.assertIn("src/test/java/com/example/CalculatorTest.java", evidence_paths)

    def test_detects_junit4_from_gradle_and_existing_test(self):
        workspace = FIXTURE_ROOT / "gradle-junit4"

        framework, evidence_paths, status, reason = inspect_test_framework_evidence(
            workspace,
            ["src/test/java"],
        )

        self.assertEqual(TestFramework.JUNIT4, framework)
        self.assertEqual(TestDiscoveryStatus.DISCOVERED, status)
        self.assertIsNone(reason)
        self.assert_relative_string_paths(evidence_paths)
        self.assertIn("build.gradle", evidence_paths)
        self.assertIn("src/test/java/com/example/SlugFormatterTest.java", evidence_paths)

    def test_reports_insufficient_evidence_when_no_framework_is_found(self):
        framework, evidence_paths, status, reason = inspect_test_framework_evidence(
            FIXTURE_ROOT / "unknown",
            [],
        )

        self.assertEqual(TestFramework.UNKNOWN, framework)
        self.assertEqual([], evidence_paths)
        self.assertEqual(TestDiscoveryStatus.INSUFFICIENT_EVIDENCE, status)
        self.assertIsNotNone(reason)

    def test_reports_conflicting_framework_evidence_without_selecting_one(self):
        workspace = FIXTURE_ROOT / "conflicting"

        framework, evidence_paths, status, reason = inspect_test_framework_evidence(
            workspace,
            ["src/test/java"],
        )

        self.assertEqual(TestFramework.UNKNOWN, framework)
        self.assertEqual(TestDiscoveryStatus.CONFLICTING_EVIDENCE, status)
        self.assertIsNotNone(reason)
        self.assert_relative_string_paths(evidence_paths)
        self.assertIn("pom.xml", evidence_paths)
        self.assertIn("src/test/java/com/example/CounterTest.java", evidence_paths)

    def test_detects_mixed_junit_imports_in_one_file(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            test_root = workspace / "src" / "test" / "java"
            test_root.mkdir(parents=True)
            test_file = test_root / "MixedTest.java"
            test_file.write_text(
                "import org.junit.Test;\nimport org.junit.jupiter.api.Test;\n",
                encoding="utf-8",
            )

            framework, evidence_paths, status, reason = inspect_test_framework_evidence(
                workspace,
                ["src/test/java"],
            )

        self.assertEqual(TestFramework.UNKNOWN, framework)
        self.assertEqual(["src/test/java/MixedTest.java"], evidence_paths)
        self.assertEqual(TestDiscoveryStatus.CONFLICTING_EVIDENCE, status)
        self.assertIsNotNone(reason)

    def test_maven_junit4_requires_group_and_artifact_pair(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / "pom.xml").write_text(
                "<artifactId>junit</artifactId>",
                encoding="utf-8",
            )

            framework, evidence_paths, status, reason = inspect_test_framework_evidence(
                workspace,
                [],
            )

        self.assertEqual(TestFramework.UNKNOWN, framework)
        self.assertEqual([], evidence_paths)
        self.assertEqual(TestDiscoveryStatus.INSUFFICIENT_EVIDENCE, status)
        self.assertIsNotNone(reason)

    def test_caps_framework_evidence_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            test_root = workspace / "src" / "test" / "java"
            test_root.mkdir(parents=True)
            for index in range(MAX_DISCOVERY_EVIDENCE_PATHS + 5):
                (test_root / f"Example{index}Test.java").write_text(
                    "import org.junit.jupiter.api.Test;\n",
                    encoding="utf-8",
                )

            _, evidence_paths, _, _ = inspect_test_framework_evidence(
                workspace,
                ["src/test/java"],
            )

        self.assertEqual(MAX_DISCOVERY_EVIDENCE_PATHS, len(evidence_paths))


class DiscoverTestFrameworkIntegrationTests(unittest.TestCase):
    def test_fixture_results_match_expected_metadata(self):
        for fixture_path in sorted(FIXTURE_ROOT.iterdir()):
            if not fixture_path.is_dir():
                continue

            with self.subTest(fixture=fixture_path.name):
                expected = json.loads(
                    (fixture_path / "discovery_expected.json").read_text(
                        encoding="utf-8"
                    )
                )

                result = discover_test_framework(fixture_path)

                self.assertEqual(expected["build_tool"], result.build_tool.value)
                self.assertEqual(
                    expected["test_framework"], result.test_framework.value
                )
                self.assertEqual(expected["source_roots"], result.source_roots)
                self.assertEqual(expected["test_roots"], result.test_roots)
                self.assertEqual(expected["status"], result.test_status.value)
                self.assertTrue(
                    all(isinstance(path, str) for path in result.evidence_paths)
                )

    def test_accepts_workspace_as_a_string_path(self):
        result = discover_test_framework(str(FIXTURE_ROOT / "maven-junit5"))

        self.assertEqual(BuildTool.MAVEN, result.build_tool)
        self.assertEqual(TestFramework.JUNIT5, result.test_framework)


if __name__ == "__main__":
    unittest.main()
