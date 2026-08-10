import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from cd_assist.errors import FileParseError, ModelResponseError
from cd_assist.models import (
    EvidenceItem,
    EvidenceSet,
    READ_FILE,
    SEARCH_FILES,
    RetrievalDecision,
    RetrievalObservation,
    RetrievalRequest,
    RetrievalState,
    StopReason,
    TaskIntent,
    TaskInterpretation,
)
from cd_assist.retrieval import (
    build_retrieval_context,
    execute_retrieval,
    resolve_retrieval_request,
    run_retrieval_loop,
)
from cd_assist.tools import (
    LineSnippet,
    MatchType,
    SearchResult,
)

FIXTURE_WORKSPACE = Path(__file__).parent / "fixtures"


class RetrievalStateTests(unittest.TestCase):
    def test_new_state_starts_empty(self):
        state = RetrievalState()

        self.assertEqual([], state.requests)
        self.assertEqual([], state.observations)
        self.assertIsNone(state.stop_reason)

    def test_request_lists_are_independent_between_states(self):
        first_state = RetrievalState()
        second_state = RetrievalState()
        request = RetrievalRequest(
            tool=READ_FILE,
            path="UserService.java",
            query=None,
        )

        first_state.requests.append(request)

        self.assertEqual([request], first_state.requests)
        self.assertEqual([], second_state.requests)

    def test_observation_can_be_recorded(self):
        state = RetrievalState()
        request = RetrievalRequest(
            tool=SEARCH_FILES,
            path=None,
            query="validation",
        )
        observation = RetrievalObservation(
            request=request,
            result=["UserService.java"],
        )

        state.update(request, observation)

        self.assertEqual([request], state.requests)
        self.assertEqual([observation], state.observations)

    def test_stop_reason_can_be_assigned(self):
        state = RetrievalState()

        state.stop_reason = StopReason.MAX_ROUNDS

        self.assertEqual(StopReason.MAX_ROUNDS, state.stop_reason)


class RetrievalObservationEvidenceTests(unittest.TestCase):
    def test_normalizes_read_file_result(self):
        observation = RetrievalObservation(
            request=RetrievalRequest(
                tool=READ_FILE,
                path="ExampleService.java",
                query=None,
            ),
            result={
                "content": "public class ExampleService {}",
                "truncated": True,
            },
        )

        items = observation.get_evidence_items()

        self.assertEqual(
            [
                EvidenceItem(
                    path="ExampleService.java",
                    start_line=1,
                    content="public class ExampleService {}",
                    source=READ_FILE,
                    truncated=True,
                )
            ],
            items,
        )

    def test_rejects_malformed_read_file_results(self):
        request = RetrievalRequest(
            tool=READ_FILE,
            path="ExampleService.java",
            query=None,
        )
        malformed_results = [
            "not a dictionary",
            {"content": 123, "truncated": False},
            {"content": "class ExampleService {}", "truncated": "false"},
        ]

        for result in malformed_results:
            with self.subTest(result=result):
                observation = RetrievalObservation(
                    request=request,
                    result=result,
                )

                with self.assertRaises(TypeError):
                    observation.get_evidence_items()

    def test_normalizes_content_search_result_with_attribution(self):
        observation = RetrievalObservation(
            request=RetrievalRequest(
                tool=SEARCH_FILES,
                path=None,
                query="display",
            ),
            result=[
                SearchResult(
                    path="ExampleService.java",
                    match_type=MatchType.CONTENT,
                    line_snippet=LineSnippet(
                        line=12,
                        snippet="public String findDisplayName(long userId) {",
                    ),
                )
            ],
        )

        items = observation.get_evidence_items()

        self.assertEqual(1, len(items))
        self.assertEqual("ExampleService.java", items[0].path)
        self.assertEqual(12, items[0].start_line)
        self.assertEqual(
            "public String findDisplayName(long userId) {",
            items[0].content,
        )
        self.assertEqual(SEARCH_FILES, items[0].source)
        self.assertFalse(items[0].truncated)

    def test_normalizes_path_only_search_result(self):
        observation = RetrievalObservation(
            request=RetrievalRequest(
                tool=SEARCH_FILES,
                path=None,
                query="ExampleService",
            ),
            result=[
                SearchResult(
                    path="ExampleService.java",
                    match_type=MatchType.PATH,
                )
            ],
        )

        items = observation.get_evidence_items()

        self.assertEqual(1, len(items))
        self.assertEqual("ExampleService.java", items[0].path)
        self.assertIsNone(items[0].start_line)
        self.assertEqual("", items[0].content)

    def test_rejects_non_list_search_result(self):
        observation = RetrievalObservation(
            request=RetrievalRequest(
                tool=SEARCH_FILES,
                path=None,
                query="display",
            ),
            result="not a list",
        )

        with self.assertRaisesRegex(
            TypeError,
            "search_files result must be a list",
        ):
            observation.get_evidence_items()


