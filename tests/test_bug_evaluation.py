import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from unittest.mock import Mock

from cd_assist.agent import CodingAssistantAgent
from cd_assist.models import (
    BugAnalysis,
    BugFinding,
    EvidenceItem,
    EvidenceSet,
    READ_FILE,
    SEARCH_FILES,
    RetrievalDecision,
    RetrievalRequest,
    TaskInterpretation,
)


FIXTURE_WORKSPACE = Path(__file__).parent / "fixtures"
ExpectedOutcome = Literal["finding", "no_bug", "insufficient_evidence"]


@dataclass(frozen=True)
class BugEvaluationCase:
    name: str
    request: str
    expected_outcome: ExpectedOutcome
    expected_paths: tuple[str, ...] = ()
    expected_line_ranges: tuple[tuple[int, int], ...] = ()
    expected_evidence_paths: tuple[str, ...] = ()
    required_concept_groups: tuple[tuple[str, ...], ...] = ()
    forbidden_concepts: tuple[str, ...] = ()


PAYMENT_CASE = BugEvaluationCase(
    name="inverted_payment_balance_check",
    request="Find bugs related to payment authorization",
    expected_outcome="finding",
    expected_paths=("PaymentAuthorizationService.java",),
    expected_line_ranges=((8, 10),),
    expected_evidence_paths=("PaymentAuthorizationService.java",),
    required_concept_groups=(
        ("balance",),
        ("amount", "payment"),
        ("comparison", "reversed", "<="),
    ),
    forbidden_concepts=("naming", "formatting"),
)

TOKEN_CASE = BugEvaluationCase(
    name="inverted_token_expiration_check",
    request="Find bugs in AccessTokenService.java",
    expected_outcome="finding",
    expected_paths=("AccessTokenService.java",),
    expected_line_ranges=((8, 10),),
    expected_evidence_paths=("AccessTokenService.java",),
    required_concept_groups=(
        ("expiration", "expires", "expired"),
        ("valid", "invalid"),
    ),
    forbidden_concepts=("naming", "formatting"),
)

INVENTORY_CASE = BugEvaluationCase(
    name="inventory_is_added_instead_of_subtracted",
    request="Find bugs in the inventory reservation logic",
    expected_outcome="finding",
    expected_paths=("InventoryReservationService.java",),
    expected_line_ranges=((12, 16),),
    expected_evidence_paths=("InventoryReservationService.java",),
    required_concept_groups=(
        ("add", "increase", "+"),
        ("subtract", "decrease"),
        ("stock", "inventory"),
    ),
    forbidden_concepts=("naming", "formatting"),
)

CUSTOMER_GREETING_CASE = BugEvaluationCase(
    name="unsafe_optional_access",
    request="Find bugs in customer greeting generation",
    expected_outcome="finding",
    expected_paths=("CustomerGreetingService.java",),
    expected_line_ranges=((12, 16),),
    expected_evidence_paths=("CustomerGreetingService.java",),
    required_concept_groups=(
        ("optional",),
        ("empty", "missing"),
        ("exception", "fail"),
    ),
    forbidden_concepts=("naming", "formatting"),
)

AMBIGUOUS_DISPLAY_NAME_CASE = BugEvaluationCase(
    name="unproven_nullable_display_name",
    request="Find bugs in the method that builds a user's display name",
    expected_outcome="insufficient_evidence",
    expected_evidence_paths=("ExampleService.java",),
    required_concept_groups=(
        ("not established", "does not establish", "no evidence"),
    ),
    forbidden_concepts=("repository returns null names",),
)

RETRY_POLICY_CASE = BugEvaluationCase(
    name="retry_policy_requires_a_contract",
    request="Find bugs in RetryPolicy.java",
    expected_outcome="insufficient_evidence",
    expected_evidence_paths=("RetryPolicy.java",),
    required_concept_groups=(("requirements", "contract"),),
    forbidden_concepts=("three attempts is definitely wrong",),
)

STYLE_ONLY_CASE = BugEvaluationCase(
    name="style_is_not_a_bug",
    request="Find bugs in StyleOnlyService.java",
    expected_outcome="no_bug",
    expected_evidence_paths=("StyleOnlyService.java",),
    forbidden_concepts=("uppercase method name is a bug",),
)

