# Coding Assistant

Difficulty: ★★★☆☆

## Description

Use `PREVIOUS_PROJECTS.md` as context when working on this project. The earlier projects introduced model interaction, structured outputs, tool calling, conversation state, retrieval, context construction, and evaluation. This project builds on those foundations by giving an agent controlled access to a code repository.

User:

- "Explain this Java file."
- "Find bugs in this service."
- "Generate tests for this class."
- "Refactor this method."

Agent:

- searches the repository
- reads relevant files
- builds a compact working context
- explains code behavior
- identifies likely defects
- proposes safe changes
- writes patches only through controlled tools
- runs tests when available
- records state for idempotency and retries

This project is not only about making an agent edit code. It is a learning lab for the question:

> How can an agent safely and reliably perform multi-step work in a real repository?

The intended workflow is:

```text
User Task
      ↓
Intent and Permission Classification
      ↓
Repository Search and File Reading
      ↓
Working Context Construction
      ↓
Plan or Explanation
      ↓
Controlled Patch Generation
      ↓
Idempotency Check
      ↓
Apply Patch
      ↓
Run Tests With Retry Policy
      ↓
Report Result, Diff, Tests, and Risks
```

## Tools

The assistant should expose a small, explicit tool surface:

```text
read_file()
search_files()
write_file()
run_tests()
```

The implementation may add internal helpers around these tools, but the project should keep the model-facing tool set intentionally small.

Tool behavior must be validated by application code. The model should never be trusted as the only enforcement mechanism for paths, permissions, retries, or duplicate writes.

## You'll Learn

- multi-tool agent orchestration
- repository context construction
- tool-call accuracy
- tool argument validation
- read vs write permissions
- least privilege
- idempotency
- retry classification
- state management
- duplicate side-effect prevention
- safe patch application
- test execution as a tool
- failure recovery
- indirect prompt injection from code and test output
- trajectory evaluation
- task-completion evaluation

## Version Management

- v0.1 - Read and explain a Java file.
- v0.2 - Search repository files and build working context.
- v0.3 - Add natural-language repository questions, model-directed retrieval, and evidence-based bug finding.
- v0.4 - Generate tests as proposed patches.
- v0.5 - Refactor code through controlled writes.
- v0.6 - Add write permissions and approval policy.
- v0.7 - Add idempotency for write operations.
- v0.8 - Add retry classification for tool failures.
- v0.9 - Add durable task state and operation logs.
- v0.10 - Add prompt-injection safety checks.
- v0.11 - Add a tool-call evaluation suite.
- v0.12 - Add task-completion and regression metrics.

## Current Implementation Status

v0.3 is implemented and smoke-tested through v0.3.6. The application supports
natural-language task interpretation, model-directed selection of approved read
tools, bounded multi-step retrieval, and attributed, deduplicated evidence-set
construction. It produces structured bug findings with validated evidence
references and explicit insufficient-evidence outcomes. Seeded fixture cases
cover concrete defects, ambiguous behavior, style-only observations, and
missing repository evidence. Test-framework discovery is next in v0.4.1.

This project should start from the existing learning baseline rather than rebuilding earlier introductory pieces. The assistant may reuse patterns for CLI interaction, model calls, structured outputs, logging, fake or real model abstractions, and tests.

## v0.1 Acceptance Criteria

- User can ask the assistant to explain a specific Java file.
- `read_file()` accepts only paths inside the configured workspace.
- `read_file()` rejects unsupported file types and path traversal attempts.
- The assistant summarizes the file's purpose, key classes or methods, dependencies, and important control flow.
- The assistant distinguishes direct evidence from inference.
- The assistant reports when the requested file does not exist.
- File explanation behavior is covered by tests using fixture Java files.

## v0.2 Acceptance Criteria

- User can use an explicit search query to ask a repository question.
- `search_files()` can search filenames and file contents.
- Search is deterministic and returns stable ordering.
- Search results include path, line number where available, and a short snippet.
- Large search results are capped before being sent to the model.
- The CLI routes known-file requests through `explain` and literal repository searches through `ask`.
- Search validates its workspace, query, limits, supported file types, and file-read errors.
- Working context groups results by file, preserves source locations, and enforces a total character budget.
- Search and context-construction behavior are covered by tests.

## v0.3 Acceptance Criteria

- User can ask the assistant to find bugs in a Java file or related module.
- User can ask natural-language repository questions without supplying an exact search term.
- The model can choose between reading a known file and searching for relevant files.
- The model can perform multiple retrieval steps when more evidence is required.
- The assistant gathers relevant code before making bug claims.
- Each reported bug includes file path, location when known, reasoning, impact, and confidence.
- The assistant avoids presenting style preferences as bugs.
- The assistant explicitly says when there is not enough evidence to call something a bug.
- Bug-finding behavior is covered by tests with seeded fixture bugs.