class EvidenceSetTests(unittest.TestCase):
    def test_items_default_to_an_independent_empty_list(self):
        first = EvidenceSet(truncated=False)
        second = EvidenceSet(truncated=False)

        first.items.append(
            EvidenceItem(
                path="ExampleService.java",
                start_line=1,
                content="class ExampleService {}",
                source=READ_FILE,
                truncated=False,
            )
        )

        self.assertEqual(1, len(first.items))
        self.assertEqual([], second.items)

    def test_deduplicates_identical_evidence(self):
        first = EvidenceItem(
            path="ExampleService.java",
            start_line=12,
            content="findDisplayName()",
            source=SEARCH_FILES,
            truncated=False,
        )
        duplicate = EvidenceItem(
            path="exampleservice.java",
            start_line=12,
            content="  findDisplayName()  ",
            source=READ_FILE,
            truncated=False,
        )
        evidence_set = EvidenceSet(
            items=[first, duplicate],
            truncated=False,
        )

        result = evidence_set.deduplicated()

        self.assertEqual([first], result.items)
        self.assertFalse(result.truncated)

    def test_parse_enforces_context_budget_and_marks_truncation(self):
        evidence_set = EvidenceSet(
            items=[
                EvidenceItem(
                    path="LargeService.java",
                    start_line=1,
                    content="x" * 1_000,
                    source=READ_FILE,
                    truncated=False,
                )
            ],
            truncated=False,
        )

        result = evidence_set.parse(max_chars=200)

        self.assertLessEqual(len(result.context_str), 200)
        self.assertIn("Evidence context truncated: True", result.context_str)
        self.assertTrue(result.truncated)

    def test_parse_deduplicates_before_applying_context_budget(self):
        item = EvidenceItem(
            path="ExampleService.java",
            start_line=12,
            content="findDisplayName()",
            source=SEARCH_FILES,
            truncated=False,
        )
        duplicate = item.model_copy()
        item_context = f"Evidence 0: \n{item.to_context_string()}"
        single_item_length = len(item_context)
        evidence_set = EvidenceSet(
            items=[item, duplicate],
            truncated=False,
        )

        result = evidence_set.parse(max_chars=single_item_length)

        self.assertEqual([item], result.items)
        self.assertEqual(item_context, result.context_str)
        self.assertFalse(result.truncated)

    def test_parse_does_not_truncate_context_at_exact_budget(self):
        item = EvidenceItem(
            path="ExampleService.java",
            start_line=1,
            content="class ExampleService {}",
            source=READ_FILE,
            truncated=False,
        )
        item_context = f"Evidence 0: \n{item.to_context_string()}"

        result = EvidenceSet(
            items=[item],
            truncated=False,
        ).parse(max_chars=len(item_context))

        self.assertEqual([item], result.items)
        self.assertEqual(item_context, result.context_str)
        self.assertFalse(result.truncated)

    def test_oversized_evidence_keeps_items_consistent_with_context(self):
        item = EvidenceItem(
            path="LargeService.java",
            start_line=1,
            content="x" * 1_000,
            source=READ_FILE,
            truncated=False,
        )

        result = EvidenceSet(
            items=[item],
            truncated=False,
        ).parse(max_chars=200)

        self.assertEqual([item], result.items)
        self.assertFalse(result.items[0].truncated)
        self.assertTrue(result.truncated)
        self.assertIn("File: LargeService.java", result.context_str)
        self.assertIn("Evidence context truncated: True", result.context_str)
        self.assertNotIn(item.content, result.context_str)
        self.assertLessEqual(len(result.context_str), 200)


