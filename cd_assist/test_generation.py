
from enum import Enum
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator, StringConstraints

from cd_assist.models import EvidenceSet, TaskInterpretation

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
        return normalize_relative_paths(paths)


class TestGenerationContext(BaseModel):
    request: str
    interpretation: TaskInterpretation
    discovery: TestFrameworkDiscovery
    evidence: EvidenceSet

    def to_console_string(self) -> str:
        target = self.interpretation.target or "Unknown"
        search_terms = ", ".join(self.interpretation.search_terms)

        discovery_reason_parts = [
            reason
            for reason in (
                self.discovery.build_reason,
                self.discovery.test_reason,
            )
            if reason is not None
        ]
        discovery_reason = (
            " ".join(discovery_reason_parts)
            if discovery_reason_parts
            else "None"
        )

        evidence = self.evidence.to_console_string()

        return (
            f"Request: {self.request}\n\n"
            "Task Interpretation\n"
            f"Intent: {self.interpretation.intent.value}\n"
            f"Target: {target}\n"
            f"Search Terms: {search_terms}\n\n"
            "Test Discovery\n"
            f"Build Tool: {self.discovery.build_tool.value}\n"
            f"Test Framework: {self.discovery.test_framework.value}\n"
            f"Source Roots: {', '.join(self.discovery.source_roots) or 'None'}\n"
            f"Test Roots: {', '.join(self.discovery.test_roots) or 'None'}\n"
            f"Status: {self.discovery.test_status.value}\n"
            f"Reason: {discovery_reason}\n"
            f"Evidence Paths: "
            f"{', '.join(self.discovery.evidence_paths) or 'None'}\n\n"
            "Repository Evidence\n"
            f"{evidence}"
        )

ProposedTestCaseName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=500),
]

BoundedProposalText = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=1_000,
    ),
]

class ProposedTestCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: ProposedTestCaseName
    behavior: BoundedProposalText
    rationale: BoundedProposalText
    evidence_indices: list[Annotated[int, Field(ge=0)]] = Field(min_length=1,max_length=10)

    @field_validator("evidence_indices")
    @classmethod
    def validate_evidence_indices(cls, indices: list[int]) -> list[int]:
        if len(indices) != len(set(indices)):
            raise ValueError("Evidence indices must be unique")

        return indices


class TestProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_path: str | None
    proposed_test_path: str | None
    test_framework: TestFramework
    test_cases: list[ProposedTestCase] = Field(max_length=10)
    assumptions: list[BoundedProposalText] = Field(max_length=10)
    insufficient_evidence_reason: BoundedProposalText | None = None


    @model_validator(mode="after")
    def validate_test_proposal(self):
        if self.insufficient_evidence_reason is None :
            if len(self.test_cases) == 0:
                raise ValueError("Successful proposal requires at least 1 proposed testcase.")
            if self.target_path is None:
                raise ValueError("Successful proposal requires target path.")
            if self.proposed_test_path is None:
                raise ValueError("Successful proposal requires proposed test path.")

        if self.insufficient_evidence_reason is not None and len(self.test_cases) > 0:
            raise ValueError("Unsuccessful proposal should not have any proposed testcase.")

        if self.test_framework == TestFramework.UNKNOWN and len(self.test_cases) > 0:
            raise ValueError("Unsuccessful framework should not have any proposed testcase.")

        normalized_names = [test_case.name.strip().lower() for test_case in self.test_cases]
        if len(normalized_names) != len(set(normalized_names)):
            raise ValueError("Test names must be unique")
        return self

    @field_validator("target_path", "proposed_test_path")
    @classmethod
    def validate_relative_paths(cls, path: str | None) -> str | None:
        if path is not None:
            return normalize_relative_path(path)
        else:
            return None

    def validate_result(self, context: TestGenerationContext):
        if self.test_framework != context.discovery.test_framework:
            raise ValueError("Framework in proposal and discovery does not match.")

        if self.insufficient_evidence_reason is None:
            validate_path_under_test_root(
                self.proposed_test_path,
                context.discovery.test_roots,
            )
            validate_path_in_evidence_set(self.target_path, context.evidence)
            validate_evidence_indices_in_range(
                self.test_cases,
                len(context.evidence.items),
            )

        return self

    def to_console_string(self) -> str:
        header = (
            "Test Proposal\n"
            f"Target: {self.target_path}\n"
            f"Proposed Test Path: {self.proposed_test_path}\n"
            f"Test Framework: {self.test_framework.value}\n"
        )

        if self.insufficient_evidence_reason is not None:
            return (
                f"{header}"
                "Status: insufficient_evidence\n"
                f"Reason: {self.insufficient_evidence_reason}"
            )

        test_cases = "\n".join(
            (
                f"{index}. {test_case.name}\n"
                f"   Behavior: {test_case.behavior}\n"
                f"   Rationale: {test_case.rationale}\n"
                "   Evidence: "
                f"{', '.join(str(item) for item in test_case.evidence_indices)}"
            )
            for index, test_case in enumerate(self.test_cases, start=1)
        )
        assumptions = (
            "\n".join(f"- {assumption}" for assumption in self.assumptions)
            if self.assumptions
            else "None"
        )

        return (
            f"{header}"
            "Status: proposed\n\n"
            "Test Cases\n"
            f"{test_cases}\n\n"
            "Assumptions\n"
            f"{assumptions}"
        )


