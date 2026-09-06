from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from cd_assist.evidence_validation import (
    require_unique,
    validate_evidence_indices_in_range,
)
from cd_assist.models import (
    EvidenceIndex,
    EvidenceSet,
    TaskIntent,
    TaskInterpretation,
)
from cd_assist.workspace import Workspace

BoundedRefactorRequest = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=2_000),
]
BoundedRefactorText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=1_000),
]
BoundedTargetSymbol = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=200),
]
MAX_REFACTOR_EVIDENCE_ITEMS = 30
MAX_REFACTOR_EVIDENCE_CHARS = 20_000


class RefactorEvidenceRole(str, Enum):
    TARGET = "target"
    CALLER = "caller"
    DEPENDENCY = "dependency"
    TEST = "test"


class RefactorEvidenceReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_index: EvidenceIndex
    role: RefactorEvidenceRole
    reason: BoundedRefactorText

class InvariantBasis(str, Enum):
    OBSERVED = "observed"
    INFERRED = "inferred"


class BehaviorInvariant(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: BoundedRefactorText
    basis: InvariantBasis
    evidence_indices: list[EvidenceIndex] = Field(min_length=1, max_length=10)
    confidence: Literal["low", "medium", "high"]

    @field_validator("evidence_indices")
    @classmethod
    def reject_duplicate_evidence_indices(cls, indices: list[int]) -> list[int]:
        return require_unique(indices, "Evidence indices")


class RefactorContextStatus(str, Enum):
    READY = "ready"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class RefactorContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request: BoundedRefactorRequest
    interpretation: TaskInterpretation
    target_symbol: BoundedTargetSymbol | None = None
    evidence: EvidenceSet
    evidence_references: list[RefactorEvidenceReference] = Field(default_factory=list, max_length=30)
    behavior_invariants: list[BehaviorInvariant] = Field(default_factory=list, max_length=20)
    assumptions: list[BoundedRefactorText] = Field(default_factory=list, max_length=10)
    insufficient_evidence_reason: BoundedRefactorText | None = None
    status: RefactorContextStatus

    @model_validator(mode="after")
    def validate_refactor_context(self):
        self._validate_outcome_state()
        self._validate_evidence_set()
        self._validate_evidence_references()
        self._validate_target_support()
        self._validate_behavior_invariants()
        return self

    def _validate_outcome_state(self) -> None:
        if self.interpretation.intent != TaskIntent.REFACTOR:
            raise ValueError("Refactor context requires REFACTOR task intent.")

        if self.status == RefactorContextStatus.READY:
            if self.target_symbol is None:
                raise ValueError("Ready refactor context requires a target symbol.")
            if not self.evidence_references:
                raise ValueError("Ready refactor context requires evidence references.")
            if not self.behavior_invariants:
                raise ValueError("Ready refactor context requires behavior invariants.")
            if self.insufficient_evidence_reason is not None:
                raise ValueError("Ready refactor context cannot have an insufficient-evidence reason.")
        else:
            if self.insufficient_evidence_reason is None:
                raise ValueError("Insufficient refactor context requires a reason.")
            if self.target_symbol is not None:
                raise ValueError("Insufficient refactor context cannot claim a target symbol.")
            if self.behavior_invariants:
                raise ValueError("An insufficient context cannot claim behavior invariants.")

    def _validate_evidence_references(self) -> None:
        keys = [
            (reference.evidence_index, reference.role)
            for reference in self.evidence_references
        ]
        require_unique(keys, "Evidence reference index-role pairs")
        validate_evidence_indices_in_range(
            [reference.evidence_index for reference in self.evidence_references],
            len(self.evidence.items),
        )
        referenced_indices = {
            reference.evidence_index for reference in self.evidence_references
        }
        unattributed_indices = set(range(len(self.evidence.items))) - referenced_indices
        if unattributed_indices:
            formatted_indices = ", ".join(
                str(index) for index in sorted(unattributed_indices)
            )
            raise ValueError(
                f"Evidence items must be attributed; missing indices: {formatted_indices}."
            )

    def _validate_target_support(self) -> None:
        if self.status != RefactorContextStatus.READY:
            return

        target_indices = [
            reference.evidence_index
            for reference in self.evidence_references
            if reference.role == RefactorEvidenceRole.TARGET
        ]
        if not target_indices:
            raise ValueError("Ready refactor context requires target evidence.")

        if not any(
            self.target_symbol in self.evidence.items[index].content
            for index in target_indices
        ):
            raise ValueError("Target symbol does not appear in target evidence.")

    def _validate_behavior_invariants(self) -> None:
        attributed_indices = {
            reference.evidence_index for reference in self.evidence_references
        }
        observed_indices = {
            reference.evidence_index
            for reference in self.evidence_references
            if reference.role in {
                RefactorEvidenceRole.TARGET,
                RefactorEvidenceRole.TEST,
            }
        }
        normalized_descriptions = [
            invariant.description.casefold() for invariant in self.behavior_invariants
        ]
        require_unique(normalized_descriptions, "Behavior invariant descriptions")

        for invariant in self.behavior_invariants:
            validate_evidence_indices_in_range(
                invariant.evidence_indices,
                len(self.evidence.items),
            )
            if not set(invariant.evidence_indices).issubset(attributed_indices):
                raise ValueError("Behavior invariant uses unattributed evidence.")
            if (
                invariant.basis == InvariantBasis.OBSERVED
                and not set(invariant.evidence_indices).intersection(observed_indices)
            ):
                raise ValueError("Observed invariant requires target or test evidence.")

    def _validate_evidence_set(self) -> None:
        if len(self.evidence.items) > MAX_REFACTOR_EVIDENCE_ITEMS:
            raise ValueError(
                "Refactor evidence exceeds the maximum item count of "
                f"{MAX_REFACTOR_EVIDENCE_ITEMS}."
            )

        for index, item in enumerate(self.evidence.items):
            try:
                Workspace.normalize_relative_path(item.path)
            except (TypeError, ValueError) as error:
                raise ValueError(
                    f"Evidence item {index} must have a repository-relative path: "
                    f"{error}"
                ) from error

            if not item.content.strip():
                raise ValueError(f"Evidence item {index} content must not be blank.")

            if item.start_line is not None and item.start_line <= 0:
                raise ValueError(
                    f"Evidence item {index} start line must be greater than zero."
                )

        evidence_keys = [item.evidence_key() for item in self.evidence.items]
        require_unique(evidence_keys, "Refactor evidence items")

        bounded_evidence = EvidenceSet(
            items=self.evidence.items,
            truncated=False,
        ).parse(max_chars=MAX_REFACTOR_EVIDENCE_CHARS)
        if bounded_evidence.truncated:
            raise ValueError(
                "Refactor evidence exceeds the maximum context size of "
                f"{MAX_REFACTOR_EVIDENCE_CHARS} characters."
            )

    def to_console_string(self) -> str:
        constraints = self._format_list(self.interpretation.constraints)
        assumptions = self._format_list(self.assumptions)
        evidence_references = self._format_evidence_references()
        invariants = self._format_invariants()
        target = self.interpretation.target or "Not specified"
        target_symbol = self.target_symbol or "Not established"
        reason = self.insufficient_evidence_reason or "None"

        return (
            "Refactor Context\n"
            f"Status: {self.status.value}\n"
            f"Request: {self.request}\n"
            f"Intent: {self.interpretation.intent.value}\n"
            f"Requested Target: {target}\n"
            f"Target Symbol: {target_symbol}\n"
            f"Ready for Planning: {'Yes' if self.status == RefactorContextStatus.READY else 'No'}\n"
            f"Insufficient Evidence Reason: {reason}\n\n"
            "User Constraints\n"
            f"{constraints}\n\n"
            "Evidence References\n"
            f"{evidence_references}\n\n"
            "Behavior Invariants\n"
            f"{invariants}\n\n"
            "Assumptions\n"
            f"{assumptions}"
        )

    @staticmethod
    def _format_list(items: list[str]) -> str:
        return "\n".join(f"- {item}" for item in items) if items else "None"

    def _format_evidence_references(self) -> str:
        if not self.evidence_references:
            return "None"
        lines = []
        for reference in self.evidence_references:
            item = self.evidence.items[reference.evidence_index]
            location = (
                f"{item.path}:{item.start_line}"
                if item.start_line is not None
                else item.path
            )
            lines.append(
                f"- [{reference.evidence_index}] {reference.role.value} "
                f"{location} — {reference.reason}"
            )
        return "\n".join(lines)

    def _format_invariants(self) -> str:
        if not self.behavior_invariants:
            return "None"
        return "\n".join(
            f"- {invariant.description} "
            f"(basis: {invariant.basis.value}; confidence: {invariant.confidence}; "
            f"evidence: {', '.join(str(index) for index in invariant.evidence_indices)})"
            for invariant in self.behavior_invariants
        )

    def require_ready_for_planning(self) -> RefactorContext:
        if self.status != RefactorContextStatus.READY:
            raise ValueError("Refactor context is not ready for planning.")
        return self
