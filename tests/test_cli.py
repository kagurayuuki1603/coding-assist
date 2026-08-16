import unittest
from unittest.mock import Mock, patch

from cd_assist.cli import (
    handle_ask_command,
    handle_explain_command,
    handle_find_bug_command,
    handle_generate_test_command,
    handle_interpret_command,
    handle_select_tool_command,
    run_app,
)
from cd_assist.errors import FileParseError, ModelResponseError
from cd_assist.models import (
    BugAnalysis,
    BugFinding,
    READ_FILE,
    RetrievalRequest,
    TaskIntent,
    TaskInterpretation,
)


class HandleExplainCommandTests(unittest.TestCase):
    @patch("cd_assist.cli.print_agent_response")
    @patch("cd_assist.cli.print_exception")
    def test_reports_missing_file(self, print_exception, print_agent_response):
        agent = Mock()
        error = FileParseError("Path provided is not a file")
        agent.explain_file.side_effect = error

        handle_explain_command("explain Missing.java", agent)

        agent.explain_file.assert_called_once_with("Missing.java")
        print_exception.assert_called_once_with(error)
        print_agent_response.assert_not_called()


class HandleAskCommandTests(unittest.TestCase):
    @patch("cd_assist.cli.print_agent_response")
    @patch("cd_assist.cli.build_working_context")
    @patch("cd_assist.cli.search_files")
    def test_searches_builds_context_and_asks_agent(
        self,
        search_files,
        build_working_context,
        print_agent_response,
    ):
        agent = Mock()
        search_results = [object()]
        search_files.return_value = search_results
        build_working_context.return_value = "working context"
        agent.ask_question.return_value = "agent answer"

        handle_ask_command("ask validation", agent, "workspace")

        search_files.assert_called_once_with("workspace", "validation")
        build_working_context.assert_called_once_with(search_results)
        agent.ask_question.assert_called_once_with("validation", "working context")
        print_agent_response.assert_called_once_with("agent answer")

    @patch("cd_assist.cli.print_agent_response")
    @patch("cd_assist.cli.print_exception")
    @patch("cd_assist.cli.build_working_context", return_value="working context")
    @patch("cd_assist.cli.search_files", return_value=[])
    def test_reports_model_error(
        self,
        search_files,
        build_working_context,
        print_exception,
        print_agent_response,
    ):
        agent = Mock()
        error = ModelResponseError("Could not generate a model response")
        agent.ask_question.side_effect = error

        handle_ask_command("ask validation", agent, "workspace")

        print_exception.assert_called_once_with(error)
        print_agent_response.assert_not_called()

    @patch("cd_assist.cli.print_no_query")
    def test_reports_missing_query(self, print_no_query):
        agent = Mock()

        handle_ask_command("ask", agent, "workspace")

        print_no_query.assert_called_once_with()
        agent.ask_question.assert_not_called()

    @patch("cd_assist.cli.print_agent_response")
    @patch("cd_assist.cli.print_exception")
    @patch("cd_assist.cli.search_files")
    def test_reports_search_error(
        self,
        search_files,
        print_exception,
        print_agent_response,
    ):
        agent = Mock()
        error = FileParseError("Workspace is not a directory")
        search_files.side_effect = error

        handle_ask_command("ask validation", agent, "workspace")

        print_exception.assert_called_once_with(error)
        agent.ask_question.assert_not_called()
        print_agent_response.assert_not_called()


class HandleInterpretCommandTests(unittest.TestCase):
    @patch("cd_assist.cli.print_interpretation")
    def test_interprets_and_prints_task(self, print_interpretation):
        interpretation = TaskInterpretation(
            intent=TaskIntent.FIND_BUGS,
            target="UserService.java",
            search_terms=["UserService", "validation"],
        )
        agent = Mock()
        agent.interpret_task.return_value = interpretation

        handle_interpret_command(
            "interpret Find validation bugs in UserService.java",
            agent,
        )

        agent.interpret_task.assert_called_once_with(
            "Find validation bugs in UserService.java"
        )
        print_interpretation.assert_called_once_with(interpretation)

    @patch("cd_assist.cli.print_interpretation")
    @patch("cd_assist.cli.print_exception")
    def test_reports_model_error(self, print_exception, print_interpretation):
        agent = Mock()
        error = ModelResponseError("Could not generate a model response")
        agent.interpret_task.side_effect = error

        handle_interpret_command("interpret Explain UserService.java", agent)

        print_exception.assert_called_once_with(error)
        print_interpretation.assert_not_called()

    @patch("cd_assist.cli.print_no_query")
    def test_reports_missing_query(self, print_no_query):
        agent = Mock()

        handle_interpret_command("interpret", agent)

        print_no_query.assert_called_once_with()
        agent.interpret_task.assert_not_called()


