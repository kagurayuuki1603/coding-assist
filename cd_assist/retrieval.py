from pathlib import Path, PurePosixPath
from typing import Callable

from cd_assist.errors import FileParseError, ModelResponseError
from cd_assist.models import (
    READ_FILE,
    SEARCH_FILES,
    RetrievalAction,
    RetrievalDecision,
    RetrievalObservation,
    RetrievalRequest,
    RetrievalState,
    StopReason,
    TaskInterpretation,
)
from cd_assist.tools import read_file, search_files

MAX_RETRIEVAL_ROUNDS = 3

def execute_retrieval(workspace, request: RetrievalRequest):
    if request.tool == READ_FILE:
        return read_file(workspace, request.path)

    if request.tool == SEARCH_FILES:
        return search_files(workspace, request.query)

    raise ValueError("Unsupported retrieval tool")


def build_retrieval_context(
    state: RetrievalState,
    remaining_rounds: int,
    max_chars: int = 20_000,
) -> str:

    context_str = f"""
Remaining retrieval rounds: {remaining_rounds}\n
    """
    
    observations = state.observations

    if not state.observations:
        context_str += "No retrieval observations.\n"
        return context_str

    truncation_marker = "Retrieval context truncated: True"

    for index, observation in enumerate(observations, start=1):
        obv_request = observation.request
        obv_result = observation.result

        truncated_text = ""
        result_text = ""

        if obv_request.tool == READ_FILE and isinstance(obv_result, dict):
            truncated_text = f'Truncated: {obv_result.get("truncated", False)}'
            result_text = obv_result.get("content", "No content found.")

        else :
            for search_result in obv_result: 
                result_text += search_result.to_context_str() + "\n"

        obv_str = f"""
Observation {index}
{obv_request.to_context_string()}
{truncated_text}

Result: 
{result_text}

        """

        if len(context_str) + len(obv_str) > max_chars:
            available_chars = max(
                0,
                max_chars - len(context_str) - len(truncation_marker),
            )
            context_str += obv_str[:available_chars]
            context_str += truncation_marker
            return context_str[:max_chars]

        context_str += obv_str

    return context_str


def resolve_retrieval_request(workspace, request: RetrievalRequest)-> RetrievalRequest:
    if request.tool != READ_FILE:
        return request

    workspace_path = Path(workspace)
    requested_path = PurePosixPath(request.path.replace("\\", "/"))

    if requested_path.is_absolute() or ".." in requested_path.parts:
        return request

    if (workspace_path / requested_path).is_file():
        return request

    return RetrievalRequest(
        tool=SEARCH_FILES,
        path=None,
        query=request.path,
    )


def run_retrieval_loop(
    workspace, 
    interpretation: TaskInterpretation, 
    initial_request: RetrievalRequest, 
    decide_next: Callable[[TaskInterpretation, str], RetrievalDecision], 
    max_rounds: int = MAX_RETRIEVAL_ROUNDS
) -> RetrievalState:

    state = RetrievalState()
    current_rounds = 0
    current_request = initial_request

    while current_rounds <= max_rounds:
        if current_rounds == max_rounds:
            state.stop_reason = StopReason.MAX_ROUNDS
            return state

        current_request = resolve_retrieval_request(workspace, current_request)

        if state.was_attempted(current_request):
            state.stop_reason = StopReason.REPEATED_REQUEST
            return state
        
        try:
            tool_result = execute_retrieval(workspace, current_request)
        except (ValueError, FileParseError) as error:
            state.stop_reason = StopReason.TOOL_ERROR
            return state

        current_rounds += 1

        observation = RetrievalObservation(
            request=current_request,
            result=tool_result
        )

        state.update(current_request, observation)

        retrieval_context = build_retrieval_context(state, max_rounds - current_rounds)

        try:
            decision = decide_next(interpretation, retrieval_context)
        except ModelResponseError as error:
            state.stop_reason = StopReason.AGENT_ERROR
            return state

        if decision.action == RetrievalAction.STOP:
            if decision.stop_reason != StopReason.SUFFICIENT_EVIDENCE and decision.stop_reason != StopReason.NO_RESULTS:
                state.stop_reason = StopReason.AGENT_ERROR
                return state
            state.stop_reason = StopReason(decision.stop_reason)
            return state
        elif decision.action == RetrievalAction.RETRIEVE:
            current_request = RetrievalRequest(
                tool=decision.tool,
                path=decision.path,
                query=decision.query
            )
        else: 
            # scenario where the decision.action is neither stop or retrieve 
            state.stop_reason = StopReason.AGENT_ERROR
            return state
        
    return state
