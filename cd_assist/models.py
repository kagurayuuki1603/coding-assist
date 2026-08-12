from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Annotated, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from cd_assist.errors import ModelResponseError
from cd_assist.tools import MatchType

READ_FILE: Final = "read_file"
SEARCH_FILES: Final = "search_files"
ToolName = Literal[READ_FILE, SEARCH_FILES]

####### TASK RELATED MODELS #######
class TaskIntent(str, Enum):
    ANSWER_QUESTION = "answer_question"
    FIND_BUGS = "find_bugs"
    GENERATE_TESTS = "generate_tests"

SearchTerm = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=100),
]
Target = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=500),
]

class TaskInterpretation(BaseModel):
    intent: TaskIntent
    target: Target | None = None
    search_terms: list[SearchTerm] = Field(min_length=1, max_length=5)





####### RETRIEVAL RELATED MODELS #######
class RetrievalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: ToolName
    path: str | None
    query: str | None

    @model_validator(mode="after")
    def validate_tool_arguments(self):
        if self.tool == READ_FILE:
            if self.path is None:
                raise ValueError("read_file requires path")
            if self.query is not None:
                raise ValueError("read_file does not accept query")

        if self.tool == SEARCH_FILES:
            if self.query is None:
                raise ValueError("search_files requires query")
            if self.path is not None:
                raise ValueError("search_files does not accept path")

        return self
    
    def to_context_string(self) -> str:
        if self.tool == READ_FILE:
            return (
                f"Tool: {READ_FILE}\n"
                f"Path: {self.path}"
            )

        return (
            f"Tool: {SEARCH_FILES}\n"
            f"Query: {self.query}"
        )

class RetrievalAction(str, Enum):
    RETRIEVE = "retrieve"
    STOP = "stop"

Reason = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=300,
    ),
]

class RetrievalDecision(BaseModel):
    action: RetrievalAction
    tool: ToolName | None
    path: str | None
    query: str | None
    stop_reason: Literal[
        "sufficient_evidence",
        "no_results"
    ] | None
    reason: Reason

    @model_validator(mode="after")
    def validate_decision_arguments(self):
        if self.action == RetrievalAction.STOP:
            if self.query is not None or self.tool is not None or self.path is not None:
                raise ValueError("stop action does not accept query, tool and path")
            if self.stop_reason != "sufficient_evidence" and self.stop_reason != "no_results":
                raise ValueError("stop reason invalid.")
        if self.action == RetrievalAction.RETRIEVE:
            if self.tool is None:
                raise ValueError("retrieve action requires a tool")
            if self.stop_reason is not None:
                raise ValueError("retrieve action should not have stop_reason")
            if self.tool == READ_FILE:
                if self.path is None:
                    raise ValueError("read_file requires path")
                if self.query is not None:
                    raise ValueError("read_file does not accept query")
            if self.tool == SEARCH_FILES:
                if self.query is None:
                    raise ValueError("search_files requires query")
                if self.path is not None:
                    raise ValueError("search_files does not accept path")
        
        return self
        

####### EVIDENCE RELATED MODELS #######
class EvidenceItem(BaseModel):
    path: str
    start_line: int | None
    content: str
    source: ToolName
    truncated: bool

    def evidence_key(self) -> tuple[str, int | None, str]:
        return (
            self.path.lower(),
            self.start_line,
            self.content.strip(),
        )

    def to_context_string(self):
        location = (
            f"Line: {self.start_line}"
            if self.start_line is not None
            else "Line: Unknown"
        )

        return (
            f"File: {self.path}\n"
            f"{location}\n"
            f"Source: {self.source}\n"
            f"Truncated: {self.truncated}\n"
            "Content:\n"
            f"{self.content.rstrip()}"
        )


class EvidenceSet(BaseModel):
    items: list[EvidenceItem] = Field(default_factory=list)
    truncated: bool
    context_str: str = ""

    def deduplicated(self) -> EvidenceSet:
        seen = set()
        unique_items = []

        for item in self.items:
            key = item.evidence_key()

            if key in seen:
                continue

            seen.add(key)
            unique_items.append(item)

        return EvidenceSet(
            items=unique_items, 
            truncated=self.truncated
        )

    def parse(self, max_chars = 20_000) -> EvidenceSet:

        if not isinstance(max_chars, int) or max_chars <= 0:
            raise ValueError("max_chars must be a positive integer")

        deduplicated_set = self.deduplicated()

        bounded_items = []
        context_str = ""
        marker = "\nEvidence context truncated: True"
        truncated = False

        for index, item in enumerate(deduplicated_set.items):
            separator = "\n\n" if context_str else ""
            evidence_index_indicator = f"Evidence {index}: \n"
            item_str = separator + evidence_index_indicator + item.to_context_string()
            if len(context_str) + len(item_str) > max_chars:
                prefix_budget = max(0, max_chars - len(marker))
                combined = context_str + item_str
                context_str = combined[:prefix_budget] + marker
                context_str = context_str[:max_chars]
                truncated = True
                bounded_items.append(item)
                break
            bounded_items.append(item)
            context_str += item_str
        
        return EvidenceSet(
            items=bounded_items,
            truncated=truncated if truncated else self.truncated,
            context_str=context_str[:max_chars]
        )

    def to_console_string(self):
        return "No Evidence Found." if (self.context_str is None or len(self.context_str) == 0) else self.context_str