UNKNOWN_DOMAIN_CASE = BugEvaluationCase(
    name="unknown_shipment_tracking_domain",
    request="Find bugs related to shipment tracking",
    expected_outcome="insufficient_evidence",
    required_concept_groups=(
        ("no repository evidence", "no evidence was supplied", "no evidence"),
    ),
    forbidden_concepts=("ShipmentService.java",),
)

BUG_EVALUATION_CASES = (
    PAYMENT_CASE,
    TOKEN_CASE,
    INVENTORY_CASE,
    CUSTOMER_GREETING_CASE,
    AMBIGUOUS_DISPLAY_NAME_CASE,
    RETRY_POLICY_CASE,
    STYLE_ONLY_CASE,
    UNKNOWN_DOMAIN_CASE,
)


def assert_case_matches(
    test: unittest.TestCase,
    case: BugEvaluationCase,
    evidence_set: EvidenceSet,
    analysis: BugAnalysis,
) -> None:
    evidence_paths = {item.path for item in evidence_set.items}

    for expected_path in case.expected_evidence_paths:
        test.assertIn(expected_path, evidence_paths)

    analysis.validate_evidence_references(evidence_set)

    if case.expected_outcome == "finding":
        test.assertTrue(analysis.findings)
        matching_findings = [
            finding
            for finding in analysis.findings
            if finding.path in case.expected_paths
        ]
        test.assertTrue(
            matching_findings,
            f"No finding used an expected path for case {case.name}",
        )

        if case.expected_line_ranges:
            test.assertTrue(
                any(
                    finding.start_line is not None
                    and any(
                        start <= finding.start_line <= end
                        for start, end in case.expected_line_ranges
                    )
                    for finding in matching_findings
                ),
                f"No finding used an expected line for case {case.name}",
            )

        output_text = " ".join(
            f"{finding.reasoning} {finding.impact}".lower()
            for finding in matching_findings
        )
    else:
        test.assertEqual([], analysis.findings)
        test.assertIsNotNone(analysis.insufficient_evidence_reason)
        output_text = analysis.insufficient_evidence_reason.lower()

    for alternatives in case.required_concept_groups:
        test.assertTrue(
            any(concept.lower() in output_text for concept in alternatives),
            f"None of {alternatives!r} appeared for case {case.name}",
        )

    for concept in case.forbidden_concepts:
        test.assertNotIn(concept.lower(), output_text)


def make_evidence_set(*paths: str) -> EvidenceSet:
    items = [
        EvidenceItem(
            path=path,
            start_line=1,
            content="class Fixture {}",
            source=READ_FILE,
            truncated=False,
        )
        for path in paths
    ]
    context_str = "\n\n".join(
        f"Evidence {index}: \n{item.to_context_string()}"
        for index, item in enumerate(items)
    )
    return EvidenceSet(items=items, truncated=False, context_str=context_str)


FINDING_DETAILS = {
    PAYMENT_CASE.name: (
        9,
        "The balance and amount comparison is reversed.",
        "The service can authorize an amount above the account balance.",
    ),
    TOKEN_CASE.name: (
        9,
        "The expiration comparison treats an expired token as valid.",
        "Expired tokens remain valid while unexpired tokens are rejected.",
    ),
    INVENTORY_CASE.name: (
        14,
        "The method adds requested units instead of subtracting them.",
        "Reported stock increases after an inventory reservation.",
    ),
    CUSTOMER_GREETING_CASE.name: (
        15,
        "Calling get on an empty Optional throws an exception.",
        "A missing customer causes greeting generation to fail.",
    ),
}


NEGATIVE_REASONS = {
    AMBIGUOUS_DISPLAY_NAME_CASE.name:
        "Null name inputs and required fallback behavior are not established.",
    RETRY_POLICY_CASE.name:
        "The requirements do not establish the correct retry count.",
    STYLE_ONLY_CASE.name:
        "The evidence shows unusual style but no incorrect behavior.",
    UNKNOWN_DOMAIN_CASE.name:
        "There is no repository evidence for shipment tracking.",
}