class RetrievalStateEvidenceTests(unittest.TestCase):
    def test_empty_state_builds_empty_non_truncated_evidence_set(self):
        result = RetrievalState().build_evidence_set()

        self.assertEqual([], result.items)
        self.assertEqual("", result.context_str)
        self.assertFalse(result.truncated)

    def test_builds_evidence_set_from_observations_in_order(self):
        search_request = RetrievalRequest(
            tool=SEARCH_FILES,
            path=None,
            query="display",
        )
        read_request = RetrievalRequest(
            tool=READ_FILE,
            path="ExampleService.java",
            query=None,
        )
        state = RetrievalState(
            observations=[
                RetrievalObservation(
                    request=search_request,
                    result=[
                        SearchResult(
                            path="ExampleService.java",
                            match_type=MatchType.CONTENT,
                            line_snippet=LineSnippet(
                                line=12,
                                snippet="findDisplayName",
                            ),
                        )
                    ],
                ),
                RetrievalObservation(
                    request=read_request,
                    result={
                        "content": "public class ExampleService {}",
                        "truncated": True,
                    },
                ),
            ]
        )

        result = state.build_evidence_set()

        self.assertEqual(2, len(result.items))
        self.assertEqual(SEARCH_FILES, result.items[0].source)
        self.assertEqual(READ_FILE, result.items[1].source)
        self.assertTrue(result.truncated)


class RequestWasAttemptedTests(unittest.TestCase):
    def test_identical_read_requests_are_duplicates(self):
        previous = RetrievalRequest(
            tool=READ_FILE,
            path="UserService.java",
            query=None,
        )
        state = RetrievalState(requests=[previous])
        repeated = RetrievalRequest(
            tool=READ_FILE,
            path="UserService.java",
            query=None,
        )

        self.assertTrue(state.was_attempted(repeated))

    def test_identical_search_requests_are_duplicates(self):
        previous = RetrievalRequest(
            tool=SEARCH_FILES,
            path=None,
            query="user validation",
        )
        state = RetrievalState(requests=[previous])
        repeated = RetrievalRequest(
            tool=SEARCH_FILES,
            path=None,
            query="user validation",
        )

        self.assertTrue(state.was_attempted(repeated))

    def test_surrounding_whitespace_does_not_bypass_detection(self):
        previous = RetrievalRequest(
            tool=SEARCH_FILES,
            path=None,
            query="user validation",
        )
        state = RetrievalState(requests=[previous])
        repeated = RetrievalRequest(
            tool=SEARCH_FILES,
            path=None,
            query="  USER VALIDATION  ",
        )

        self.assertTrue(state.was_attempted(repeated))

    def test_different_paths_or_queries_are_allowed(self):
        state = RetrievalState(
            requests=[
                RetrievalRequest(
                    tool=READ_FILE,
                    path="UserService.java",
                    query=None,
                ),
                RetrievalRequest(
                    tool=SEARCH_FILES,
                    path=None,
                    query="validation",
                ),
            ]
        )
        different_requests = [
            RetrievalRequest(
                tool=READ_FILE,
                path="UserValidator.java",
                query=None,
            ),
            RetrievalRequest(
                tool=SEARCH_FILES,
                path=None,
                query="authentication",
            ),
        ]

        for request in different_requests:
            with self.subTest(request=request):
                self.assertFalse(state.was_attempted(request))