class PatchOperation(str, Enum):
    CREATE = "create"
    # MODIFY = "modify"

BoundedPatchText = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=2_000,
    ),
]

class ProposedPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: PatchOperation
    path: str
    expected_existing_content: str | None
    proposed_content: BoundedPatchText
    rationale: BoundedPatchText
    applied: Literal[False]

    @model_validator(mode="after")
    def validate_proposed_patch(self):
        if self.operation == PatchOperation.CREATE:
            if self.expected_existing_content is not None:
                raise ValueError("CREATE patch should not have any existing content.")
        return self


    @field_validator("path")
    @classmethod
    def validate_relative_paths(cls, path: str) -> str :
        return normalize_relative_path(path)

    def validate_result(self, test_proposal: TestProposal, test_discovery: TestFrameworkDiscovery, workspace: Path | str):
        if test_proposal.test_framework != test_discovery.test_framework:
            raise ValueError("Framework in proposal and discovery does not match.")

        validate_path_under_test_root(self.path, test_discovery.test_roots)

        # check that destination does not exist for CREATE
        if self.operation == PatchOperation.CREATE:
            workspace = Path(workspace).resolve()
            destination = (workspace / self.path).resolve()

            if not destination.is_relative_to(workspace):
                raise ValueError("Patch destination is outside the workspace.")

            if destination.exists():
                raise ValueError("CREATE patch destination already exists.")

        # validate that destination ends with .java
        path_parsed = PurePosixPath(self.path)

        if path_parsed.suffix.lower() != ".java":
            raise ValueError("Patch destination must be a Java file.")

        if self.path != test_proposal.proposed_test_path:
            raise ValueError("Path does not match TestProposal proposed test path.")

        #filename matches declared test class
        expected_class_name = PurePosixPath(self.path).stem
        if not re.search(rf"\bclass\s+{re.escape(expected_class_name)}\b", self.proposed_content):
            raise ValueError("Declared test class does not match destination filename.")

        # validate proposal imports
        junit5_test_keyword = ["org.junit.jupiter.api.test"]
        junit4_test_keyword = ["org.junit.test"]

        if test_proposal.test_framework == TestFramework.JUNIT4:
            if not contains_any(self.proposed_content, junit4_test_keyword):
                raise ValueError("JUnit4 Framework proposed test did not use Junit4 imports.")
            if contains_any(self.proposed_content, junit5_test_keyword):
                raise ValueError("JUnit4 Framework proposed test used Junit5 imports.")
        elif test_proposal.test_framework == TestFramework.JUNIT5:
            if not contains_any(self.proposed_content, junit5_test_keyword):
                raise ValueError("JUnit5 Framework proposed test did not use Junit5 imports.")
            if contains_any(self.proposed_content, junit4_test_keyword):
                raise ValueError("JUnit5 Framework proposed test used Junit4 imports.")

        # validate proposed content contains proposal test methods
        proposed_test_names = [ test_case.name for test_case in test_proposal.test_cases ]

        if not contains_all(self.proposed_content, proposed_test_names):
            raise ValueError("Not all proposed test names were generated.")

    def to_console_string(self) -> str:
        expected_existing_content = (
            self.expected_existing_content
            if self.expected_existing_content is not None
            else "None"
        )

        return (
            "Proposed Test Patch\n"
            f"Operation: {self.operation.value}\n"
            f"Path: {self.path}\n"
            f"Expected Existing Content: {expected_existing_content}\n"
            f"Applied: {self.applied}\n\n"
            "Rationale\n"
            f"{self.rationale}\n\n"
            "Proposed Content\n"
            f"{self.proposed_content}"
        )


