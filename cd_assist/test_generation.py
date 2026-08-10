
from enum import Enum
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator, StringConstraints

MAX_DISCOVERY_FILE_BYTES = 200_000
MAX_DISCOVERY_TEST_FILES = 100
MAX_DISCOVERY_EVIDENCE_PATHS = 10


class FrameworkDiscoveryError(Exception):
    pass

class BuildTool(str, Enum):
    MAVEN = "maven"
    GRADLE = "gradle"
    UNKNOWN = "unknown"
    CONFLICTING = "conflicting"


class TestFramework(str, Enum):
    JUNIT4 = "junit4"
    JUNIT5 = "junit5"
    UNKNOWN = "unknown"


class TestDiscoveryStatus(str, Enum):
    DISCOVERED = "discovered"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    CONFLICTING_EVIDENCE = "conflicting_evidence"

DiscoveryReason = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=2_000),
]

DiscoveryPath = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=500),
]


class TestFrameworkDiscovery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    
    test_framework: TestFramework
    build_tool: BuildTool
    build_reason: DiscoveryReason | None = None
    source_roots: list[DiscoveryPath] = Field(max_length=10)
    test_roots: list[DiscoveryPath] = Field(max_length=10)
    evidence_paths: list[DiscoveryPath] = Field(max_length=10)
    test_status: TestDiscoveryStatus
    test_reason: DiscoveryReason | None = None

    @model_validator(mode="after")
    def validate_analysis_outcome(self):
        if self.build_tool in {BuildTool.UNKNOWN, BuildTool.CONFLICTING} and self.build_reason is None:
            raise ValueError("Unknown or conflicting build tool requires a reason.")
        if self.test_framework == TestFramework.UNKNOWN and self.test_status == TestDiscoveryStatus.DISCOVERED:
            raise ValueError("Framework Discovery with Unknown Framework cannot be discovered.")
        if self.test_status == TestDiscoveryStatus.INSUFFICIENT_EVIDENCE and self.test_reason is None:
            raise ValueError("Framework Discovery with insufficient evidence requires a reason.")
        if self.test_status == TestDiscoveryStatus.CONFLICTING_EVIDENCE and len(self.evidence_paths) == 0:
            raise ValueError("Framework Discovery with conflicting evidence requires evidence.")
        if self.test_framework is not TestFramework.UNKNOWN and len(self.test_roots) == 0:
            raise ValueError("Framework Discovery with Known Framework requires test roots.")
        if self.test_status == TestDiscoveryStatus.DISCOVERED and len(self.evidence_paths) == 0:
            raise ValueError("Successful Framework Discovery requires evidence paths.")
        if self.test_status == TestDiscoveryStatus.CONFLICTING_EVIDENCE and self.test_reason is None:
            raise ValueError("Framework Discovery with Conflicting Evidence requires reason.")
        return self

    @field_validator("source_roots", "test_roots", "evidence_paths")
    @classmethod
    def validate_relative_paths(cls, paths: list[str]) -> list[str]:

        normalized_paths: list[str] = []
        seen: set[str] = set()

        for raw_path in paths:
            path = raw_path.strip()

            if not path:
                raise ValueError("paths must not be blank")

            # Convert Windows separators so results use one stable format.
            path = path.replace("\\", "/")
            parsed = PurePosixPath(path)
            windows_path = PureWindowsPath(path)

            if parsed.is_absolute() or windows_path.is_absolute():
                raise ValueError("paths must be relative")

            if ".." in parsed.parts:
                raise ValueError("paths must not contain parent traversal")

            normalized = parsed.as_posix()

            if normalized == ".":
                raise ValueError("paths must not be blank")

            if normalized in seen:
                raise ValueError("paths must be unique")

            seen.add(normalized)
            normalized_paths.append(normalized)

        return normalized_paths


def discover_build_tool(
    workspace: Path,
) -> tuple[BuildTool, list[DiscoveryPath], DiscoveryReason | None]:
    
    file_evidence = []

    if not workspace.is_dir():
        return BuildTool.UNKNOWN, file_evidence, "Workspace is not a directory."

    maven_config_file = ["pom.xml"]
    gradle_config_file = ["build.gradle", "build.gradle.kts"]

    has_maven = (workspace / "pom.xml").is_file()
    if has_maven:
        file_evidence.extend(maven_config_file)

    has_gradle = False
    for config_file in gradle_config_file:
        if (workspace / config_file).is_file():
            has_gradle = True
            file_evidence.append(config_file)

    if has_gradle and has_maven:
        return BuildTool.CONFLICTING, file_evidence, f"Found both gradle and maven: {file_evidence}"
    elif has_maven:
        return BuildTool.MAVEN, file_evidence, None
    elif has_gradle:
        return BuildTool.GRADLE, file_evidence, None
    else:
        return BuildTool.UNKNOWN, file_evidence, "Neither Gradle or Maven were found."

def discover_source_roots(workspace: Path) -> list[str]:
    if (workspace / "src/main/java").is_dir():
        return ["src/main/java"]
    return []

def discover_test_roots(workspace: Path) -> list[str]:
    expected_path = (workspace / "src/test/java")
    if expected_path.is_dir():
        return ["src/test/java"]
    return []

def contains_any(content: str, keywords: list[str]) -> bool:
    normalized_content = content.lower()
    return any(keyword.lower() in normalized_content for keyword in keywords)

def contains_all(content: str, keywords: list[str]) -> bool:
    normalized_content = content.lower()
    return all(keyword.lower() in normalized_content for keyword in keywords)