class ExecuteRetrievalTests(unittest.TestCase):
    @patch("cd_assist.retrieval.search_files")
    @patch("cd_assist.retrieval.read_file")
    def test_dispatches_read_file_and_returns_result(
        self,
        read_file,
        search_files,
    ):
        request = RetrievalRequest(
            tool=READ_FILE,
            path="UserService.java",
            query=None,
        )
        tool_result = {
            "content": "public class UserService {}",
            "truncated": False,
        }
        read_file.return_value = tool_result

        result = execute_retrieval("workspace", request)

        self.assertIs(tool_result, result)
        read_file.assert_called_once_with("workspace", "UserService.java")
        search_files.assert_not_called()

    @patch("cd_assist.retrieval.search_files")
    @patch("cd_assist.retrieval.read_file")
    def test_dispatches_search_files_and_returns_result(
        self,
        read_file,
        search_files,
    ):
        request = RetrievalRequest(
            tool=SEARCH_FILES,
            path=None,
            query="validation",
        )
        tool_result = [object()]
        search_files.return_value = tool_result

        result = execute_retrieval("workspace", request)

        self.assertIs(tool_result, result)
        search_files.assert_called_once_with("workspace", "validation")
        read_file.assert_not_called()

    @patch("cd_assist.retrieval.search_files")
    @patch("cd_assist.retrieval.read_file")
    def test_rejects_unsupported_tool(self, read_file, search_files):
        invalid_request = SimpleNamespace(
            tool="delete_file",
            path="UserService.java",
            query=None,
        )

        with self.assertRaisesRegex(ValueError, "Unsupported retrieval tool"):
            execute_retrieval("workspace", invalid_request)

        read_file.assert_not_called()
        search_files.assert_not_called()


