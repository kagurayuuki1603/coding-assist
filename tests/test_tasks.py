import unittest

from pydantic import ValidationError

from cd_assist.models import (
    BugAnalysis,
    BugFinding,
    EvidenceItem,
    EvidenceSet,
    READ_FILE,
    SEARCH_FILES,
    RetrievalAction,
    RetrievalDecision,
    RetrievalRequest,
    TaskIntent,
    TaskInterpretation,
)


class TaskInterpretationTests(unittest.TestCase):
    def test_accepts_answer_question_with_no_known_target(self):
        interpretation = TaskInterpretation(
            intent="answer_question",
            search_terms=["user validation"],
        )

        self.assertEqual(TaskIntent.ANSWER_QUESTION, interpretation.intent)
        self.assertIsNone(interpretation.target)
        self.assertEqual(["user validation"], interpretation.search_terms)

    def test_accepts_find_bugs_with_known_target(self):
        interpretation = TaskInterpretation(
            intent="find_bugs",
            target="src/main/java/UserService.java",
            search_terms=["UserService", "validation"],
        )

        self.assertEqual(TaskIntent.FIND_BUGS, interpretation.intent)
        self.assertEqual(
            "src/main/java/UserService.java",
            interpretation.target,
        )

    def test_rejects_unsupported_intent(self):
        with self.assertRaises(ValidationError):
            TaskInterpretation(
                intent="refactor",
                search_terms=["UserService"],
            )

    def test_rejects_empty_search_terms(self):
        with self.assertRaises(ValidationError):
            TaskInterpretation(
                intent="answer_question",
                search_terms=[],
            )

    def test_rejects_more_than_five_search_terms(self):
        with self.assertRaises(ValidationError):
            TaskInterpretation(
                intent="find_bugs",
                search_terms=["one", "two", "three", "four", "five", "six"],
            )

    def test_strips_search_terms_and_target(self):
        interpretation = TaskInterpretation(
            intent="answer_question",
            target="  UserService.java  ",
            search_terms=["  validation  "],
        )

        self.assertEqual("UserService.java", interpretation.target)
        self.assertEqual(["validation"], interpretation.search_terms)

    def test_rejects_blank_search_term(self):
        with self.assertRaises(ValidationError):
            TaskInterpretation(
                intent="answer_question",
                search_terms=["   "],
            )

    def test_rejects_blank_target(self):
        with self.assertRaises(ValidationError):
            TaskInterpretation(
                intent="answer_question",
                target="   ",
                search_terms=["validation"],
            )

    def test_rejects_search_term_longer_than_limit(self):
        with self.assertRaises(ValidationError):
            TaskInterpretation(
                intent="answer_question",
                search_terms=["x" * 101],
            )

    def test_rejects_target_longer_than_limit(self):
        with self.assertRaises(ValidationError):
            TaskInterpretation(
                intent="answer_question",
                target="x" * 501,
                search_terms=["validation"],
            )


class RetrievalRequestTests(unittest.TestCase):
    def test_parses_read_file_request(self):
        request = RetrievalRequest.model_validate(
            {
                "tool": READ_FILE,
                "path": "UserService.java",
                "query": None,
            }
        )

        self.assertEqual(READ_FILE, request.tool)
        self.assertEqual("UserService.java", request.path)
        self.assertIsNone(request.query)

    def test_parses_search_files_request(self):
        request = RetrievalRequest.model_validate(
            {
                "tool": SEARCH_FILES,
                "path": None,
                "query": "user validation",
            }
        )

        self.assertEqual(SEARCH_FILES, request.tool)
        self.assertIsNone(request.path)
        self.assertEqual("user validation", request.query)

    def test_generates_object_schema_at_root(self):
        schema = RetrievalRequest.model_json_schema()

        self.assertEqual("object", schema["type"])
        self.assertIn("tool", schema["properties"])
        self.assertNotIn("oneOf", schema)

    def test_rejects_unknown_tool(self):
        with self.assertRaises(ValidationError):
            RetrievalRequest.model_validate(
                {
                    "tool": "delete_file",
                    "path": "UserService.java",
                    "query": None,
                }
            )

    def test_rejects_query_for_read_file(self):
        with self.assertRaises(ValidationError):
            RetrievalRequest.model_validate(
                {
                    "tool": READ_FILE,
                    "path": "UserService.java",
                    "query": "validation",
                }
            )

    def test_rejects_path_for_search_files(self):
        with self.assertRaises(ValidationError):
            RetrievalRequest.model_validate(
                {
                    "tool": SEARCH_FILES,
                    "path": "UserService.java",
                    "query": "validation",
                }
            )

    def test_rejects_missing_path_for_read_file(self):
        with self.assertRaises(ValidationError):
            RetrievalRequest.model_validate(
                {
                    "tool": READ_FILE,
                    "path": None,
                    "query": None,
                }
            )

    def test_rejects_missing_query_for_search_files(self):
        with self.assertRaises(ValidationError):
            RetrievalRequest.model_validate(
                {
                    "tool": SEARCH_FILES,
                    "path": None,
                    "query": None,
                }
            )