## v0.4 Acceptance Criteria

- User can ask the assistant to generate tests for existing code.
- The assistant identifies the likely test framework or reports when it cannot.
- Test generation is represented as a proposed file write or patch.
- New tests are placed in an appropriate test path.
- Generated tests are idempotent: repeating the same request does not duplicate the same test class or test method.
- The assistant can run the relevant tests after generating them when `run_tests()` is available.
- Test-generation behavior is covered by unit tests and at least one integration-style fixture.

## v0.5 Acceptance Criteria

- User can ask the assistant to refactor a method, class, or small module.
- The assistant reads enough surrounding code to preserve behavior.
- Refactors are applied through controlled writes only.
- The assistant reports changed files and the reason for each change.
- The assistant runs relevant tests when available.
- Refactors preserve public behavior unless the user explicitly asks for behavior changes.
- Refactor behavior is covered by tests using small fixture projects.

## v0.6 Acceptance Criteria

- The app separates read-only tasks from write-capable tasks.
- Write operations require an explicit permission mode or approval step.
- `write_file()` validates workspace boundaries, file type, and intended operation.
- The assistant cannot write outside the configured repository.
- The assistant cannot perform destructive actions such as deleting arbitrary files.
- Permission decisions are logged.
- Permission behavior is covered by tests.

## v0.7 Acceptance Criteria

- Every write operation has a stable operation id or content hash.
- Repeating the same write request does not duplicate content.
- Repeating a file-creation request detects that the file already exists.
- Repeating a patch after context loss detects whether the desired change is already applied.
- The app records completed write operations in durable state.
- Idempotency behavior is covered by duplicate-call tests.

## v0.8 Acceptance Criteria

- Tool failures are classified before retrying.
- Transient failures, such as test command timeouts, can be retried according to policy.
- Invalid tool arguments are not retried blindly.
- Missing files are reported as user or context errors rather than transient failures.
- Failed tests are treated as domain results, not infrastructure failures.
- Retry attempts use a maximum attempt count and backoff.
- Retry behavior is covered by tests for timeout, invalid argument, missing file, and failing-test scenarios.

## v0.9 Acceptance Criteria

- The app records task state separately from model conversation history.
- Task state includes user request, selected files, planned actions, completed operations, retry counts, and test results.
- The assistant can summarize current task state after a failure.
- The assistant can avoid repeating completed side effects after a simulated context reset.
- Operation logs are written in a structured format.
- State-management behavior is covered by tests.

## v0.10 Acceptance Criteria

- Fixture repositories include malicious instructions inside source files, comments, documentation, or test output.
- The assistant treats repository content and test output as untrusted data.
- The assistant does not follow repository-contained instructions that conflict with user instructions, system behavior, or tool permissions.
- Unauthorized write attempts are blocked and logged.
- Prompt-injection behavior is evaluated with repeatable tests.

## v0.11 Acceptance Criteria

- Project includes an evaluation dataset of coding-agent tasks.
- Each evaluation case includes the user task, fixture repository, expected relevant tools, forbidden actions, and expected outcome.
- Evaluation records valid tool-call rate, invalid-argument rate, unnecessary-tool-call count, and task-completion result.
- Evaluation allows multiple acceptable trajectories when appropriate.
- Evaluation dataset loading and scoring are covered by tests.

## v0.12 Acceptance Criteria

- Project can run the evaluation suite repeatedly.
- Evaluation reports aggregate task-completion rate.
- Evaluation reports duplicate-write prevention rate.
- Evaluation reports retry success rate and unnecessary retry count.
- Evaluation reports prompt-injection failure rate.
- Evaluation output is suitable for regression testing after prompt or code changes.

## Security Requirements

- Restrict all file operations to a configured workspace root.
- Normalize and validate paths before reading or writing.
- Treat source files, comments, documentation, and test output as untrusted content.
- Separate read tools from write tools.
- Require permission or approval before write operations.
- Avoid unrestricted shell execution.
- Do not expose environment secrets to the model.
- Log blocked operations without executing them.

## Reliability Requirements

- Keep task state outside the model context.
- Use stable operation ids or content hashes for writes.
- Detect already-applied changes before writing.
- Classify failures before retrying.
- Retry only failures that policy marks retryable.
- Set retry limits and backoff for transient failures.
- Treat failing tests as evidence, not as a reason for blind retries.
- Make final reports include files changed, tests run, retries attempted, and remaining risks.
