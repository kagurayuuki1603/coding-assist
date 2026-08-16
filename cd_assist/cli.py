import argparse

from cd_assist.client import init_openai_client
from cd_assist.agent import CodingAssistantAgent, init_agent
from cd_assist.context import build_working_context
from cd_assist.errors import FileParseError, ModelResponseError, AgentResponseError
from cd_assist.input_util import (
    should_ask,
    should_exit,
    should_explain,
    should_find_bugs,
    should_generate_tests,
    should_interpret,
    should_select_tool,
    should_retrieve_tool,
)
from cd_assist.models import BugAnalysis
from cd_assist.test_generation import FrameworkDiscoveryError, ProposedPatch
from cd_assist.print import (
    print_agent_response,
    print_exception,
    print_goodbye,
    print_idk,
    print_interpretation,
    print_intro,
    print_no_file,
    print_no_query,
)
from cd_assist.tools import search_files

def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    return parser.parse_args()

def validate_and_get_arg(user_input: str, print_method):
    parts = user_input.split(" ", 1)
    if len(parts) < 2:
        print_method()
        return None

    return parts[1]

def handle_explain_command(user_input: str, agent: CodingAssistantAgent):
    # validate arguments
    requested_path = validate_and_get_arg(user_input, print_no_file)

    if requested_path is None:
        return 

    # call agent
    try:
        response = agent.explain_file(requested_path)
    except (FileParseError, ModelResponseError) as error:
        print_exception(error)
        return
    
    print_agent_response(response)

def handle_ask_command(user_input: str, agent: CodingAssistantAgent, workspace: str):

    query = validate_and_get_arg(user_input, print_no_query)

    if query is None:
        return 

    try:
        search_results = search_files(workspace, query)
        context = build_working_context(search_results)
        response = agent.ask_question(query, context)
    except (FileParseError, ModelResponseError) as error:
        print_exception(error)
        return

    print_agent_response(response)

def handle_interpret_command(user_input: str, agent: CodingAssistantAgent):

    question = validate_and_get_arg(user_input, print_no_query)

    if question is None:
        return 
    
    try:
        interpretation = agent.interpret_task(question)
    except ModelResponseError as error:
        print_exception(error)
        return

    print_interpretation(interpretation)

def handle_select_tool_command(user_input: str, agent: CodingAssistantAgent):

    question = validate_and_get_arg(user_input, print_no_query)

    if question is None:
        return 
    
    try:
        interpretation = agent.interpret_task(question)
        tool = agent.retrieve_tool(interpretation)
    except ModelResponseError as error:
        print_exception(error)
        return
    
    print(tool)

def handle_retrieve_tool_command(user_input: str, agent: CodingAssistantAgent):
    query = validate_and_get_arg(user_input, print_no_query)

    if query is None:
        return 
    try:
        evidence_set = agent.gather_evidence(query)
    except ModelResponseError as error:
        print_exception(error)
        return
    
    print_agent_response(evidence_set.to_console_string())

def handle_find_bug_command(user_input: str, agent: CodingAssistantAgent):
    query = user_input[len("find bugs"):].strip()
    
    if not query:
        print_no_query()
        return
    try:
        bug_analysis: BugAnalysis = agent.find_bugs(query)
    except ModelResponseError as error:
        print_exception(error)
        return
    
    print_agent_response(bug_analysis.to_console_string())

def handle_generate_test_command(user_input: str, agent: CodingAssistantAgent):
    query = user_input[len("generate tests"):].strip()

    if not query:
        print_no_query()
        return
    try:
        test_patch: ProposedPatch = agent.generate_test_patch(user_input.strip())
        print_agent_response(test_patch.to_console_string())
    except (ValueError, ModelResponseError, FrameworkDiscoveryError, AgentResponseError) as error:
        print_exception(error)
        return

def run_app(workspace, agent):
    print_intro(workspace)

    try: 
        while True:
            user_input = input("➜] : ") 

            if should_exit(user_input):
                print_goodbye()
                break

            elif should_explain(user_input):
                handle_explain_command(user_input, agent)

            elif should_ask(user_input):
                handle_ask_command(user_input, agent, workspace)

            elif should_interpret(user_input):
                handle_interpret_command(user_input, agent)

            elif should_select_tool(user_input):
                handle_select_tool_command(user_input, agent)

            elif should_retrieve_tool(user_input):
                handle_retrieve_tool_command(user_input, agent)

            elif should_find_bugs(user_input):
                handle_find_bug_command(user_input, agent)

            elif should_generate_tests(user_input):
                handle_generate_test_command(user_input, agent)

            else:
                print_idk()

    
    except (KeyboardInterrupt, EOFError):
        print_goodbye()



def main():
    client = init_openai_client()
    args = parse_args()
    workspace = args.workspace
    agent = init_agent(workspace, client)

    run_app(workspace, agent)


if __name__ == "__main__":
    main()
