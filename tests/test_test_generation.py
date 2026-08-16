import json
import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from cd_assist.models import EvidenceItem, EvidenceSet, READ_FILE, TaskIntent, TaskInterpretation
from cd_assist.test_generation import (
    BuildTool,
    FrameworkDiscoveryError,
    MAX_DISCOVERY_EVIDENCE_PATHS,
    PatchOperation,
    ProposedPatch,
    ProposedTestCase,
    TestDiscoveryStatus,
    TestFramework,
    TestFrameworkDiscovery,
    TestGenerationContext,
    TestProposal,
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


class ProposedPatchTests(unittest.TestCase):
    def make_test_case(self, **overrides):
        values = {
            "name": "retriesTransientFailure",
            "behavior": "Retries a transient failure.",
            "rationale": "Covers retry behavior.",
            "evidence_indices": [0],
        }
        values.update(overrides)
        return ProposedTestCase(**values)

    def make_proposal(self, **overrides):
        values = {
            "target_path": "src/main/java/com/example/RetryPolicy.java",
            "proposed_test_path": "src/test/java/com/example/RetryPolicyTest.java",
            "test_framework": TestFramework.JUNIT5,
            "test_cases": [self.make_test_case()],
            "assumptions": [],
            "insufficient_evidence_reason": None,
        }
        values.update(overrides)
        return TestProposal(**values)

    def make_discovery(self, **overrides):
        values = {
            "test_framework": TestFramework.JUNIT5,
            "build_tool": BuildTool.MAVEN,
            "source_roots": ["src/main/java"],
            "test_roots": ["src/test/java"],
            "evidence_paths": ["pom.xml"],
            "test_status": TestDiscoveryStatus.DISCOVERED,
        }
        values.update(overrides)
        return TestFrameworkDiscovery(**values)

    def make_patch(self, **overrides):
        values = {
            "operation": PatchOperation.CREATE,
            "path": "src/test/java/com/example/RetryPolicyTest.java",
            "expected_existing_content": None,
            "proposed_content": (
                "package com.example;\n\n"
                "import org.junit.jupiter.api.Test;\n\n"
                "class RetryPolicyTest {\n"
                "    @Test\n"
                "    void retriesTransientFailure() {}\n"
                "}\n"
            ),
            "rationale": "Adds JUnit coverage for transient retry behavior.",
            "applied": False,
        }
        values.update(overrides)
        return ProposedPatch(**values)

    def test_accepts_create_patch_for_junit_test(self):
        patch = self.make_patch()
        proposal = self.make_proposal()
        discovery = self.make_discovery()

        patch.validate_result(proposal, discovery, FIXTURE_ROOT)

        self.assertEqual(PatchOperation.CREATE, patch.operation)
        self.assertTrue(patch.path.endswith("RetryPolicyTest.java"))
        self.assertIn("org.junit.jupiter.api.Test", patch.proposed_content)
        self.assertFalse(patch.applied)

    def test_normalizes_windows_path(self):
        patch = self.make_patch(
            path=r"src\test\java\com\example\RetryPolicyTest.java"
        )

        self.assertEqual(
            "src/test/java/com/example/RetryPolicyTest.java",
            patch.path,
        )

    def test_rejects_absolute_and_parent_traversal_paths(self):
        for invalid_path in (
            "/repo/src/test/java/RetryPolicyTest.java",
            "../RetryPolicyTest.java",
        ):
            with self.subTest(path=invalid_path):
                with self.assertRaises(ValidationError):
                    self.make_patch(path=invalid_path)

    def test_create_rejects_expected_existing_content(self):
        with self.assertRaisesRegex(ValueError, "existing content"):
            self.make_patch(expected_existing_content="class RetryPolicyTest {}")

    def test_rejects_applied_patch(self):
        with self.assertRaises(ValidationError):
            self.make_patch(applied=True)

    def test_rejects_blank_and_overlong_patch_text(self):
        for field_name in ("proposed_content", "rationale"):
            for invalid_text in ("   ", "x" * 2_001):
                with self.subTest(field=field_name, length=len(invalid_text)):
                    with self.assertRaises(ValidationError):
                        self.make_patch(**{field_name: invalid_text})

    def test_rejects_unsupported_modify_operation(self):
        with self.assertRaises(ValidationError):
            self.make_patch(operation="modify")

    def test_rejects_extra_fields(self):
        with self.assertRaises(ValidationError):
            self.make_patch(write_to_workspace=True)

    def test_rejects_path_outside_test_root(self):
        patch = self.make_patch(path="src/main/java/com/example/RetryPolicyTest.java")

        with self.assertRaisesRegex(ValueError, "test root"):
            patch.validate_result(
                self.make_proposal(), self.make_discovery(), FIXTURE_ROOT
            )

    def test_accepts_junit4_import_for_junit4_proposal(self):
        patch = self.make_patch(
            proposed_content=(
                "package com.example;\n\n"
                "import org.junit.Test;\n\n"
                "class RetryPolicyTest {\n"
                "    @Test public void retriesTransientFailure() {}\n"
                "}\n"
            )
        )
        proposal = self.make_proposal(test_framework=TestFramework.JUNIT4)
        discovery = self.make_discovery(
            test_framework=TestFramework.JUNIT4,
            build_tool=BuildTool.GRADLE,
        )

        patch.validate_result(proposal, discovery, FIXTURE_ROOT)

    def test_rejects_non_java_destination(self):
        patch = self.make_patch(path="src/test/java/com/example/RetryPolicyTest.txt")
        proposal = self.make_proposal(
            proposed_test_path="src/test/java/com/example/RetryPolicyTest.txt"
        )

        with self.assertRaisesRegex(ValueError, "Java file"):
            patch.validate_result(proposal, self.make_discovery(), FIXTURE_ROOT)

    def test_rejects_path_different_from_proposed_test_path(self):
        patch = self.make_patch(path="src/test/java/com/example/OtherTest.java")

        with self.assertRaisesRegex(ValueError, "proposed test path"):
            patch.validate_result(
                self.make_proposal(), self.make_discovery(), FIXTURE_ROOT
            )

    def test_rejects_wrong_or_mixed_junit_imports(self):
        junit4_content = self.make_patch().proposed_content.replace(
            "org.junit.jupiter.api.Test",
            "org.junit.Test",
        )
        mixed_content = self.make_patch().proposed_content.replace(
            "import org.junit.jupiter.api.Test;",
            "import org.junit.jupiter.api.Test;\nimport org.junit.Test;",
        )

        for content in (junit4_content, mixed_content):
            with self.subTest(content=content):
                with self.assertRaisesRegex(ValueError, "JUnit5"):
                    self.make_patch(proposed_content=content).validate_result(
                        self.make_proposal(),
                        self.make_discovery(),
                        FIXTURE_ROOT,
                    )

    def test_rejects_missing_proposed_test_method(self):
        proposal = self.make_proposal(
            test_cases=[self.make_test_case(name="stopsAfterMaximumAttempts")]
        )

        with self.assertRaisesRegex(ValueError, "test names"):
            self.make_patch().validate_result(
                proposal, self.make_discovery(), FIXTURE_ROOT
            )

    def test_rejects_framework_mismatch_between_proposal_and_discovery(self):
        discovery = self.make_discovery(
            test_framework=TestFramework.JUNIT4,
            build_tool=BuildTool.GRADLE,
        )

        with self.assertRaisesRegex(ValueError, "Framework"):
            self.make_patch().validate_result(
                self.make_proposal(), discovery, FIXTURE_ROOT
            )

    def test_rejects_class_name_different_from_destination_filename(self):
        content = self.make_patch().proposed_content.replace(
            "class RetryPolicyTest",
            "class DifferentTest",
        )

        with self.assertRaisesRegex(ValueError, "class"):
            self.make_patch(proposed_content=content).validate_result(
                self.make_proposal(),
                self.make_discovery(),
                FIXTURE_ROOT,
            )

    def test_create_rejects_existing_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            destination = workspace / "src/test/java/com/example/RetryPolicyTest.java"
            destination.parent.mkdir(parents=True)
            destination.touch()

            with self.assertRaisesRegex(ValueError, "already exists"):
                self.make_patch().validate_result(
                    self.make_proposal(),
                    self.make_discovery(),
                    workspace,
                )

    def test_accepts_nonexistent_destination_with_string_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            self.make_patch().validate_result(
                self.make_proposal(),
                self.make_discovery(),
                directory,
            )

    def test_formats_proposed_patch_for_console(self):
        patch = self.make_patch()

        output = patch.to_console_string()

        self.assertIn("Proposed Test Patch", output)
        self.assertIn("Operation: create", output)
        self.assertIn(
            "Path: src/test/java/com/example/RetryPolicyTest.java",
            output,
        )
        self.assertIn("Expected Existing Content: None", output)
        self.assertIn("Applied: False", output)
        self.assertIn(patch.rationale, output)
        self.assertIn(patch.proposed_content, output)


class ProposedTestCaseTests(unittest.TestCase):
    def test_accepts_test_case_with_supporting_evidence(self):
        test_case = ProposedTestCase(
            name="returnsFalseAfterMaximumAttempts",
            behavior="Retries stop after the configured maximum attempt count.",
            rationale="The boundary controls whether another operation is attempted.",
            evidence_indices=[0],
        )

        self.assertEqual("returnsFalseAfterMaximumAttempts", test_case.name)
        self.assertEqual([0], test_case.evidence_indices)

    def test_rejects_more_than_ten_evidence_indices(self):
        with self.assertRaises(ValidationError):
            ProposedTestCase(
                name="coversBoundary",
                behavior="Covers the retry boundary.",
                rationale="The boundary is important.",
                evidence_indices=list(range(11)),
            )

    def test_rejects_blank_and_overlong_name(self):
        for invalid_name in ("   ", "n" * 501):
            with self.subTest(name_length=len(invalid_name)):
                with self.assertRaises(ValidationError):
                    ProposedTestCase(
                        name=invalid_name,
                        behavior="Covers the retry boundary.",
                        rationale="The boundary is important.",
                        evidence_indices=[0],
                    )

    def test_rejects_blank_and_overlong_behavior_or_rationale(self):
        for field_name in ("behavior", "rationale"):
            for invalid_text in ("   ", "x" * 1_001):
                with self.subTest(field=field_name, text_length=len(invalid_text)):
                    values = {
                        "name": "coversBoundary",
                        "behavior": "Covers the retry boundary.",
                        "rationale": "The boundary is important.",
                        "evidence_indices": [0],
                    }
                    values[field_name] = invalid_text

                    with self.assertRaises(ValidationError):
                        ProposedTestCase(**values)

    def test_rejects_empty_evidence_indices(self):
        with self.assertRaises(ValidationError):
            ProposedTestCase(
                name="coversBoundary",
                behavior="Covers the retry boundary.",
                rationale="The boundary is important.",
                evidence_indices=[],
            )

    def test_rejects_negative_evidence_index(self):
        with self.assertRaises(ValidationError):
            ProposedTestCase(
                name="coversBoundary",
                behavior="Covers the retry boundary.",
                rationale="The boundary is important.",
                evidence_indices=[-1],
            )

    def test_rejects_duplicate_evidence_indices(self):
        with self.assertRaisesRegex(ValueError, "unique"):
            ProposedTestCase(
                name="coversBoundary",
                behavior="Covers the retry boundary.",
                rationale="The boundary is important.",
                evidence_indices=[0, 0],
            )

    def test_rejects_extra_fields(self):
        with self.assertRaises(ValidationError):
            ProposedTestCase(
                name="coversBoundary",
                behavior="Covers the retry boundary.",
                rationale="The boundary is important.",
                evidence_indices=[0],
                source_code="assertTrue(true);",
            )


class TestProposalTests(unittest.TestCase):
    def make_test_case(self, **overrides):
        values = {
            "name": "returnsFalseAfterMaximumAttempts",
            "behavior": "Retries stop after the configured maximum attempt count.",
            "rationale": "The boundary controls whether another operation is attempted.",
            "evidence_indices": [0],
        }
        values.update(overrides)
        return ProposedTestCase(**values)

    def make_proposal(self, **overrides):
        values = {
            "target_path": "src/main/java/com/example/RetryPolicy.java",
            "proposed_test_path": "src/test/java/com/example/RetryPolicyTest.java",
            "test_framework": TestFramework.JUNIT5,
            "test_cases": [self.make_test_case()],
            "assumptions": [],
            "insufficient_evidence_reason": None,
        }
        values.update(overrides)
        return TestProposal(**values)

    def make_context(self, **overrides):
        values = {
            "request": "generate tests for RetryPolicy.java",
            "interpretation": TaskInterpretation(
                intent=TaskIntent.GENERATE_TESTS,
                target="src/main/java/com/example/RetryPolicy.java",
                search_terms=["RetryPolicy.java"],
            ),
            "discovery": TestFrameworkDiscovery(
                test_framework=TestFramework.JUNIT5,
                build_tool=BuildTool.MAVEN,
                source_roots=["src/main/java"],
                test_roots=["src/test/java"],
                evidence_paths=["pom.xml"],
                test_status=TestDiscoveryStatus.DISCOVERED,
            ),
            "evidence": EvidenceSet(
                items=[
                    EvidenceItem(
                        path="src/main/java/com/example/RetryPolicy.java",
                        start_line=1,
                        content="class RetryPolicy {}",
                        source=READ_FILE,
                        truncated=False,
                    )
                ],
                truncated=False,
            ),
        }
        values.update(overrides)
        return TestGenerationContext(**values)

    def test_accepts_successful_proposal_with_test_cases(self):
        proposal = self.make_proposal()

        self.assertEqual(TestFramework.JUNIT5, proposal.test_framework)
        self.assertEqual(1, len(proposal.test_cases))
        self.assertIsNone(proposal.insufficient_evidence_reason)

    def test_accepts_insufficient_evidence_without_test_cases(self):
        proposal = self.make_proposal(
            target_path=None,
            proposed_test_path=None,
            test_framework=TestFramework.UNKNOWN,
            test_cases=[],
            insufficient_evidence_reason="The repository test framework is unknown.",
        )

        self.assertIsNone(proposal.target_path)
        self.assertIsNone(proposal.proposed_test_path)
        self.assertEqual([], proposal.test_cases)
        self.assertIsNotNone(proposal.insufficient_evidence_reason)

    def test_successful_proposal_requires_target_and_proposed_paths(self):
        for field_name in ("target_path", "proposed_test_path"):
            with self.subTest(field=field_name):
                with self.assertRaisesRegex(ValueError, "requires"):
                    self.make_proposal(**{field_name: None})

    def test_successful_proposal_requires_at_least_one_test_case(self):
        with self.assertRaisesRegex(ValueError, "at least 1"):
            self.make_proposal(test_cases=[])

    def test_insufficient_proposal_rejects_test_cases(self):
        with self.assertRaisesRegex(ValueError, "should not have"):
            self.make_proposal(
                insufficient_evidence_reason="The framework is unknown."
            )

    def test_rejects_duplicate_test_names_case_insensitively(self):
        with self.assertRaisesRegex(ValueError, "unique"):
            self.make_proposal(
                test_cases=[
                    self.make_test_case(name="handlesBoundary"),
                    self.make_test_case(name=" HandlesBoundary "),
                ]
            )

    def test_normalizes_windows_paths(self):
        proposal = self.make_proposal(
            target_path=r"src\main\java\RetryPolicy.java",
            proposed_test_path=r"src\test\java\RetryPolicyTest.java",
        )

        self.assertEqual("src/main/java/RetryPolicy.java", proposal.target_path)
        self.assertEqual(
            "src/test/java/RetryPolicyTest.java",
            proposal.proposed_test_path,
        )

    def test_rejects_absolute_and_parent_traversal_paths(self):
        for field_name, invalid_path in (
            ("target_path", "/repo/RetryPolicy.java"),
            ("proposed_test_path", "../RetryPolicyTest.java"),
        ):
            with self.subTest(field=field_name):
                with self.assertRaises(ValidationError):
                    self.make_proposal(**{field_name: invalid_path})

    def test_rejects_more_than_ten_test_cases_or_assumptions(self):
        with self.assertRaises(ValidationError):
            self.make_proposal(
                test_cases=[
                    self.make_test_case(name=f"case{index}")
                    for index in range(11)
                ]
            )

        with self.assertRaises(ValidationError):
            self.make_proposal(
                assumptions=[f"assumption {index}" for index in range(11)]
            )

    def test_rejects_extra_fields(self):
        with self.assertRaises(ValidationError):
            self.make_proposal(source_code="class RetryPolicyTest {}")

    def test_unknown_framework_rejects_successful_proposal(self):
        with self.assertRaisesRegex(ValueError, "framework"):
            self.make_proposal(test_framework=TestFramework.UNKNOWN)

    def test_rejects_blank_and_overlong_assumptions(self):
        for invalid_assumption in ("   ", "a" * 1_001):
            with self.subTest(assumption_length=len(invalid_assumption)):
                with self.assertRaises(ValidationError):
                    self.make_proposal(assumptions=[invalid_assumption])

    def test_rejects_blank_and_overlong_insufficient_evidence_reason(self):
        for invalid_reason in ("   ", "r" * 1_001):
            with self.subTest(reason_length=len(invalid_reason)):
                with self.assertRaises(ValidationError):
                    self.make_proposal(
                        test_framework=TestFramework.UNKNOWN,
                        test_cases=[],
                        insufficient_evidence_reason=invalid_reason,
                    )

    def test_validates_successful_proposal_against_context(self):
        proposal = self.make_proposal()

        result = proposal.validate_result(self.make_context())

        self.assertIs(proposal, result)

    def test_rejects_target_path_missing_from_evidence(self):
        proposal = self.make_proposal(target_path="src/main/java/Other.java")

        with self.assertRaisesRegex(ValueError, "Target path"):
            proposal.validate_result(self.make_context())

    def test_rejects_out_of_range_evidence_index(self):
        proposal = self.make_proposal(
            test_cases=[self.make_test_case(evidence_indices=[1])]
        )

        with self.assertRaisesRegex(ValueError, "outside the evidence set"):
            proposal.validate_result(self.make_context())

    def test_rejects_proposed_path_outside_discovered_test_roots(self):
        proposal = self.make_proposal(
            proposed_test_path="tests/RetryPolicyTest.java"
        )

        with self.assertRaisesRegex(ValueError, "discovered test root"):
            proposal.validate_result(self.make_context())

    def test_rejects_framework_that_differs_from_discovery(self):
        proposal = self.make_proposal(test_framework=TestFramework.JUNIT4)

        with self.assertRaisesRegex(ValueError, "does not match"):
            proposal.validate_result(self.make_context())

    def test_accepts_insufficient_result_without_test_roots_or_evidence(self):
        proposal = self.make_proposal(
            target_path=None,
            proposed_test_path=None,
            test_framework=TestFramework.UNKNOWN,
            test_cases=[],
            insufficient_evidence_reason="The framework could not be discovered.",
        )
        context = self.make_context(
            discovery=TestFrameworkDiscovery(
                test_framework=TestFramework.UNKNOWN,
                build_tool=BuildTool.UNKNOWN,
                build_reason="No supported build configuration was found.",
                source_roots=[],
                test_roots=[],
                evidence_paths=[],
                test_status=TestDiscoveryStatus.INSUFFICIENT_EVIDENCE,
                test_reason="No test framework was found.",
            ),
            evidence=EvidenceSet(items=[], truncated=False),
        )

        result = proposal.validate_result(context)

        self.assertIs(proposal, result)

    def test_formats_successful_proposal_for_console(self):
        proposal = self.make_proposal(
            assumptions=["RetryPolicy has no constructor dependencies."]
        )

        output = proposal.to_console_string()

        self.assertIn("Test Proposal", output)
        self.assertIn(
            "Target: src/main/java/com/example/RetryPolicy.java",
            output,
        )
        self.assertIn(
            "Proposed Test Path: src/test/java/com/example/RetryPolicyTest.java",
            output,
        )
        self.assertIn("Test Framework: junit5", output)
        self.assertIn("Status: proposed", output)
        self.assertIn("1. returnsFalseAfterMaximumAttempts", output)
        self.assertIn("Evidence: 0", output)
        self.assertIn("- RetryPolicy has no constructor dependencies.", output)

    def test_formats_successful_proposal_without_assumptions(self):
        output = self.make_proposal().to_console_string()

        self.assertIn("Assumptions\nNone", output)

    def test_formats_insufficient_proposal_for_console(self):
        proposal = self.make_proposal(
            target_path=None,
            proposed_test_path=None,
            test_framework=TestFramework.UNKNOWN,
            test_cases=[],
            insufficient_evidence_reason="The test framework is unknown.",
        )

        output = proposal.to_console_string()

        self.assertIn("Target: None", output)
        self.assertIn("Proposed Test Path: None", output)
        self.assertIn("Status: insufficient_evidence", output)
        self.assertIn("Reason: The test framework is unknown.", output)
        self.assertNotIn("Test Cases", output)


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
