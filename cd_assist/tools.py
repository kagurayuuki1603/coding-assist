# controlled, model-facing repository tools
from pathlib import Path
from enum import Enum
from dataclasses import dataclass

from cd_assist.errors import FileParseError
from cd_assist.workspace import Workspace, WorkspaceError, WorkspaceErrorReason, as_workspace


def read_file(workspace: Workspace | str | Path, requested_path: str, max_char=50_000):
    try:
        workspace = as_workspace(workspace)
        target = workspace.resolve(requested_path)
        if target.suffix.lower() != ".java":
            raise FileParseError("File is not a .java file")
        content, truncated = workspace.read_bounded(requested_path, max_chars=max_char)
        return {"content": content, "truncated": truncated}
    except WorkspaceError as error:
        if error.reason in {
            WorkspaceErrorReason.INVALID_PATH,
            WorkspaceErrorReason.OUTSIDE_WORKSPACE,
        }:
            raise FileParseError("Path is outside the workspace") from error
        if error.reason in {WorkspaceErrorReason.MISSING, WorkspaceErrorReason.NOT_FILE}:
            raise FileParseError("Path provided is not a file") from error
        if error.reason == WorkspaceErrorReason.INVALID_UTF8:
            raise FileParseError("File is not valid UTF-8") from error
        raise FileParseError(str(error)) from error

##### search tools #####
class MatchType(str, Enum):
    PATH = "path"
    CONTENT = "content"

@dataclass
class LineSnippet:
    line: int | None = None
    snippet: str = ""

@dataclass
class SearchResult:
    path: str
    match_type: MatchType
    line_snippet: LineSnippet | None = None

    def to_context_str(self) -> str:
        if self.match_type == MatchType.CONTENT:
            return (
                    f"File: {self.path}\n"
                    f"Line {self.line_snippet.line}: {self.line_snippet.snippet}"
                )
        else:
            return (
                    f"File: {self.path}\n"
                    "Path match"
                )


def search_files(workspace: Workspace | str | Path, query: str, max_results=20, max_snippet_chars=200) -> list[SearchResult]:
    try:
        workspace = as_workspace(workspace)
    except WorkspaceError as error:
        raise FileParseError("Workspace is not a directory") from error
    query = query.strip()

    if not query:
        raise FileParseError("Search query cannot be empty")

    if not isinstance(max_results, int) or max_results <= 0:
        raise FileParseError("max_results must be a positive integer")

    if not isinstance(max_snippet_chars, int) or max_snippet_chars <= 0:
        raise FileParseError("max_snippet_chars must be a positive integer")

    results = []

    try:
        paths = workspace.files(suffix=".java")
    except WorkspaceError as error:
        raise FileParseError(f"Could not search workspace: {error}") from error

    for path in paths:
        relative_path = workspace.relative_path(path)

        if query.lower() in relative_path.lower():
            results.append(
                SearchResult(
                    path=relative_path,
                    match_type=MatchType.PATH,
                )
            )

            if len(results) >= max_results:
                return results

        try:
            with path.open(encoding="utf-8") as file:
                for line_number, line in enumerate(file, start=1):
                    if query.lower() in line.lower():
                        results.append(
                            SearchResult(
                                path=relative_path,
                                match_type=MatchType.CONTENT,
                                line_snippet=LineSnippet(
                                    line=line_number,
                                    snippet=line[:max_snippet_chars],
                                ),
                            )
                        )

                    if len(results) >= max_results:
                        return results
        except UnicodeDecodeError as error:
            raise FileParseError(
                f"File is not valid UTF-8: {relative_path}"
            ) from error
        except OSError as error:
            raise FileParseError(
                f"Could not read file: {relative_path}: {error}"
            ) from error

    return results