class TestClassification(str, Enum):
    NEW = "new"
    ALREADY_PRESENT = "already present"
    PARTIALLY_PRESENT = "partially present"
    CONFLICTING = "conflicting"

ExistingMethodIdentity = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=2_000
    )
]

class TestAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    classification: TestClassification
    destination_path: str
    existing_method_identities: list[ExistingMethodIdentity] = Field(max_length=100)
    missing_test_cases: list[ProposedTestCase] = Field(max_length=100)
    conflicting_reason: BoundedProposalText | None = None

    @field_validator("destination_path")
    @classmethod
    def validate_relative_paths(cls, path: str) -> str :
        return normalize_relative_path(path)

    @field_validator("existing_method_identities")
    @classmethod
    def reject_duplicate_method_identities(cls, identities: list[str]) -> list[str]:
        if len(identities) != len(set(identities)):
            raise ValueError("Existing method identities must be unique.")

        return identities

    @model_validator(mode="after")
    def validate_test_assessment(self):

        if self.classification == TestClassification.CONFLICTING and self.conflicting_reason is None:
            raise ValueError("Conflicting classification requires a reason.")
        if self.classification != TestClassification.CONFLICTING and self.conflicting_reason is not None:
            raise ValueError("Non-conflicting classification should not have a reason.")

        if self.classification == TestClassification.PARTIALLY_PRESENT:
            if len(self.missing_test_cases) == 0:
                raise ValueError("Partially present test proposal requires missing tests.")
            if len(self.existing_method_identities) == 0:
                raise ValueError("Partially present test proposal requires existing tests.")

        if self.classification == TestClassification.NEW:
            if len(self.existing_method_identities) > 0:
                raise ValueError("New test proposal should not have any existing methods.")
            if len(self.missing_test_cases) == 0:
                raise ValueError("New test proposal should have missing test cases.")

        if self.classification == TestClassification.ALREADY_PRESENT:
            if len(self.missing_test_cases) > 0:
                raise ValueError("Already present test proposal should not have any missing test cases.")
            if len(self.existing_method_identities) == 0:
                raise ValueError("Already present test proposal requires existing tests.")

        return self

class ExistingTestInspection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    destination_path: str
    destination_exists: bool
    declared_class_name: str | None
    method_identities: list[ExistingMethodIdentity]

    @field_validator("method_identities")
    @classmethod
    def reject_duplicate_method_identities(cls, identities: list[str]) -> list[str]:
        if len(identities) != len(set(identities)):
            raise ValueError("Existing method identities must be unique.")

        return identities


def inspect_existing_test(workspace: Path | str, destination_path: str) -> ExistingTestInspection:
    JAVA_CLASS_PATTERN = re.compile(r"\b(?:public\s+)?(?:final\s+|abstract\s+)?class\s+([A-Za-z_$][\w$]*)\b")
    JAVA_TEST_METHOD_PATTERN = re.compile(
        r"@Test(?:\s*\([^)]*\))?\s+"
        r"(?:(?:public|protected|private|static|final|synchronized)\s+)*"
        r"[A-Za-z_$][\w$<>,.?\[\]]*\s+"
        r"([A-Za-z_$][\w$]*)\s*\(",
        re.MULTILINE,
    )

    workspace_path = Path(workspace).resolve()
    destination = (workspace_path / destination_path).resolve()

    if not destination.is_relative_to(workspace_path):
        raise ValueError("Test destination is outside the workspace.")

    if destination.suffix.lower() != ".java":
        raise ValueError("Test destination must be a Java file.")

    normalized_path = destination.relative_to(workspace_path).as_posix()

    if not destination.exists():
        return ExistingTestInspection(
            destination_path=normalized_path,
            destination_exists=False,
            declared_class_name=None,
            method_identities=[],
        )

    if not destination.is_file():
        raise ValueError("Test destination is not a file.")

    content = destination.read_text(encoding="utf-8")
    match = JAVA_CLASS_PATTERN.search(content)

    if match is None:
        raise ValueError("Existing Java test does not declare a class.")

    declared_class_name = match.group(1)

    if declared_class_name != destination.stem:
        raise ValueError(
            "Existing Java class does not match the destination filename."
        )

    method_identities = JAVA_TEST_METHOD_PATTERN.findall(content)

    return ExistingTestInspection(
        destination_path=normalized_path,
        destination_exists=True,
        declared_class_name=declared_class_name,
        method_identities=method_identities,
    )