def make_analysis(
    case: BugEvaluationCase,
    evidence_set: EvidenceSet,
) -> BugAnalysis:
    if case.expected_outcome != "finding":
        return BugAnalysis(
            findings=[],
            insufficient_evidence_reason=NEGATIVE_REASONS[case.name],
        )

    path = case.expected_paths[0]
    evidence_index = next(
        index
        for index, item in enumerate(evidence_set.items)
        if item.path == path
    )
    start_line, reasoning, impact = FINDING_DETAILS[case.name]
    return BugAnalysis(
        findings=[
            BugFinding(
                path=path,
                start_line=start_line,
                reasoning=reasoning,
                impact=impact,
                confidence="high",
                evidence_indices=[evidence_index],
            )
        ],
        insufficient_evidence_reason=None,
    )


class BugEvaluationCaseDefinitionTests(unittest.TestCase):
    def test_case_definitions_are_complete_and_unique(self):
        names = set()

        for case in BUG_EVALUATION_CASES:
            with self.subTest(case=case.name):
                self.assertNotIn(case.name, names)
                self.assertTrue(case.request.strip())
                if case.expected_outcome == "finding":
                    self.assertTrue(case.expected_paths)
                    self.assertTrue(case.expected_line_ranges)
                    self.assertTrue(case.expected_evidence_paths)
                names.add(case.name)


class EvaluationScorerTests(unittest.TestCase):
    def test_accepts_all_expected_case_outcomes(self):
        for case in BUG_EVALUATION_CASES:
            with self.subTest(case=case.name):
                evidence_set = make_evidence_set(*case.expected_evidence_paths)
                analysis = make_analysis(case, evidence_set)

                assert_case_matches(self, case, evidence_set, analysis)

    def test_rejects_missing_expected_evidence(self):
        evidence_set = make_evidence_set("OtherService.java")

        with self.assertRaises(AssertionError):
            assert_case_matches(
                self,
                PAYMENT_CASE,
                evidence_set,
                BugAnalysis(
                    findings=[],
                    insufficient_evidence_reason="No evidence was found.",
                ),
            )

    def test_rejects_missing_expected_finding(self):
        evidence_set = make_evidence_set("PaymentAuthorizationService.java")

        with self.assertRaises(AssertionError):
            assert_case_matches(
                self,
                PAYMENT_CASE,
                evidence_set,
                BugAnalysis(
                    findings=[],
                    insufficient_evidence_reason="No defect was found.",
                ),
            )

    def test_rejects_finding_for_negative_case(self):
        evidence_set = make_evidence_set("StyleOnlyService.java")
        analysis = BugAnalysis(
            findings=[
                BugFinding(
                    path="StyleOnlyService.java",
                    start_line=4,
                    reasoning="The uppercase method name is a bug.",
                    impact="Formatting is unconventional.",
                    confidence="low",
                    evidence_indices=[0],
                )
            ],
            insufficient_evidence_reason=None,
        )

        with self.assertRaises(AssertionError):
            assert_case_matches(self, STYLE_ONLY_CASE, evidence_set, analysis)

    def test_rejects_finding_outside_expected_line_range(self):
        evidence_set = make_evidence_set("PaymentAuthorizationService.java")
        analysis = make_analysis(PAYMENT_CASE, evidence_set)
        analysis.findings[0].start_line = 99

        with self.assertRaisesRegex(AssertionError, "expected line"):
            assert_case_matches(self, PAYMENT_CASE, evidence_set, analysis)

    def test_rejects_finding_missing_required_concept(self):
        evidence_set = make_evidence_set("PaymentAuthorizationService.java")
        analysis = make_analysis(PAYMENT_CASE, evidence_set)
        analysis.findings[0].reasoning = "The condition is reversed."
        analysis.findings[0].impact = "Some requests receive the wrong result."

        with self.assertRaises(AssertionError):
            assert_case_matches(self, PAYMENT_CASE, evidence_set, analysis)

    def test_rejects_finding_containing_forbidden_claim(self):
        evidence_set = make_evidence_set("PaymentAuthorizationService.java")
        analysis = make_analysis(PAYMENT_CASE, evidence_set)
        analysis.findings[0].reasoning += " The naming is also incorrect."

        with self.assertRaises(AssertionError):
            assert_case_matches(self, PAYMENT_CASE, evidence_set, analysis)

    def test_rejects_finding_referencing_wrong_evidence_item(self):
        evidence_set = make_evidence_set(
            "PaymentAuthorizationService.java",
            "OtherService.java",
        )
        analysis = make_analysis(PAYMENT_CASE, evidence_set)
        analysis.findings[0].evidence_indices = [1]

        with self.assertRaisesRegex(ValueError, "path does not match"):
            assert_case_matches(self, PAYMENT_CASE, evidence_set, analysis)

    def test_rejects_finding_for_insufficient_evidence_case(self):
        evidence_set = make_evidence_set("ExampleService.java")
        analysis = BugAnalysis(
            findings=[
                BugFinding(
                    path="ExampleService.java",
                    start_line=20,
                    reasoning="The repository returns null names.",
                    impact="The display name contains null text.",
                    confidence="medium",
                    evidence_indices=[0],
                )
            ],
            insufficient_evidence_reason=None,
        )

        with self.assertRaises(AssertionError):
            assert_case_matches(
                self,
                AMBIGUOUS_DISPLAY_NAME_CASE,
                evidence_set,
                analysis,
            )