class ResolveRetrievalRequestTests(unittest.TestCase):
    def test_preserves_existing_relative_file_request(self):
        request = RetrievalRequest(
            tool=READ_FILE,
            path="ExampleService.java",
            query=None,
        )

        result = resolve_retrieval_request(FIXTURE_WORKSPACE, request)

        self.assertIs(request, result)

    def test_converts_missing_relative_file_to_search(self):
        request = RetrievalRequest(
            tool=READ_FILE,
            path="Calculator.java",
            query=None,
        )

        result = resolve_retrieval_request(FIXTURE_WORKSPACE, request)

        self.assertEqual(
            RetrievalRequest(
                tool=SEARCH_FILES,
                path=None,
                query="Calculator.java",
            ),
            result,
        )

    def test_preserves_search_request(self):
        request = RetrievalRequest(
            tool=SEARCH_FILES,
            path=None,
            query="Calculator",
        )

        result = resolve_retrieval_request(FIXTURE_WORKSPACE, request)

        self.assertIs(request, result)

    def test_preserves_absolute_read_for_tool_validation(self):
        request = RetrievalRequest(
            tool=READ_FILE,
            path="/tmp/Outside.java",
            query=None,
        )

        result = resolve_retrieval_request(FIXTURE_WORKSPACE, request)

        self.assertIs(request, result)

    def test_preserves_parent_traversal_for_tool_validation(self):
        request = RetrievalRequest(
            tool=READ_FILE,
            path="../Outside.java",
            query=None,
        )

        result = resolve_retrieval_request(FIXTURE_WORKSPACE, request)

        self.assertIs(request, result)

    def test_normalizes_windows_separators_when_file_exists(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            target = workspace / "src" / "Example.java"
            target.parent.mkdir()
            target.touch()
            request = RetrievalRequest(
                tool=READ_FILE,
                path=r"src\Example.java",
                query=None,
            )

            result = resolve_retrieval_request(workspace, request)

        self.assertIs(request, result)


class BuildRetrievalContextTests(unittest.TestCase):
    def test_describes_empty_state_and_remaining_rounds(self):
        context = build_retrieval_context(
            RetrievalState(),
            remaining_rounds=3,
        )

        self.assertIn("Remaining retrieval rounds: 3", context)
        self.assertIn("No retrieval observations.", context)

    def test_includes_read_file_request_and_result(self):
        request = RetrievalRequest(
            tool=READ_FILE,
            path="ExampleService.java",
            query=None,
        )
        state = RetrievalState(
            requests=[request],
            observations=[
                RetrievalObservation(
                    request=request,
                    result={
                        "content": "public class ExampleService {}",
                        "truncated": False,
                    },
                )
            ],
        )

        context = build_retrieval_context(state, remaining_rounds=2)

        self.assertIn("Observation 1", context)
        self.assertIn("Tool: read_file", context)
        self.assertIn("Path: ExampleService.java", context)
        self.assertIn("Truncated: False", context)
        self.assertIn("public class ExampleService {}", context)

    def test_includes_attributed_search_results(self):
        request = RetrievalRequest(
            tool=SEARCH_FILES,
            path=None,
            query="validation",
        )
        state = RetrievalState(
            requests=[request],
            observations=[
                RetrievalObservation(
                    request=request,
                    result=[
                        SearchResult(
                            path="ExampleService.java",
                            match_type=MatchType.CONTENT,
                            line_snippet=LineSnippet(
                                line=7,
                                snippet="validate(input);",
                            ),
                        )
                    ],
                )
            ],
        )

        context = build_retrieval_context(state, remaining_rounds=2)

        self.assertIn("Tool: search_files", context)
        self.assertIn("Query: validation", context)
        self.assertIn("File: ExampleService.java", context)
        self.assertIn("Line 7:", context)
        self.assertIn("validate(input);", context)

    def test_preserves_observation_order(self):
        first_request = RetrievalRequest(
            tool=SEARCH_FILES,
            path=None,
            query="validation",
        )
        second_request = RetrievalRequest(
            tool=READ_FILE,
            path="ExampleService.java",
            query=None,
        )
        state = RetrievalState(
            requests=[first_request, second_request],
            observations=[
                RetrievalObservation(
                    request=first_request,
                    result=[],
                ),
                RetrievalObservation(
                    request=second_request,
                    result={
                        "content": "class ExampleService {}",
                        "truncated": False,
                    },
                ),
            ],
        )

        context = build_retrieval_context(state, remaining_rounds=1)

        first_position = context.index("Observation 1")
        second_position = context.index("Observation 2")
        self.assertLess(first_position, second_position)

    def test_enforces_character_limit_and_marks_truncation(self):
        request = RetrievalRequest(
            tool=READ_FILE,
            path="LargeService.java",
            query=None,
        )
        state = RetrievalState(
            requests=[request],
            observations=[
                RetrievalObservation(
                    request=request,
                    result={
                        "content": "x" * 1_000,
                        "truncated": False,
                    },
                )
            ],
        )

        context = build_retrieval_context(
            state,
            remaining_rounds=2,
            max_chars=200,
        )

        self.assertLessEqual(len(context), 200)
        self.assertIn("Retrieval context truncated: True", context)


class RunRetrievalLoopTests(unittest.TestCase):
    def setUp(self):
        resolver = patch(
            "cd_assist.retrieval.resolve_retrieval_request",
            side_effect=lambda workspace, request: request,
        )
        resolver.start()
        self.addCleanup(resolver.stop)
        self.interpretation = TaskInterpretation(
            intent="find_bugs",
            target="UserService.java",
            search_terms=["UserService", "validation"],
        )
        self.initial_request = RetrievalRequest(
            tool=READ_FILE,
            path="UserService.java",
            query=None,
        )

    @patch("cd_assist.retrieval.build_retrieval_context", return_value="context")
    @patch("cd_assist.retrieval.execute_retrieval")
    def test_records_initial_observation_and_stops_with_sufficient_evidence(
        self,
        execute_retrieval,
        build_retrieval_context,
    ):
        tool_result = {"content": "class UserService {}", "truncated": False}
        execute_retrieval.return_value = tool_result
        decide_next = Mock(
            return_value=RetrievalDecision(
                action="stop",
                tool=None,
                path=None,
                query=None,
                stop_reason="sufficient_evidence",
                reason="The target file was read.",
            )
        )

        state = run_retrieval_loop(
            "workspace",
            self.interpretation,
            self.initial_request,
            decide_next,
        )

        self.assertEqual(StopReason.SUFFICIENT_EVIDENCE, state.stop_reason)
        self.assertEqual([self.initial_request], state.requests)
        self.assertEqual(tool_result, state.observations[0].result)
        build_retrieval_context.assert_called_once_with(state, 2)
        decide_next.assert_called_once_with(self.interpretation, "context")

    @patch("cd_assist.retrieval.build_retrieval_context", return_value="context")
    @patch("cd_assist.retrieval.execute_retrieval", return_value=[])
    def test_stops_with_no_results(
        self,
        execute_retrieval,
        build_retrieval_context,
    ):
        decide_next = Mock(
            return_value=RetrievalDecision(
                action="stop",
                tool=None,
                path=None,
                query=None,
                stop_reason="no_results",
                reason="The retrieval returned no results.",
            )
        )

        state = run_retrieval_loop(
            "workspace",
            self.interpretation,
            self.initial_request,
            decide_next,
        )

        self.assertEqual(StopReason.NO_RESULTS, state.stop_reason)

    @patch("cd_assist.retrieval.build_retrieval_context", return_value="context")
    @patch("cd_assist.retrieval.execute_retrieval")
    def test_executes_second_distinct_request_in_order(
        self,
        execute_retrieval,
        build_retrieval_context,
    ):
        second_request = RetrievalRequest(
            tool=SEARCH_FILES,
            path=None,
            query="validation",
        )
        execute_retrieval.side_effect = ["first result", "second result"]
        decide_next = Mock(
            side_effect=[
                RetrievalDecision(
                    action="retrieve",
                    tool=second_request.tool,
                    path=second_request.path,
                    query=second_request.query,
                    stop_reason=None,
                    reason="Related validation code is still needed.",
                ),
                RetrievalDecision(
                    action="stop",
                    tool=None,
                    path=None,
                    query=None,
                    stop_reason="sufficient_evidence",
                    reason="Both relevant sources were retrieved.",
                ),
            ]
        )

        state = run_retrieval_loop(
            "workspace",
            self.interpretation,
            self.initial_request,
            decide_next,
        )

        self.assertEqual([self.initial_request, second_request], state.requests)
        self.assertEqual(
            ["first result", "second result"],
            [observation.result for observation in state.observations],
        )
        self.assertEqual(StopReason.SUFFICIENT_EVIDENCE, state.stop_reason)

    @patch("cd_assist.retrieval.build_retrieval_context", return_value="context")
    @patch("cd_assist.retrieval.execute_retrieval", return_value="result")
    def test_stops_before_executing_repeated_request(
        self,
        execute_retrieval,
        build_retrieval_context,
    ):
        decide_next = Mock(
            return_value=RetrievalDecision(
                action="retrieve",
                tool=self.initial_request.tool,
                path=self.initial_request.path,
                query=self.initial_request.query,
                stop_reason=None,
                reason="Read the same file again.",
            )
        )

        state = run_retrieval_loop(
            "workspace",
            self.interpretation,
            self.initial_request,
            decide_next,
        )

        self.assertEqual(StopReason.REPEATED_REQUEST, state.stop_reason)
        execute_retrieval.assert_called_once_with("workspace", self.initial_request)
        self.assertEqual(1, len(state.requests))

    @patch("cd_assist.retrieval.build_retrieval_context", return_value="context")
    @patch("cd_assist.retrieval.execute_retrieval", return_value="result")
    def test_never_executes_more_than_max_rounds(
        self,
        execute_retrieval,
        build_retrieval_context,
    ):
        decisions = [
            RetrievalDecision(
                action="retrieve",
                tool=SEARCH_FILES,
                path=None,
                query="validation",
                stop_reason=None,
                reason="Search validation.",
            ),
            RetrievalDecision(
                action="retrieve",
                tool=SEARCH_FILES,
                path=None,
                query="authentication",
                stop_reason=None,
                reason="Search authentication.",
            ),
        ]
        decide_next = Mock(side_effect=decisions)

        state = run_retrieval_loop(
            "workspace",
            self.interpretation,
            self.initial_request,
            decide_next,
            max_rounds=2,
        )

        self.assertEqual(StopReason.MAX_ROUNDS, state.stop_reason)
        self.assertEqual(2, execute_retrieval.call_count)
        self.assertEqual(2, len(state.requests))

    @patch("cd_assist.retrieval.build_retrieval_context")
    @patch("cd_assist.retrieval.execute_retrieval")
    def test_stops_on_tool_error_without_recording_observation(
        self,
        execute_retrieval,
        build_retrieval_context,
    ):
        execute_retrieval.side_effect = FileParseError("missing file")
        decide_next = Mock()

        state = run_retrieval_loop(
            "workspace",
            self.interpretation,
            self.initial_request,
            decide_next,
        )

        self.assertEqual(StopReason.TOOL_ERROR, state.stop_reason)
        self.assertEqual([], state.requests)
        self.assertEqual([], state.observations)
        build_retrieval_context.assert_not_called()
        decide_next.assert_not_called()

    @patch("cd_assist.retrieval.build_retrieval_context", return_value="context")
    @patch("cd_assist.retrieval.execute_retrieval", return_value="result")
    def test_stops_on_agent_error(
        self,
        execute_retrieval,
        build_retrieval_context,
    ):
        decide_next = Mock(side_effect=ModelResponseError("model failed"))

        state = run_retrieval_loop(
            "workspace",
            self.interpretation,
            self.initial_request,
            decide_next,
        )

        self.assertEqual(StopReason.AGENT_ERROR, state.stop_reason)
        self.assertEqual(1, len(state.observations))


class RetrievalResolutionLoopTests(unittest.TestCase):
    def setUp(self):
        self.interpretation = TaskInterpretation(
            intent=TaskIntent.GENERATE_TESTS,
            target="Calculator.java",
            search_terms=["Calculator.java"],
        )

    @patch("cd_assist.retrieval.build_retrieval_context", return_value="context")
    @patch("cd_assist.retrieval.execute_retrieval", return_value=[])
    def test_resolves_missing_initial_read_before_execution(
        self,
        execute_retrieval,
        build_retrieval_context,
    ):
        initial_request = RetrievalRequest(
            tool=READ_FILE,
            path="Calculator.java",
            query=None,
        )
        decide_next = Mock(
            return_value=RetrievalDecision(
                action="stop",
                tool=None,
                path=None,
                query=None,
                stop_reason="no_results",
                reason="No matching file was found.",
            )
        )

        state = run_retrieval_loop(
            FIXTURE_WORKSPACE,
            self.interpretation,
            initial_request,
            decide_next,
        )

        effective_request = RetrievalRequest(
            tool=SEARCH_FILES,
            path=None,
            query="Calculator.java",
        )
        self.assertEqual([effective_request], state.requests)
        self.assertEqual(effective_request, state.observations[0].request)
        execute_retrieval.assert_called_once_with(
            FIXTURE_WORKSPACE,
            effective_request,
        )
        self.assertEqual(StopReason.NO_RESULTS, state.stop_reason)

    @patch("cd_assist.retrieval.build_retrieval_context", return_value="context")
    @patch("cd_assist.retrieval.execute_retrieval", side_effect=[[], []])
    def test_resolves_missing_subsequent_read_before_execution(
        self,
        execute_retrieval,
        build_retrieval_context,
    ):
        initial_request = RetrievalRequest(
            tool=SEARCH_FILES,
            path=None,
            query="Calculator",
        )
        decide_next = Mock(
            side_effect=[
                RetrievalDecision(
                    action="retrieve",
                    tool=READ_FILE,
                    path="Calculator.java",
                    query=None,
                    stop_reason=None,
                    reason="Read the discovered target.",
                ),
                RetrievalDecision(
                    action="stop",
                    tool=None,
                    path=None,
                    query=None,
                    stop_reason="no_results",
                    reason="No exact file was found.",
                ),
            ]
        )

        state = run_retrieval_loop(
            FIXTURE_WORKSPACE,
            self.interpretation,
            initial_request,
            decide_next,
        )

        resolved_request = RetrievalRequest(
            tool=SEARCH_FILES,
            path=None,
            query="Calculator.java",
        )
        self.assertEqual([initial_request, resolved_request], state.requests)
        self.assertEqual(
            [initial_request, resolved_request],
            [call.args[1] for call in execute_retrieval.call_args_list],
        )

    @patch("cd_assist.retrieval.build_retrieval_context", return_value="context")
    @patch("cd_assist.retrieval.execute_retrieval", return_value=[])
    def test_repeat_detection_uses_effective_request(
        self,
        execute_retrieval,
        build_retrieval_context,
    ):
        initial_request = RetrievalRequest(
            tool=READ_FILE,
            path="Calculator.java",
            query=None,
        )
        decide_next = Mock(
            return_value=RetrievalDecision(
                action="retrieve",
                tool=READ_FILE,
                path="Calculator.java",
                query=None,
                stop_reason=None,
                reason="Try the same unresolved file again.",
            )
        )

        state = run_retrieval_loop(
            FIXTURE_WORKSPACE,
            self.interpretation,
            initial_request,
            decide_next,
        )

        self.assertEqual(StopReason.REPEATED_REQUEST, state.stop_reason)
        self.assertEqual(1, execute_retrieval.call_count)
        self.assertEqual(1, len(state.requests))

    def test_existing_initial_path_remains_a_read(self):
        initial_request = RetrievalRequest(
            tool=READ_FILE,
            path="ExampleService.java",
            query=None,
        )
        decide_next = Mock(
            return_value=RetrievalDecision(
                action="stop",
                tool=None,
                path=None,
                query=None,
                stop_reason="sufficient_evidence",
                reason="The target file was read.",
            )
        )

        state = run_retrieval_loop(
            FIXTURE_WORKSPACE,
            self.interpretation,
            initial_request,
            decide_next,
        )

        self.assertEqual([initial_request], state.requests)
        self.assertEqual(READ_FILE, state.observations[0].request.tool)
        self.assertEqual(StopReason.SUFFICIENT_EVIDENCE, state.stop_reason)


class RetrievalLoopIntegrationTests(unittest.TestCase):
    def test_reads_fixture_builds_context_and_passes_it_to_decider(self):
        interpretation = TaskInterpretation(
            intent="find_bugs",
            target="ExampleService.java",
            search_terms=["ExampleService"],
        )
        initial_request = RetrievalRequest(
            tool=READ_FILE,
            path="ExampleService.java",
            query=None,
        )
        received_contexts = []

        def decide_next(received_interpretation, retrieval_context):
            self.assertIs(interpretation, received_interpretation)
            received_contexts.append(retrieval_context)

            return RetrievalDecision(
                action="stop",
                tool=None,
                path=None,
                query=None,
                stop_reason="sufficient_evidence",
                reason="The requested file was retrieved.",
            )

        state = run_retrieval_loop(
            FIXTURE_WORKSPACE,
            interpretation,
            initial_request,
            decide_next,
        )

        self.assertEqual(StopReason.SUFFICIENT_EVIDENCE, state.stop_reason)
        self.assertEqual([initial_request], state.requests)
        self.assertEqual(1, len(state.observations))
        self.assertEqual(1, len(received_contexts))

        retrieval_context = received_contexts[0]
        self.assertIn("Remaining retrieval rounds: 2", retrieval_context)
        self.assertIn("Tool: read_file", retrieval_context)
        self.assertIn("Path: ExampleService.java", retrieval_context)
        self.assertIn("public class ExampleService", retrieval_context)


if __name__ == "__main__":
    unittest.main()
