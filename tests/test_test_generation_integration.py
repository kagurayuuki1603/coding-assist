import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from cd_assist.agent import CodingAssistantAgent
from cd_assist.cli import run_app
from cd_assist.errors import AgentResponseError
from cd_assist.models import (
    READ_FILE,
    RetrievalDecision,
    RetrievalRequest,
    TaskIntent,
    TaskInterpretation,
)
from cd_assist.test_generation import (
    PatchOperation,
    ProposedPatch,
    ProposedTestCase,
    TestFramework,
    TestGenerationContext,
    TestProposal,
)


FIXTURE_WORKSPACE = (
    Path(__file__).parent / "fixtures" / "test_generation_vertical"
)
REQUEST = "generate tests for src/main/java/com/example/RetryPolicy.java"
SOURCE_PATH = "src/main/java/com/example/RetryPolicy.java"
TEST_PATH = "src/test/java/com/example/RetryPolicyTest.java"


class TestGenerationVerticalSliceTests(unittest.TestCase):
    def snapshot_fixture(self):
        return {
            path.relative_to(FIXTURE_WORKSPACE).as_posix(): path.read_bytes()
            for path in FIXTURE_WORKSPACE.rglob("*")
            if path.is_file()
        }

    def make_proposal(self):
        return TestProposal(
            target_path=SOURCE_PATH,
            proposed_test_path=TEST_PATH,
            test_framework=TestFramework.JUNIT5,
            test_cases=[
                ProposedTestCase(
                    name="retriesTransientFailureBeforeLimit",
                    behavior="Retries transient failures before the attempt limit.",
                    rationale="Covers the successful retry branch.",
                    evidence_indices=[0],
                ),
                ProposedTestCase(
                    name="doesNotRetryPermanentFailure",
                    behavior="Does not retry a permanent failure.",
                    rationale="Covers the non-transient failure branch.",
                    evidence_indices=[0],
                ),
            ],
            assumptions=[],
            insufficient_evidence_reason=None,
        )

    def make_patch(self):
        return ProposedPatch(
            operation=PatchOperation.CREATE,
            path=TEST_PATH,
            expected_existing_content=None,
            proposed_content=(
                "package com.example;\n\n"
                "import org.junit.jupiter.api.Test;\n"
                "import static org.junit.jupiter.api.Assertions.assertFalse;\n"
                "import static org.junit.jupiter.api.Assertions.assertTrue;\n\n"
                "class RetryPolicyTest {\n"
                "    @Test void retriesTransientFailureBeforeLimit() {\n"
                "        assertTrue(new RetryPolicy().shouldRetry(1, true));\n"
                "    }\n"
                "    @Test void doesNotRetryPermanentFailure() {\n"
                "        assertFalse(new RetryPolicy().shouldRetry(1, false));\n"
                "    }\n"
                "}\n"
            ),
            rationale="Adds coverage for retry eligibility.",
            applied=False,
        )

    def make_agent(self):
        interpretation = TaskInterpretation(
            intent=TaskIntent.GENERATE_TESTS,
            target=SOURCE_PATH,
            search_terms=["RetryPolicy.java", "RetryPolicy"],
        )
        proposal = self.make_proposal()
        proposed_patch = self.make_patch()

        def propose_tests(client, context: TestGenerationContext):
            self.assertEqual(TestFramework.JUNIT5, context.discovery.test_framework)
            self.assertEqual(["src/test/java"], context.discovery.test_roots)
            self.assertEqual(SOURCE_PATH, context.evidence.items[0].path)
            self.assertIn("class RetryPolicy", context.evidence.items[0].content)
            return proposal

        def generate_patch(client, received_proposal, context, workspace):
            proposed_patch.validate_result(
                received_proposal,
                context.discovery,
                workspace,
            )
            return proposed_patch

        patch_generator = Mock(side_effect=generate_patch)
        agent = CodingAssistantAgent(
            client=object(),
            workspace=FIXTURE_WORKSPACE,
            generate_response=Mock(),
            interpret_intention=Mock(return_value=interpretation),
            select_tool=Mock(return_value=RetrievalRequest(
                tool=READ_FILE,
                path=SOURCE_PATH,
                query=None,
            )),
            decide_next_retrieval=Mock(return_value=RetrievalDecision(
                action="stop",
                tool=None,
                path=None,
                query=None,
                stop_reason="sufficient_evidence",
                reason="The target source is sufficient.",
            )),
            analyze_bugs=Mock(),
            propose_tests=Mock(side_effect=propose_tests),
            get_test_patch=patch_generator,
        )
        return agent, patch_generator

    def test_request_produces_valid_unapplied_patch_without_writing_fixture(self):
        before = self.snapshot_fixture()
        agent, patch_generator = self.make_agent()

        result = agent.generate_test_patch(REQUEST)

        self.assertEqual(PatchOperation.CREATE, result.operation)
        self.assertEqual(TEST_PATH, result.path)
        self.assertFalse(result.applied)
        self.assertIn("org.junit.jupiter.api.Test", result.proposed_content)
        self.assertIn("class RetryPolicyTest", result.proposed_content)
        self.assertFalse((FIXTURE_WORKSPACE / TEST_PATH).exists())
        self.assertEqual(before, self.snapshot_fixture())
        patch_generator.assert_called_once()

    def test_repeated_request_is_suppressed_without_writing_fixture(self):
        before = self.snapshot_fixture()
        agent, patch_generator = self.make_agent()

        agent.generate_test_patch(REQUEST)

        with self.assertRaisesRegex(AgentResponseError, "already generated"):
            agent.generate_test_patch(REQUEST)

        patch_generator.assert_called_once()
        self.assertEqual(before, self.snapshot_fixture())

    @patch("cd_assist.cli.print_goodbye")
    @patch("cd_assist.cli.print_intro")
    @patch("cd_assist.cli.print_exception")
    @patch("cd_assist.cli.print_agent_response")
    @patch("builtins.input", side_effect=[REQUEST, REQUEST, "exit"])
    def test_cli_prints_patch_then_reports_same_session_duplicate(
        self,
        input_mock,
        print_agent_response,
        print_exception,
        print_intro,
        print_goodbye,
    ):
        before = self.snapshot_fixture()
        agent, patch_generator = self.make_agent()

        run_app(FIXTURE_WORKSPACE, agent)

        printed_patch = print_agent_response.call_args.args[0]
        self.assertIn("Proposed Test Patch", printed_patch)
        self.assertIn("Applied: False", printed_patch)
        error = print_exception.call_args.args[0]
        self.assertIsInstance(error, AgentResponseError)
        self.assertIn("already generated", str(error))
        patch_generator.assert_called_once()
        print_goodbye.assert_called_once_with()
        self.assertEqual(before, self.snapshot_fixture())


if __name__ == "__main__":
    unittest.main()