def read_discovery_file(
    workspace: Path,
    path: Path,
    max_bytes: int = MAX_DISCOVERY_FILE_BYTES,
) -> str:
    try:
        relative_path = path.relative_to(workspace).as_posix()
    except ValueError as error:
        raise FrameworkDiscoveryError("Discovery file is outside the workspace.") from error

    try:
        with path.open("rb") as file:
            content = file.read(max_bytes + 1)
    except OSError as error:
        raise FrameworkDiscoveryError(
            f"Could not read discovery file: {relative_path}"
        ) from error

    if len(content) > max_bytes:
        raise FrameworkDiscoveryError(
            f"Discovery file exceeds {max_bytes} bytes: {relative_path}"
        )

    try:
        return content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise FrameworkDiscoveryError(
            f"Discovery file is not valid UTF-8: {relative_path}"
        ) from error

def discover_test_files(
    workspace: Path,
    test_root: DiscoveryPath,
    max_files: int = MAX_DISCOVERY_TEST_FILES,
) -> list[Path]:
    if not isinstance(max_files, int) or max_files <= 0:
        raise ValueError("max_files must be a positive integer")

    root = workspace / test_root

    if not root.is_dir():
        return []

    return sorted(
        path
        for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() == ".java"
    )[:max_files]

def inspect_test_framework_evidence(
    workspace: Path,
    test_root: list[str],
) -> tuple[
    TestFramework,
    list[DiscoveryPath],
    TestDiscoveryStatus,
    DiscoveryReason | None,
]:
    junit4_evidence_paths: list[DiscoveryPath] = []
    junit5_evidence_paths: list[DiscoveryPath] = []
    has_junit4 = False
    has_junit5 = False

    # build configuration check 
    config_files = ["pom.xml", "build.gradle", "build.gradle.kts"]
    junit5_config_keywords = ["org.junit.jupiter", "junit-jupiter"]
    junit4_gradle_keywords = ["junit:junit"]
    junit4_maven_keywords = ["<groupId>junit</groupId>", "<artifactId>junit</artifactId>"]

    for file in config_files:
        path = workspace / file
        if path.exists() and path.is_file():
            file_content = read_discovery_file(workspace, path)
            is_junit4 = contains_any(file_content, junit4_gradle_keywords) or contains_all(
                file_content,
                junit4_maven_keywords,
            )
            is_junit5 = contains_any(file_content, junit5_config_keywords)
            if is_junit4:
                has_junit4 = True
                junit4_evidence_paths.append(file)
            if is_junit5:
                has_junit5 = True
                junit5_evidence_paths.append(file)

    # java file check     
    junit5_test_keywords = ["org.junit.jupiter.api.test"]
    junit4_test_keywords = ["org.junit.test"]
   
    for root in test_root:
        paths = discover_test_files(workspace, root)
        for path in paths: 
            file_content = read_discovery_file(workspace, path)
            relative_path = path.relative_to(workspace).as_posix()
            if contains_any(file_content, junit5_test_keywords):
                has_junit5 = True
                junit5_evidence_paths.append(relative_path)
            if contains_any(file_content, junit4_test_keywords):
                has_junit4 = True
                junit4_evidence_paths.append(relative_path)

    junit4_evidence_paths = list(dict.fromkeys(junit4_evidence_paths))
    junit5_evidence_paths = list(dict.fromkeys(junit5_evidence_paths))

    if has_junit4 and has_junit5:
        evidence_paths = list(dict.fromkeys(junit4_evidence_paths + junit5_evidence_paths))[:MAX_DISCOVERY_EVIDENCE_PATHS]
        return TestFramework.UNKNOWN, evidence_paths, TestDiscoveryStatus.CONFLICTING_EVIDENCE, f"Both JUnit4 and JUnit5 were found: {evidence_paths}"
    elif has_junit4:
        return TestFramework.JUNIT4, junit4_evidence_paths[:MAX_DISCOVERY_EVIDENCE_PATHS], TestDiscoveryStatus.DISCOVERED, None
    elif has_junit5:
        return TestFramework.JUNIT5, junit5_evidence_paths[:MAX_DISCOVERY_EVIDENCE_PATHS], TestDiscoveryStatus.DISCOVERED, None
    else:
        return TestFramework.UNKNOWN, junit4_evidence_paths + junit5_evidence_paths, TestDiscoveryStatus.INSUFFICIENT_EVIDENCE, "Neither JUnit4 nor JUnit5 were found."

def validate_workspace(workspace: Path | str) -> Path:
    if not isinstance(workspace, (str, Path)):
        raise TypeError("workspace must be a string or Path")

    if isinstance(workspace, str):
        workspace = workspace.strip()

        if not workspace:
            raise ValueError("workspace must not be empty")

    path = Path(workspace)

    if not path.exists():
        raise ValueError("workspace does not exist")

    if not path.is_dir():
        raise ValueError("workspace must be a directory")

    return path

def discover_test_framework(workspace_input: Path | str) -> TestFrameworkDiscovery:

    workspace = validate_workspace(workspace_input)

    build_tool, build_evidence_path, build_reason = discover_build_tool(workspace)
    source_roots: list[str] = discover_source_roots(workspace)
    test_roots: list[str] = discover_test_roots(workspace)

    test_framework, test_evidence_paths, test_discovery_status, test_reason = inspect_test_framework_evidence(workspace, test_roots)

    evidence_paths = list(dict.fromkeys(build_evidence_path + test_evidence_paths))[:MAX_DISCOVERY_EVIDENCE_PATHS]

    return TestFrameworkDiscovery(
        build_tool=build_tool,
        build_reason=build_reason,
        test_framework=test_framework,
        source_roots=source_roots,
        test_roots=test_roots,
        evidence_paths=evidence_paths,
        test_status=test_discovery_status,
        test_reason=test_reason
    )