@dataclass
class RetrievalObservation:
    request: RetrievalRequest
    result: object

    def get_evidence_items(self) -> list[EvidenceItem]:
        items = []
        if self.request.tool == READ_FILE:
            if not isinstance(self.result, dict):
                raise TypeError("read_file result must be a dictionary")

            content = self.result.get("content")
            truncated = self.result.get("truncated")

            if not isinstance(content, str):
                raise TypeError("read_file result content must be a string")

            if not isinstance(truncated, bool):
                raise TypeError("read_file result truncated must be a boolean")

            items.append(
                EvidenceItem(
                    path=self.request.path,
                    start_line=1,
                    content=self.result['content'],
                    source=READ_FILE,
                    truncated=self.result['truncated']
                )
            )
        else:
            query = self.request.query
            search_results = self.result

            if not isinstance(search_results, list):
                raise TypeError("search_files result must be a list")

            for search_result in search_results:
                items.append(
                    EvidenceItem(
                        path=search_result.path,
                        start_line=search_result.line_snippet.line if search_result.match_type == MatchType.CONTENT else None,
                        content=search_result.line_snippet.snippet if search_result.match_type == MatchType.CONTENT else "", 
                        source=SEARCH_FILES,
                        truncated=False
                    )
                )

        return items

class StopReason(str, Enum):
    SUFFICIENT_EVIDENCE = "sufficient_evidence"
    MAX_ROUNDS = "max_rounds"
    REPEATED_REQUEST = "repeated_request"
    NO_RESULTS = "no_results"
    TOOL_ERROR = "tool_error"
    AGENT_ERROR = "agent_error"


@dataclass
class RetrievalState:
    requests: list[RetrievalRequest] = field(default_factory=list)
    observations: list[RetrievalObservation] = field(default_factory=list)
    stop_reason: StopReason | None = None

    def update(self, request: RetrievalRequest, observation: RetrievalObservation):
        self.requests.append(request)
        self.observations.append(observation)

    @staticmethod
    def retrieval_request_key(request):
        return (
            request.tool,
            request.path.strip() if request.path else None,
            request.query.strip().lower() if request.query else None,
        )
    
    def was_attempted(self, request: RetrievalRequest):
        key = self.retrieval_request_key(request)

        return any(
            self.retrieval_request_key(previous) == key
            for previous in self.requests
        )

    def to_cli_string(self) -> str:
        return (
            f"Stop reason: {self.stop_reason}\n"
            f"Retrievals completed: {len(self.observations)}"
        )

    def build_evidence_set(self, max_chars=20_000) -> EvidenceSet:
        evidence_list = []

        for observation in self.observations:
            evidence_list.extend(observation.get_evidence_items())
        try:
            return EvidenceSet(
                items=evidence_list, 
                truncated=any(evidence.truncated for evidence in evidence_list)
            ).parse(max_chars)
        except ValueError as error:
            raise ModelResponseError(f"Could not generate EvidenceSet: {error}")


####### BUG RELATED MODELS #######
LOW = "low"
MEDIUM = "medium"
HIGH = "high"
ConfidenceLevel = Literal[LOW, MEDIUM, HIGH]

BugText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=2_000),
]
EvidenceIndex = Annotated[int, Field(ge=0)]
StartLine = Annotated[int, Field(ge=1)]

class BugFinding(BaseModel):
    path: Target
    start_line: StartLine | None
    reasoning: BugText
    impact: BugText
    confidence: ConfidenceLevel
    evidence_indices: list[EvidenceIndex] = Field(min_length=1, max_length=10)

class BugAnalysis(BaseModel):
    findings: list[BugFinding] = Field(max_length=10)
    insufficient_evidence_reason: BugText | None

    @model_validator(mode="after")
    def validate_analysis_outcome(self):
        if self.findings and self.insufficient_evidence_reason is not None:
            raise ValueError(
                "A bug analysis with findings cannot be marked insufficient"
            )

        if not self.findings and self.insufficient_evidence_reason is None:
            raise ValueError(
                "A bug analysis without findings requires an insufficient evidence reason"
            )

        return self

    def validate_evidence_references(self, evidence_set: EvidenceSet):
        for finding in self.findings:
            for index in finding.evidence_indices:
                if index < 0 or index >= len(evidence_set.items):
                    raise ValueError(f"Invalid evidence index: {index}")
                
                evidence = evidence_set.items[index]
                if finding.path != evidence.path:
                    raise ValueError("Finding path does not match referenced evidence")

        return self

    def to_console_string(self):
        if self.insufficient_evidence_reason is not None:
            return f"Insufficient Evidence: {self.insufficient_evidence_reason}"

        findings_str = "\n".join(
            finding.model_dump_json() for finding in self.findings
        )
        return (
            f"Findings: {findings_str}\n"
            "Sufficient evidence found."
        )
