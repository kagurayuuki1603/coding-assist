from pathlib import Path
from typing import Callable

from openai import OpenAI, OpenAIError

import cd_assist.client as client_config
from cd_assist.errors import ModelResponseError, AgentResponseError
from cd_assist.models import (
    BugAnalysis,
    RetrievalDecision,
    RetrievalRequest,
    RetrievalState,
    StopReason,
    TaskIntent,
    TaskInterpretation,
    EvidenceSet,
)
from cd_assist.prompts import (
    ASK_INSTRUCTIONS,
    BUG_FINDING_INSTRUCTIONS,
    EXPLANATION_INSTRUCTIONS,
    INTERPRETATION_INSTRUCTIONS,
    NEXT_RETRIEVAL_INSTRUCTIONS,
    RETRIEVAL_SELECTION_INSTRUCTIONS,
    TEST_PROPOSAL_INSTRUCTIONS,
    TEST_PATCH_INSTRUCTIONS
)
from cd_assist.test_generation import TestGenerationContext, TestProposal, discover_test_framework, ProposedPatch
from cd_assist.tools import read_file
from cd_assist.retrieval import run_retrieval_loop


def init_agent(workspace, client):
    return CodingAssistantAgent(
        client=client,
        workspace=workspace,
        generate_response=generate_response,
        interpret_intention=interpret_intention,
        select_tool=select_tool,
        decide_next_retrieval=decide_next_retrieval,
        analyze_bugs=analyze_bugs,
        propose_tests=propose_tests,
        get_test_patch=get_test_patch
    )

def interpret_intention(client: OpenAI, user_request: str) -> TaskInterpretation:
    try:
        response = client.responses.parse(
            model=client_config.MODEL_NAME,
            input=[
                {
                    "role": "system",
                    "content": INTERPRETATION_INSTRUCTIONS,
                },
                {
                    "role": "user",
                    "content": user_request,
                },
            ],
            text_format=TaskInterpretation,
        )
    except OpenAIError as error:
        raise ModelResponseError("Could not generate a model response") from error

    interpretation = response.output_parsed

    if interpretation is None:
        raise ModelResponseError(
            "The model did not return a valid task interpretation"
        )
    return interpretation

def select_tool(client: OpenAI, interpretation: TaskInterpretation) -> RetrievalRequest:
    try:
        response = client.responses.parse(
            model=client_config.MODEL_NAME,
            input=[
                {
                    "role": "system",
                    "content": RETRIEVAL_SELECTION_INSTRUCTIONS,
                },
                {
                    "role": "user",
                    "content": interpretation.model_dump_json(),
                },
            ],
            text_format=RetrievalRequest,
        )
    except OpenAIError as error:
        raise ModelResponseError(
            f"Could not select a retrieval tool: {error}"
        ) from error

    request = response.output_parsed

    if request is None:
        raise ModelResponseError(
            "The model did not return a valid retrieval request"
        )
    return request

def decide_next_retrieval(client: OpenAI, interpretation: TaskInterpretation, retrieval_context: str) -> RetrievalDecision:
    try:
        response = client.responses.parse(
            model=client_config.MODEL_NAME,
            input=[
                {
                    "role": "system",
                    "content": NEXT_RETRIEVAL_INSTRUCTIONS,
                },
                {
                    "role": "user",
                    "content": f"""
Task interpretation:
{interpretation.model_dump_json()}

Retrieved repository context:
<retrieval_context>
{retrieval_context}
</retrieval_context>
"""
                },
            ],
            text_format=RetrievalDecision,
        )
    except OpenAIError as error:
        raise ModelResponseError(
            f"Could not decide next retrieval: {error}"
        ) from error

    request = response.output_parsed

    if request is None:
        raise ModelResponseError(
            "The model did not return a valid retrieval request"
        )
    return request

def analyze_bugs(client: OpenAI, evidence_set: EvidenceSet, query: str):
    try:
        response = client.responses.parse(
            model=client_config.MODEL_NAME,
            input=[
                {
                    "role": "system",
                    "content": BUG_FINDING_INSTRUCTIONS,
                },
                {
                    "role": "user",
                    "content": f"""
Query: {query}
                    
Evidence Set:
{evidence_set.context_str}
"""
                },
            ],
            text_format=BugAnalysis,
        )
    except OpenAIError as error:
        raise ModelResponseError(
            f"Could not analyze bugs: {error}"
        ) from error

    request = response.output_parsed

    if request is None:
        raise ModelResponseError(
            "The model did not return a valid bug analysis"
        )
    return request

def propose_tests(client: OpenAI, context: TestGenerationContext) -> TestProposal:
    try:
        response = client.responses.parse(
            model=client_config.MODEL_NAME,
            input=[
                {
                    "role": "system",
                    "content": TEST_PROPOSAL_INSTRUCTIONS,
                },
                {
                    "role": "user",
                    "content": f"Context: {context.to_console_string()}"
                },
            ],
            text_format=TestProposal,
        )
    except OpenAIError as error:
        raise ModelResponseError(
            f"Could not propose tests: {error}"
        ) from error

    proposal: TestProposal = response.output_parsed

    if proposal is None:
        raise ModelResponseError(
            "The model did not return a valid test proposal"
        )

    try:
        proposal.validate_result(context)
    except ValueError as error:
        raise AgentResponseError(f"The model did not return a valid test proposal: {error}") from error

    return proposal

