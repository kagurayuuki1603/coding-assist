import unittest

from pydantic import ValidationError

from cd_assist.models import (
    READ_FILE,
    EvidenceItem,
    EvidenceSet,
    TaskIntent,
    TaskInterpretation,
)
from cd_assist.refactoring import (
    MAX_REFACTOR_EVIDENCE_CHARS,
    MAX_REFACTOR_EVIDENCE_ITEMS,
    BehaviorInvariant,
    InvariantBasis,
    RefactorContext,
    RefactorContextStatus,
    RefactorEvidenceReference,
    RefactorEvidenceRole,
)


class RefactorContextTests(unittest.TestCase):
    def make_interpretation(self, **overrides):
        values = {
            "intent": TaskIntent.REFACTOR,
            "target": "RetryPolicy.shouldRetry",
            "search_terms": ["RetryPolicy.shouldRetry"],
            "constraints": ["Keep the public signature unchanged."],
        }
        values.update(overrides)
        return TaskInterpretation(**values)

    def make_evidence(self):
        return EvidenceSet(
            items=[
                EvidenceItem(
                    path="src/main/java/com/example/RetryPolicy.java",
                    start_line=8,
                    content="boolean shouldRetry(int attempt, boolean transientFailure) {}",
                    source=READ_FILE,
                    truncated=False,
                ),
                EvidenceItem(
                    path="src/test/java/com/example/RetryPolicyTest.java",
                    start_line=10,
                    content="void shouldRetryStopsAtMaximumAttempts() {}",
                    source=READ_FILE,
                    truncated=False,
                ),
                EvidenceItem(
                    path="src/main/java/com/example/RetryRunner.java",
                    start_line=20,
                    content="retryPolicy.shouldRetry(attempt, transientFailure);",
                    source=READ_FILE,
                    truncated=False,
                ),
            ],
            truncated=False,
        )

    def make_reference(self, index=0, role=RefactorEvidenceRole.TARGET):
        return RefactorEvidenceReference(
            evidence_index=index,
            role=role,
            reason="Defines behavior relevant to the refactor.",
        )

    def make_invariant(self, **overrides):
        values = {
            "description": "Retries stop when the maximum attempt count is reached.",
            "basis": InvariantBasis.OBSERVED,
            "evidence_indices": [0, 1],
            "confidence": "high",
        }
        values.update(overrides)
        return BehaviorInvariant(**values)

    def make_ready_context(self, **overrides):
        values = {
            "request": "Refactor RetryPolicy.shouldRetry without changing its signature.",
            "interpretation": self.make_interpretation(),
            "target_symbol": "shouldRetry",
            "evidence": self.make_evidence(),
            "evidence_references": [
                self.make_reference(),
                self.make_reference(1, RefactorEvidenceRole.TEST),
                self.make_reference(2, RefactorEvidenceRole.CALLER),
            ],
            "behavior_invariants": [self.make_invariant()],
            "assumptions": ["The maximum attempt count remains three."],
            "status": RefactorContextStatus.READY,
        }
        values.update(overrides)
        return RefactorContext(**values)

    def make_insufficient_context(self, **overrides):
        values = {
            "request": "Refactor the retry code.",
            "interpretation": self.make_interpretation(target=None),
            "evidence": EvidenceSet(items=[], truncated=False),
            "status": RefactorContextStatus.INSUFFICIENT_EVIDENCE,
            "insufficient_evidence_reason": "No concrete target was identified.",
        }
        values.update(overrides)
        return RefactorContext(**values)

    def test_accepts_ready_context_and_allows_planning(self):
        context = self.make_ready_context()

        self.assertIs(context, context.require_ready_for_planning())

    def test_requires_refactor_intent(self):
        with self.assertRaisesRegex(ValidationError, "REFACTOR task intent"):
            self.make_ready_context(
                interpretation=self.make_interpretation(
                    intent=TaskIntent.ANSWER_QUESTION
                )
            )

    def test_ready_context_requires_complete_positive_claims(self):
        cases = (
            ("target_symbol", None, "target symbol"),
            ("evidence_references", [], "evidence references"),
            ("behavior_invariants", [], "behavior invariants"),
        )

        for field, value, message in cases:
            with self.subTest(field=field):
                with self.assertRaisesRegex(ValidationError, message):
                    self.make_ready_context(**{field: value})

    def test_ready_context_rejects_insufficient_reason(self):
        with self.assertRaisesRegex(ValidationError, "cannot have"):
            self.make_ready_context(
                insufficient_evidence_reason="More evidence is needed."
            )

    def test_insufficient_context_requires_reason(self):
        with self.assertRaisesRegex(ValidationError, "requires a reason"):
            self.make_insufficient_context(insufficient_evidence_reason=None)

    def test_insufficient_context_cannot_claim_target_or_invariants(self):
        cases = (
            {"target_symbol": "shouldRetry"},
            {"behavior_invariants": [self.make_invariant()]},
        )

        for overrides in cases:
            with self.subTest(overrides=overrides):
                with self.assertRaises(ValidationError):
                    self.make_insufficient_context(**overrides)

    def test_rejects_duplicate_and_out_of_range_references(self):
        cases = (
            (
                [self.make_reference(), self.make_reference()],
                "index-role pairs must be unique",
            ),
            ([self.make_reference(3)], "outside the evidence set"),
        )

        for references, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(ValidationError, message):
                    self.make_ready_context(evidence_references=references)

    def test_rejects_unattributed_evidence_items(self):
        with self.assertRaisesRegex(ValidationError, "missing indices: 2"):
            self.make_ready_context(
                evidence_references=[
                    self.make_reference(),
                    self.make_reference(1, RefactorEvidenceRole.TEST),
                ]
            )

    def test_rejects_duplicate_evidence_items(self):
        evidence = self.make_evidence()
        evidence.items.append(evidence.items[0].model_copy())

        with self.assertRaisesRegex(ValidationError, "items must be unique"):
            self.make_ready_context(evidence=evidence)

    def test_accepts_nested_repository_relative_evidence_path(self):
        context = self.make_ready_context()

        self.assertEqual(
            "src/main/java/com/example/RetryPolicy.java",
            context.evidence.items[0].path,
        )

    def test_rejects_blank_absolute_and_parent_traversal_evidence_paths(self):
        for path in ("   ", "/tmp/RetryPolicy.java", "../RetryPolicy.java"):
            with self.subTest(path=path):
                evidence = self.make_evidence()
                evidence.items[0] = evidence.items[0].model_copy(
                    update={"path": path}
                )

                with self.assertRaisesRegex(
                    ValidationError,
                    "repository-relative path",
                ):
                    self.make_ready_context(evidence=evidence)

    def test_rejects_blank_evidence_content(self):
        evidence = self.make_evidence()
        evidence.items[0] = evidence.items[0].model_copy(
            update={"content": " \n\t "}
        )

        with self.assertRaisesRegex(ValidationError, "content must not be blank"):
            self.make_ready_context(evidence=evidence)

    def test_rejects_non_positive_evidence_start_line(self):
        for start_line in (0, -1):
            with self.subTest(start_line=start_line):
                evidence = self.make_evidence()
                evidence.items[0] = evidence.items[0].model_copy(
                    update={"start_line": start_line}
                )

                with self.assertRaisesRegex(
                    ValidationError,
                    "start line must be greater than zero",
                ):
                    self.make_ready_context(evidence=evidence)

    def test_rejects_too_many_evidence_items(self):
        item = self.make_evidence().items[0]
        evidence = EvidenceSet(
            items=[
                item.model_copy(update={"path": f"src/main/java/Example{index}.java"})
                for index in range(MAX_REFACTOR_EVIDENCE_ITEMS + 1)
            ],
            truncated=False,
        )

        with self.assertRaisesRegex(ValidationError, "maximum item count"):
            self.make_ready_context(evidence=evidence)

    def test_rejects_oversized_evidence_content(self):
        evidence = self.make_evidence()
        evidence.items[0] = evidence.items[0].model_copy(
            update={"content": "x" * MAX_REFACTOR_EVIDENCE_CHARS}
        )

        with self.assertRaisesRegex(ValidationError, "maximum context size"):
            self.make_ready_context(evidence=evidence)

    def test_allows_one_evidence_item_to_have_different_roles(self):
        context = self.make_ready_context(
            evidence_references=[
                self.make_reference(),
                self.make_reference(0, RefactorEvidenceRole.TEST),
                self.make_reference(1, RefactorEvidenceRole.TEST),
                self.make_reference(2, RefactorEvidenceRole.CALLER),
            ],
            behavior_invariants=[
                self.make_invariant(evidence_indices=[0])
            ],
        )

        roles_for_first_item = {
            reference.role
            for reference in context.evidence_references
            if reference.evidence_index == 0
        }
        self.assertEqual(
            {RefactorEvidenceRole.TARGET, RefactorEvidenceRole.TEST},
            roles_for_first_item,
        )

    def test_ready_context_requires_target_evidence_containing_symbol(self):
        with self.assertRaisesRegex(ValidationError, "target evidence"):
            self.make_ready_context(
                evidence_references=[
                    self.make_reference(0, RefactorEvidenceRole.CALLER),
                    self.make_reference(1, RefactorEvidenceRole.TEST),
                    self.make_reference(2, RefactorEvidenceRole.CALLER),
                ]
            )

        with self.assertRaisesRegex(ValidationError, "does not appear"):
            self.make_ready_context(target_symbol="missingSymbol")

    def test_rejects_duplicate_invariant_evidence_indices(self):
        with self.assertRaisesRegex(ValidationError, "must be unique"):
            self.make_invariant(evidence_indices=[0, 0])

    def test_observed_invariant_requires_target_or_test_evidence(self):
        with self.assertRaisesRegex(ValidationError, "target or test evidence"):
            self.make_ready_context(
                evidence_references=[
                    self.make_reference(0),
                    self.make_reference(1, RefactorEvidenceRole.DEPENDENCY),
                    self.make_reference(2, RefactorEvidenceRole.CALLER),
                ],
                behavior_invariants=[
                    self.make_invariant(evidence_indices=[2])
                ],
            )

    def test_allows_inferred_invariant_from_caller_evidence(self):
        context = self.make_ready_context(
            evidence_references=[
                self.make_reference(0),
                self.make_reference(1, RefactorEvidenceRole.DEPENDENCY),
                self.make_reference(2, RefactorEvidenceRole.CALLER),
            ],
            behavior_invariants=[
                self.make_invariant(
                    basis=InvariantBasis.INFERRED,
                    evidence_indices=[2],
                    confidence="medium",
                )
            ],
        )

        self.assertEqual(InvariantBasis.INFERRED, context.behavior_invariants[0].basis)

    def test_rejects_duplicate_invariant_descriptions_case_insensitively(self):
        invariant = self.make_invariant()
        duplicate = self.make_invariant(description=invariant.description.upper())

        with self.assertRaisesRegex(ValidationError, "descriptions must be unique"):
            self.make_ready_context(behavior_invariants=[invariant, duplicate])

    def test_insufficient_context_is_not_ready_for_planning(self):
        with self.assertRaisesRegex(ValueError, "not ready for planning"):
            self.make_insufficient_context().require_ready_for_planning()

    def test_ready_console_output_exposes_decision_evidence_and_constraints(self):
        output = self.make_ready_context().to_console_string()

        for expected in (
            "Status: ready",
            "Ready for Planning: Yes",
            "Keep the public signature unchanged.",
            "[0] target src/main/java/com/example/RetryPolicy.java:8",
            "basis: observed",
            "confidence: high",
            "The maximum attempt count remains three.",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, output)

    def test_insufficient_console_output_explains_why_planning_is_blocked(self):
        output = self.make_insufficient_context().to_console_string()

        self.assertIn("Ready for Planning: No", output)
        self.assertIn("No concrete target was identified.", output)
        self.assertIn("Evidence References\nNone", output)
        self.assertIn("Behavior Invariants\nNone", output)


if __name__ == "__main__":
    unittest.main()