class RunAppTests(unittest.TestCase):
    @patch("cd_assist.cli.print_goodbye")
    @patch("cd_assist.cli.print_intro")
    @patch("cd_assist.cli.handle_explain_command")
    @patch("cd_assist.cli.handle_ask_command")
    @patch("builtins.input", side_effect=["ask validation", "explain Example.java", "exit"])
    def test_routes_ask_and_explain_commands(
        self,
        input_mock,
        handle_ask_command,
        handle_explain_command,
        print_intro,
        print_goodbye,
    ):
        agent = Mock()

        run_app("workspace", agent)

        handle_ask_command.assert_called_once_with("ask validation", agent, "workspace")
        handle_explain_command.assert_called_once_with("explain Example.java", agent)
        print_goodbye.assert_called_once_with()

    @patch("cd_assist.cli.print_goodbye")
    @patch("cd_assist.cli.print_intro")
    @patch("cd_assist.cli.handle_interpret_command")
    @patch(
        "builtins.input",
        side_effect=["interpret Find bugs in UserService.java", "exit"],
    )
    def test_routes_interpret_command(
        self,
        input_mock,
        handle_interpret_command,
        print_intro,
        print_goodbye,
    ):
        agent = Mock()

        run_app("workspace", agent)

        handle_interpret_command.assert_called_once_with(
            "interpret Find bugs in UserService.java",
            agent,
        )
        print_goodbye.assert_called_once_with()

    @patch("cd_assist.cli.print_goodbye")
    @patch("cd_assist.cli.print_intro")
    @patch("cd_assist.cli.handle_select_tool_command")
    @patch(
        "builtins.input",
        side_effect=["select Find bugs in UserService.java", "exit"],
    )
    def test_routes_select_command(
        self,
        input_mock,
        handle_select_tool_command,
        print_intro,
        print_goodbye,
    ):
        agent = Mock()

        run_app("workspace", agent)

        handle_select_tool_command.assert_called_once_with(
            "select Find bugs in UserService.java",
            agent,
        )
        print_goodbye.assert_called_once_with()

    @patch("cd_assist.cli.print_goodbye")
    @patch("cd_assist.cli.print_intro")
    @patch("cd_assist.cli.handle_find_bug_command")
    @patch(
        "builtins.input",
        side_effect=["find bugs in ExampleService.java", "exit"],
    )
    def test_routes_find_bugs_command(
        self,
        input_mock,
        handle_find_bug_command,
        print_intro,
        print_goodbye,
    ):
        agent = Mock()

        run_app("workspace", agent)

        handle_find_bug_command.assert_called_once_with(
            "find bugs in ExampleService.java",
            agent,
        )
        print_goodbye.assert_called_once_with()

    @patch("cd_assist.cli.print_goodbye")
    @patch("cd_assist.cli.print_intro")
    @patch("cd_assist.cli.handle_generate_test_command")
    @patch(
        "builtins.input",
        side_effect=["generate tests for RetryPolicy.java", "exit"],
    )
    def test_routes_generate_tests_command(
        self,
        input_mock,
        handle_generate_test_command,
        print_intro,
        print_goodbye,
    ):
        agent = Mock()

        run_app("workspace", agent)

        handle_generate_test_command.assert_called_once_with(
            "generate tests for RetryPolicy.java",
            agent,
        )
        print_goodbye.assert_called_once_with()