@dataclass(frozen=True)
class AgentEvaluationScenario:
    case: BugEvaluationCase
    interpretation: TaskInterpretation
    initial_request: RetrievalRequest
    next_decisions: tuple[RetrievalDecision, ...]


AGENT_SCENARIOS = (
    AgentEvaluationScenario(
        case=PAYMENT_CASE,
        interpretation=TaskInterpretation(
            intent="find_bugs",
            target="PaymentAuthorizationService.java",
            search_terms=["payment"],
        ),
        initial_request=RetrievalRequest(
            tool=READ_FILE,
            path="PaymentAuthorizationService.java",
            query=None,
        ),
        next_decisions=(
            RetrievalDecision(
                action="stop",
                tool=None,
                path=None,
                query=None,
                stop_reason="sufficient_evidence",
                reason="The target implementation was read.",
            ),
        ),
    ),
    AgentEvaluationScenario(
        case=INVENTORY_CASE,
        interpretation=TaskInterpretation(
            intent="find_bugs",
            target=None,
            search_terms=["inventory"],
        ),
        initial_request=RetrievalRequest(
            tool=SEARCH_FILES,
            path=None,
            query="inventory",
        ),
        next_decisions=(
            RetrievalDecision(
                action="retrieve",
                tool=READ_FILE,
                path="InventoryReservationService.java",
                query=None,
                stop_reason=None,
                reason="Read the file identified by the search result.",
            ),
            RetrievalDecision(
                action="stop",
                tool=None,
                path=None,
                query=None,
                stop_reason="sufficient_evidence",
                reason="The relevant implementation was read.",
            ),
        ),
    ),
    AgentEvaluationScenario(
        case=STYLE_ONLY_CASE,
        interpretation=TaskInterpretation(
            intent="find_bugs",
            target="StyleOnlyService.java",
            search_terms=["StyleOnlyService"],
        ),
        initial_request=RetrievalRequest(
            tool=READ_FILE,
            path="StyleOnlyService.java",
            query=None,
        ),
        next_decisions=(
            RetrievalDecision(
                action="stop",
                tool=None,
                path=None,
                query=None,
                stop_reason="sufficient_evidence",
                reason="The target implementation was read.",
            ),
        ),
    ),
)


class AgentBugEvaluationTests(unittest.TestCase):
    def test_representative_cases_through_real_agent_pipeline(self):
        for scenario in AGENT_SCENARIOS:
            with self.subTest(case=scenario.case.name):
                analyzer = Mock(
                    side_effect=lambda _client, evidence_set, _query:
                        make_analysis(scenario.case, evidence_set)
                )
                agent = CodingAssistantAgent(
                    client=object(),
                    workspace=FIXTURE_WORKSPACE,
                    generate_response=Mock(),
                    interpret_intention=Mock(
                        return_value=scenario.interpretation
                    ),
                    select_tool=Mock(return_value=scenario.initial_request),
                    decide_next_retrieval=Mock(
                        side_effect=scenario.next_decisions
                    ),
                    analyze_bugs=analyzer,
                )

                analysis = agent.find_bugs(scenario.case.request)
                evidence_set = analyzer.call_args.args[1]

                assert_case_matches(
                    self,
                    scenario.case,
                    evidence_set,
                    analysis,
                )


if __name__ == "__main__":
    unittest.main()