class RetrievalDecisionTests(unittest.TestCase):
    def test_accepts_stop_without_tool_arguments(self):
        decision = RetrievalDecision(
            action="stop",
            tool=None,
            path=None,
            query=None,
            stop_reason="sufficient_evidence",
            reason="Enough evidence was collected.",
        )

        self.assertEqual(RetrievalAction.STOP, decision.action)
        self.assertEqual("Enough evidence was collected.", decision.reason)

    def test_accepts_read_file_retrieval(self):
        decision = RetrievalDecision(
            action="retrieve",
            tool=READ_FILE,
            path="UserService.java",
            query=None,
            stop_reason=None,
            reason="The target file has not been read.",
        )

        self.assertEqual(RetrievalAction.RETRIEVE, decision.action)
        self.assertEqual(READ_FILE, decision.tool)
        self.assertEqual("UserService.java", decision.path)

    def test_accepts_search_files_retrieval(self):
        decision = RetrievalDecision(
            action="retrieve",
            tool=SEARCH_FILES,
            path=None,
            query="user validation",
            stop_reason=None,
            reason="The relevant file is unknown.",
        )

        self.assertEqual(SEARCH_FILES, decision.tool)
        self.assertEqual("user validation", decision.query)

    def test_strips_reason_whitespace(self):
        decision = RetrievalDecision(
            action="stop",
            tool=None,
            path=None,
            query=None,
            stop_reason="sufficient_evidence",
            reason="  Enough evidence.  ",
        )

        self.assertEqual("Enough evidence.", decision.reason)

    def test_rejects_blank_reason(self):
        with self.assertRaises(ValidationError):
            RetrievalDecision(
                action="stop",
                tool=None,
                path=None,
                query=None,
                stop_reason="sufficient_evidence",
                reason="   ",
            )

    def test_rejects_reason_longer_than_limit(self):
        with self.assertRaises(ValidationError):
            RetrievalDecision(
                action="stop",
                tool=None,
                path=None,
                query=None,
                stop_reason="sufficient_evidence",
                reason="x" * 301,
            )

    def test_rejects_tool_arguments_for_stop(self):
        invalid_arguments = [
            {"tool": READ_FILE, "path": None, "query": None},
            {"tool": None, "path": "UserService.java", "query": None},
            {"tool": None, "path": None, "query": "validation"},
        ]

        for arguments in invalid_arguments:
            with self.subTest(arguments=arguments):
                with self.assertRaises(ValidationError):
                    RetrievalDecision(
                        action="stop",
                        stop_reason="sufficient_evidence",
                        reason="Stop now.",
                        **arguments,
                    )

    def test_rejects_retrieve_without_tool(self):
        with self.assertRaises(ValidationError):
            RetrievalDecision(
                action="retrieve",
                tool=None,
                path=None,
                query=None,
                stop_reason=None,
                reason="More evidence is needed.",
            )

    def test_rejects_read_file_without_path(self):
        with self.assertRaises(ValidationError):
            RetrievalDecision(
                action="retrieve",
                tool=READ_FILE,
                path=None,
                query=None,
                stop_reason=None,
                reason="Read the target file.",
            )


