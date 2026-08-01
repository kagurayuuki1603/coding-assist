# controlled, model-facing repository tools
from pathlib import Path
from enum import Enum
from dataclasses import dataclass

from cd_assist.errors import FileParseError


def read_file(workspace: str | Path, requested_path: str, max_char=50_000):
    workspace = Path(workspace).resolve()
    target = (workspace / requested_path).resolve()

    if not target.is_relative_to(workspace):
        raise FileParseError("Path is outside the workspace")

    if not target.is_file():
        raise FileParseError("Path provided is not a file")
    
    if target.suffix.lower() != ".java":
        raise FileParseError("File is not a .java file")

    try:
        with target.open(encoding="utf-8") as file:
            content = file.read(max_char + 1)
    except UnicodeDecodeError as error:
        raise FileParseError("File is not valid UTF-8") from error
    except OSError as error:
        raise FileParseError(f"Could not read file: {error}") from error
    
    return {
        "content": content if len(content) <= max_char else content[:max_char],
        "truncated": len(content) > max_char
    }

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


def search_files(workspace: str | Path, query: str, max_results=20, max_snippet_chars=200) -> list[SearchResult]:
    workspace = Path(workspace).resolve()
    query = query.strip()

    if not workspace.is_dir():
        raise FileParseError("Workspace is not a directory")

    if not query:
        raise FileParseError("Search query cannot be empty")

    if not isinstance(max_results, int) or max_results <= 0:
        raise FileParseError("max_results must be a positive integer")

    if not isinstance(max_snippet_chars, int) or max_snippet_chars <= 0:
        raise FileParseError("max_snippet_chars must be a positive integer")

    results = []

    try:
        paths = sorted(
            (
                path
                for path in workspace.rglob("*")
                if path.is_file() and path.suffix.lower() == ".java"
            ),
            key=lambda path: path.relative_to(workspace).as_posix(),
        )
    except OSError as error:
        raise FileParseError(f"Could not search workspace: {error}") from error

    for path in paths:
        resolved_path = path.resolve()
        if not resolved_path.is_relative_to(workspace):
            raise FileParseError("Search path is outside the workspace")

        relative_path = path.relative_to(workspace)

        if query.lower() in relative_path.as_posix().lower():
            results.append(
                SearchResult(
                    path=relative_path.as_posix(),
                    match_type=MatchType.PATH,
                )
            )

            if len(results) >= max_results:
                return results

        try:
            with resolved_path.open(encoding="utf-8") as file:
                for line_number, line in enumerate(file, start=1):
                    if query.lower() in line.lower():
                        results.append(
                            SearchResult(
                                path=relative_path.as_posix(),
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
                f"File is not valid UTF-8: {relative_path.as_posix()}"
            ) from error
        except OSError as error:
            raise FileParseError(
                f"Could not read file: {relative_path.as_posix()}: {error}"
            ) from error

    return results