class HandleSelectToolCommandTests(unittest.TestCase):
    @patch("builtins.print")
    def test_interprets_selects_and_prints_request(self, print_mock):
        interpretation = TaskInterpretation(
            intent=TaskIntent.FIND_BUGS,
            target="UserService.java",
            search_terms=["UserService"],
        )
        request = RetrievalRequest(
            tool=READ_FILE,
            path="UserService.java",
            query=None,
        )
        agent = Mock()
        agent.interpret_task.return_value = interpretation
        agent.retrieve_tool.return_value = request

        handle_select_tool_command("select Find bugs in UserService.java", agent)

        agent.interpret_task.assert_called_once_with(
            "Find bugs in UserService.java"
        )
        agent.retrieve_tool.assert_called_once_with(interpretation)
        print_mock.assert_called_once_with(request)

    @patch("cd_assist.cli.print_no_query")
    def test_reports_missing_query(self, print_no_query):
        agent = Mock()

        handle_select_tool_command("select", agent)

        print_no_query.assert_called_once_with()
        agent.interpret_task.assert_not_called()
        agent.retrieve_tool.assert_not_called()

    @patch("builtins.print")
    @patch("cd_assist.cli.print_exception")
    def test_reports_model_error(self, print_exception, print_mock):
        agent = Mock()
        error = ModelResponseError("Could not generate a model response")
        agent.retrieve_tool.side_effect = error

        handle_select_tool_command("select Find bugs", agent)

        print_exception.assert_called_once_with(error)
        print_mock.assert_not_called()


class HandleFindBugCommandTests(unittest.TestCase):
    @patch("cd_assist.cli.print_agent_response")
    def test_finds_bugs_and_prints_structured_analysis(self, print_agent_response):
        analysis = BugAnalysis(
            findings=[
                BugFinding(
                    path="ExampleService.java",
                    start_line=12,
                    reasoning="The result can contain a null component.",
                    impact="Users can receive an invalid display name.",
                    confidence="high",
                    evidence_indices=[0],
                )
            ],
            insufficient_evidence_reason=None,
        )
        agent = Mock()
        agent.find_bugs.return_value = analysis

        handle_find_bug_command(
            "find bugs in ExampleService.java",
            agent,
        )

        agent.find_bugs.assert_called_once_with("in ExampleService.java")
        print_agent_response.assert_called_once_with(analysis.to_console_string())

    @patch("cd_assist.cli.print_no_query")
    def test_reports_missing_bug_query(self, print_no_query):
        agent = Mock()

        handle_find_bug_command("find bugs", agent)

        print_no_query.assert_called_once_with()
        agent.find_bugs.assert_not_called()

    @patch("cd_assist.cli.print_agent_response")
    @patch("cd_assist.cli.print_exception")
    def test_reports_bug_analysis_error(self, print_exception, print_agent_response):
        agent = Mock()
        error = ModelResponseError("Could not analyze bugs")
        agent.find_bugs.side_effect = error

        handle_find_bug_command("find bugs in ExampleService.java", agent)

        print_exception.assert_called_once_with(error)
        print_agent_response.assert_not_called()


class HandleGenerateTestCommandTests(unittest.TestCase):
    @patch("cd_assist.cli.print_agent_response")
    def test_generates_and_prints_test_patch(self, print_agent_response):
        test_patch = Mock()
        test_patch.to_console_string.return_value = "test patch"
        agent = Mock()
        agent.generate_test_patch.return_value = test_patch

        handle_generate_test_command(
            "generate tests for RetryPolicy.java",
            agent,
        )

        agent.generate_test_patch.assert_called_once_with(
            "generate tests for RetryPolicy.java"
        )
        print_agent_response.assert_called_once_with("test patch")

    @patch("cd_assist.cli.print_no_query")
    def test_reports_missing_query(self, print_no_query):
        agent = Mock()

        handle_generate_test_command("generate tests", agent)

        print_no_query.assert_called_once_with()
        agent.generate_test_patch.assert_not_called()

    @patch("cd_assist.cli.print_agent_response")
    @patch("cd_assist.cli.print_exception")
    def test_reports_patch_generation_error(
        self,
        print_exception,
        print_agent_response,
    ):
        agent = Mock()
        error = ModelResponseError("Could not interpret test-generation task")
        agent.generate_test_patch.side_effect = error

        handle_generate_test_command(
            "generate tests for RetryPolicy.java",
            agent,
        )

        print_exception.assert_called_once_with(error)
        print_agent_response.assert_not_called()

if __name__ == "__main__":
    unittest.main()