def get_test_patch(client: OpenAI, proposal: TestProposal, context: TestGenerationContext, workspace: Path | str) -> ProposedPatch:
    try:
        response = client.responses.parse(
            model=client_config.MODEL_NAME,
            input=[
                {
                    "role": "system",
                    "content": TEST_PATCH_INSTRUCTIONS,
                },
                {
                    "role": "user",
                    "content": f"Context: {context.to_console_string()}"
                },
                {
                    "role": "user",
                    "content": f"Proposal: {proposal.to_console_string()}"
                },
            ],
            text_format=ProposedPatch,
        )
    except OpenAIError as error:
        raise ModelResponseError(
            f"Could not create test patch: {error}"
        ) from error

    patch = response.output_parsed

    if patch is None:
        raise ModelResponseError(
            "The model did not return a valid test patch"
        )

    try:
        patch.validate_result(proposal, context.discovery, workspace)
    except ValueError as error:
        raise AgentResponseError(f"The model did not return a valid test patch: {error}") from error

    return patch

def generate_response(client: OpenAI, prompt: str) -> str:
    streamed_text = ""

    try:
        stream = client.responses.create(
            model=client_config.MODEL_NAME,
            input=prompt,
            stream=True,
        )

        for event in stream:
            if event.type == "response.output_text.delta":
                streamed_text += event.delta
            elif event.type in {"response.failed", "response.incomplete", "error"}:
                raise ModelResponseError("The model response did not complete")
    except OpenAIError as error:
        raise ModelResponseError("Could not generate a model response") from error

    return streamed_text


class CodingAssistantAgent:
    def __init__(
        self,
        client: OpenAI,
        workspace: Path | str,
        generate_response: Callable[[OpenAI, str], str],
        interpret_intention: Callable[[OpenAI, str], TaskInterpretation],
        select_tool: Callable[[OpenAI, TaskInterpretation], RetrievalRequest],
        decide_next_retrieval: Callable[[OpenAI, TaskInterpretation, str], RetrievalDecision],
        analyze_bugs: Callable[[OpenAI, EvidenceSet, str], BugAnalysis],
        propose_tests: Callable[[OpenAI, TestGenerationContext], TestProposal],
        get_test_patch: Callable[[OpenAI, TestProposal, TestGenerationContext, Path | str], ProposedPatch],
    ):
        self.workspace = workspace
        self.client = client
        self.generate_response = generate_response
        self.interpret_intention = interpret_intention
        self.select_tool = select_tool
        self.decide_next_retrieval = decide_next_retrieval
        self.analyze_bugs = analyze_bugs
        self.propose_tests = propose_tests
        self.get_test_patch = get_test_patch

    def explain_file(self, requested_path: str) -> str:
        file_result = read_file(self.workspace, requested_path)

        prompt = f"""
{EXPLANATION_INSTRUCTIONS}

File: {requested_path}
Truncated: {file_result["truncated"]}

<java_source>
{file_result["content"]}
</java_source>
"""

        return self.generate_response(self.client, prompt)

    def ask_question(self, query: str, context: str) -> str:
        prompt = f"""
{ASK_INSTRUCTIONS}

Query: {query}

<context>
{context}
</context>
"""
        return self.generate_response(self.client, prompt)

    def interpret_task(self, query: str) -> TaskInterpretation: 
        return self.interpret_intention(self.client, query)

    def retrieve_tool(self, interpretation: TaskInterpretation) -> RetrievalRequest:
        return self.select_tool(self.client, interpretation)

    def determine_next_retrieval(self, interpretation: TaskInterpretation, retrieval_context: str) -> RetrievalDecision:
        return self.decide_next_retrieval(self.client, interpretation, retrieval_context)

    def gather_retrievals(self, query: str) -> RetrievalState:
        interpretation = self.interpret_task(query)

        return self.gather_retrievals_for_interpretation(interpretation)

    def gather_retrievals_for_interpretation(self, interpretation: TaskInterpretation) -> RetrievalState:
        initial_request = self.retrieve_tool(interpretation)

        return run_retrieval_loop(
            workspace=self.workspace,
            interpretation=interpretation,
            initial_request=initial_request,
            decide_next=self.determine_next_retrieval,
        )

    def gather_evidence(self, query: str) -> EvidenceSet:
        state = self.gather_retrievals(query)

        return state.build_evidence_set()

    def find_bugs(self, query: str) -> BugAnalysis:
        evidence_set: EvidenceSet = self.gather_evidence(query)

        analysis: BugAnalysis = self.analyze_bugs(self.client, evidence_set, query)
        try:
            analysis.validate_evidence_references(evidence_set) 
        except ValueError as error:
            raise ModelResponseError(f"The model did not return a valid bug analysis: {error}") from error

        return analysis

    def gather_test_generation_context(self, query: str) -> TestGenerationContext:
        interpretation = self.interpret_task(query)

        if interpretation.intent != TaskIntent.GENERATE_TESTS:
            raise ValueError("Expected a test-generation task")

        state: RetrievalState = self.gather_retrievals_for_interpretation(interpretation)

        if state.stop_reason == StopReason.TOOL_ERROR:
            raise ModelResponseError(f"Something went wrong with the tool...{state.to_cli_string()}")

        evidence = state.build_evidence_set()
        discovery = discover_test_framework(self.workspace)

        return TestGenerationContext(
            request=query,
            interpretation=interpretation,
            discovery=discovery,
            evidence=evidence,
        )

    def generate_test_proposal(self, query: str) -> TestProposal:
        context: TestGenerationContext = self.gather_test_generation_context(query)

        proposal: TestProposal = self.propose_tests(self.client, context)

        return proposal

    def generate_test_patch(self, query: str) -> ProposedPatch:
        context: TestGenerationContext = self.gather_test_generation_context(query)

        proposal: TestProposal = self.propose_tests(self.client, context)

        if proposal.insufficient_evidence_reason is not None:
            raise AgentResponseError("Insufficient evidence for test patch generation.")

        test_patch: ProposedPatch = self.get_test_patch(self.client, proposal, context, self.workspace)

        return test_patch