class BugAnalysisTests(unittest.TestCase):
    def make_finding(self, **overrides):
        values = {
            "path": "ExampleService.java",
            "start_line": 12,
            "reasoning": "A missing name component produces invalid output.",
            "impact": "Users can receive an incomplete display name.",
            "confidence": "high",
            "evidence_indices": [0],
        }
        values.update(overrides)
        return BugFinding(**values)

    def make_evidence_set(self):
        return EvidenceSet(
            items=[
                EvidenceItem(
                    path="ExampleService.java",
                    start_line=1,
                    content="class ExampleService {}",
                    source=READ_FILE,
                    truncated=False,
                )
            ],
            truncated=False,
        )

    def test_accepts_finding_with_required_bug_details(self):
        finding = self.make_finding()
        analysis = BugAnalysis(
            findings=[finding],
            insufficient_evidence_reason=None,
        )

        self.assertEqual([finding], analysis.findings)
        self.assertIsNone(analysis.insufficient_evidence_reason)

    def test_accepts_explicit_insufficient_evidence_result(self):
        analysis = BugAnalysis(
            findings=[],
            insufficient_evidence_reason="The retrieved code shows no defect.",
        )

        self.assertEqual([], analysis.findings)
        self.assertIn("Insufficient Evidence", analysis.to_console_string())

    def test_rejects_finding_without_supporting_evidence(self):
        with self.assertRaises(ValidationError):
            self.make_finding(evidence_indices=[])

    def test_rejects_negative_evidence_index(self):
        with self.assertRaises(ValidationError):
            self.make_finding(evidence_indices=[-1])

    def test_rejects_invalid_confidence(self):
        with self.assertRaises(ValidationError):
            self.make_finding(confidence="certain")

    def test_rejects_blank_bug_details(self):
        for field_name in ("path", "reasoning", "impact"):
            with self.subTest(field_name=field_name):
                with self.assertRaises(ValidationError):
                    self.make_finding(**{field_name: "   "})

    def test_rejects_non_positive_start_line(self):
        with self.assertRaises(ValidationError):
            self.make_finding(start_line=0)

    def test_rejects_findings_with_insufficient_evidence_reason(self):
        with self.assertRaises(ValidationError):
            BugAnalysis(
                findings=[self.make_finding()],
                insufficient_evidence_reason="Not enough evidence.",
            )

    def test_requires_reason_when_no_findings_exist(self):
        with self.assertRaises(ValidationError):
            BugAnalysis(findings=[], insufficient_evidence_reason=None)

    def test_rejects_out_of_range_evidence_reference(self):
        analysis = BugAnalysis(
            findings=[self.make_finding(evidence_indices=[1])],
            insufficient_evidence_reason=None,
        )

        with self.assertRaisesRegex(ValueError, "Invalid evidence index: 1"):
            analysis.validate_evidence_references(self.make_evidence_set())

    def test_rejects_evidence_from_different_path(self):
        analysis = BugAnalysis(
            findings=[self.make_finding(path="OtherService.java")],
            insufficient_evidence_reason=None,
        )

        with self.assertRaisesRegex(ValueError, "path does not match"):
            analysis.validate_evidence_references(self.make_evidence_set())

    def test_accepts_matching_evidence_reference(self):
        analysis = BugAnalysis(
            findings=[self.make_finding()],
            insufficient_evidence_reason=None,
        )

        result = analysis.validate_evidence_references(self.make_evidence_set())

        self.assertIs(analysis, result)

    def test_formats_supported_findings_for_console(self):
        analysis = BugAnalysis(
            findings=[self.make_finding()],
            insufficient_evidence_reason=None,
        )

        output = analysis.to_console_string()

        self.assertIn("Findings:", output)
        self.assertIn("ExampleService.java", output)
        self.assertIn("Sufficient evidence found.", output)


class RetrievalDecisionAdditionalTests(unittest.TestCase):
    def test_rejects_query_for_read_file(self):
        with self.assertRaises(ValidationError):
            RetrievalDecision(
                action="retrieve",
                tool=READ_FILE,
                path="UserService.java",
                query="validation",
                stop_reason=None,
                reason="Read the target file.",
            )

    def test_rejects_search_files_without_query(self):
        with self.assertRaises(ValidationError):
            RetrievalDecision(
                action="retrieve",
                tool=SEARCH_FILES,
                path=None,
                query=None,
                stop_reason=None,
                reason="Search for relevant files.",
            )

    def test_rejects_path_for_search_files(self):
        with self.assertRaises(ValidationError):
            RetrievalDecision(
                action="retrieve",
                tool=SEARCH_FILES,
                path="UserService.java",
                query="validation",
                stop_reason=None,
                reason="Search for relevant files.",
            )

    def test_rejects_stop_without_stop_reason(self):
        with self.assertRaises(ValidationError):
            RetrievalDecision(
                action="stop",
                tool=None,
                path=None,
                query=None,
                stop_reason=None,
                reason="Stop now.",
            )

    def test_rejects_stop_reason_for_retrieve(self):
        with self.assertRaises(ValidationError):
            RetrievalDecision(
                action="retrieve",
                tool=READ_FILE,
                path="UserService.java",
                query=None,
                stop_reason="sufficient_evidence",
                reason="Read the target file.",
            )

if __name__ == "__main__":
    unittest.main()