def classify_test_overlap(proposal: TestProposal, inspection: ExistingTestInspection) -> TestAssessment:
    if proposal.proposed_test_path is None:
        raise ValueError("Test proposal requires a destination path.")

    destination_path = proposal.proposed_test_path

    try:
        inspected_path = normalize_relative_path(inspection.destination_path)
    except ValueError:
        return TestAssessment(
            classification=TestClassification.CONFLICTING,
            destination_path=destination_path,
            existing_method_identities=[],
            missing_test_cases=[],
            conflicting_reason="Inspected test path is invalid.",
        )

    if inspected_path != destination_path:
        return TestAssessment(
            classification=TestClassification.CONFLICTING,
            destination_path=destination_path,
            existing_method_identities=[],
            missing_test_cases=[],
            conflicting_reason="Inspected test path does not match the proposed test path.",
        )

    expected_class_name = PurePosixPath(destination_path).stem
    if (
        inspection.destination_exists
        and inspection.declared_class_name != expected_class_name
    ):
        return TestAssessment(
            classification=TestClassification.CONFLICTING,
            destination_path=destination_path,
            existing_method_identities=[],
            missing_test_cases=[],
            conflicting_reason="Existing test class does not match the destination filename.",
        )

    existing_identities = inspection.method_identities
    if len(existing_identities) != len(set(existing_identities)):
        return TestAssessment(
            classification=TestClassification.CONFLICTING,
            destination_path=destination_path,
            existing_method_identities=[],
            missing_test_cases=[],
            conflicting_reason="Existing test contains ambiguous method identities.",
        )

    existing = set(existing_identities)
    present_cases = [
        test_case
        for test_case in proposal.test_cases
        if test_case.name in existing
    ]
    missing_cases = [
        test_case
        for test_case in proposal.test_cases
        if test_case.name not in existing
    ]
    present_identities = [test_case.name for test_case in present_cases]

    if not present_cases:
        classification = TestClassification.NEW
    elif not missing_cases:
        classification = TestClassification.ALREADY_PRESENT
    else:
        classification = TestClassification.PARTIALLY_PRESENT

    return TestAssessment(
        classification=classification,
        destination_path=destination_path,
        existing_method_identities=present_identities,
        missing_test_cases=missing_cases,
        conflicting_reason=None,
    )

def validate_evidence_indices_in_range(
    test_cases: list[ProposedTestCase],
    evidence_set_item_count: int,
) -> None:
    if not all(
        index < evidence_set_item_count
        for test_case in test_cases
        for index in test_case.evidence_indices
    ):
        raise ValueError("Evidence index is outside the evidence set")

def validate_path_in_evidence_set(target_path: str, evidence_set: EvidenceSet) -> None:
    if not any(target_path == item.path for item in evidence_set.items):
        raise ValueError("Target path must match a repository evidence path")

def validate_path_under_test_root(proposed_path: str, test_roots: list[str]) -> bool:
    normalized_path = normalize_relative_path(proposed_path)
    proposed = PurePosixPath(normalized_path)

    roots = [
        PurePosixPath(normalize_relative_path(root))
        for root in test_roots
    ]

    if not any(proposed.is_relative_to(root) and proposed != root for root in roots):
        raise ValueError(
            "Proposed test path must be beneath a discovered test root"
        )

    return True

def normalize_relative_paths(paths: list[str]) -> list[str]:
    normalized_paths = [
        normalize_relative_path(path)
        for path in paths
    ]

    if len(normalized_paths) != len(set(normalized_paths)):
        raise ValueError("paths must be unique")

    return normalized_paths

def normalize_relative_path(raw_path: str) -> str:
    path = raw_path.strip()

    if not path:
        raise ValueError("path must not be blank")

    path = path.replace("\\", "/")
    parsed = PurePosixPath(path)
    windows_path = PureWindowsPath(path)

    if parsed.is_absolute() or windows_path.is_absolute():
        raise ValueError("path must be relative")

    if ".." in parsed.parts:
        raise ValueError("path must not contain parent traversal")

    normalized = parsed.as_posix()

    if normalized == ".":
        raise ValueError("path must not be blank")

    return normalized

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
