from __future__ import annotations
import operator

from dataclasses import dataclass, field
from typing import Annotated, Sequence

from pydantic import BaseModel, Field
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.messages import AnyMessage
from langgraph.graph import add_messages

class Repo(BaseModel):
    url: str = Field(
        default="https://github.com/praneetdhoolia/langgraph-se-agent",
        examples=["https://github.com/praneetdhoolia/langgraph-se-agent"],
        description="Repository URL."
    )
    src_folder: str = Field(
        default="src/se_agent",
        examples=["src/se_agent"],
        description="Source folder of the repository."
    )
    branch: str = Field(
        default="main",
        examples=["main", "master", "develop"],
        description="Branch of the repository."
    )

@dataclass(kw_only=True)
class OnboardInputState:
    repo: Repo = Field(default_factory=Repo, description="Details of the onboarding repo.")

    def __post_init__(self):
        if isinstance(self.repo, dict):
            self.repo = Repo(**self.repo)

@dataclass(kw_only=True)
class OnboardState(OnboardInputState):
    repo_id: int = field(default=0)
    """Repository ID. Defaults to 0 if not provided."""

    repo_dir: str = None
    """Temporary directory where the repository is cloned."""

    filepaths: Annotated[list, operator.add] = field(default_factory=list)
    """List of github file paths to be processed."""

    file_summaries: Annotated[list[FileSummary], operator.add] = field(default_factory=list)
    """List of file summaries."""

    package_name_index: dict[str, Package] = field(default_factory=dict)
    """Dict of {pkg_name: Package} for quick lookup."""

    package_summaries: Annotated[list[PackageSummary], operator.add] = field(default_factory=list)
    """List of package summaries."""


@dataclass(kw_only=True)
class FileSummary:
    filepath: str
    """Github file path to be processed."""
    
    summary: str
    """Summary of the file."""


@dataclass(kw_only=True)
class FilepathState:
    filepath: str
    """Github file path to be processed."""

    repo: Repo = Field(
        default_factory=Repo,
        description = """Repository details from GitHub."""
    )

    repo_dir: str = None
    """Temporary directory where the repository is cloned."""


@dataclass(kw_only=True)
class Package:
    package_id: int
    """Storage ID of the package."""

    name: str
    """Name of the package."""

    filepaths: list[str] = field(default_factory=list)
    """List of filepaths. Defaults to an empty list."""

    summary: str | None = field(default=None)
    """Summary of the package (optional)."""


@dataclass(kw_only=True)
class PackageState:
    package_name: str
    """Name of the package."""

    file_summaries: list[FileSummary]
    """List of file summaries."""


@dataclass(kw_only=True)
class PackageSummary:
    package_name: str
    """Name of the package."""

    summary: str
    """Summary of the package."""

################################################################################

@dataclass(kw_only=True)
class InputState:
    messages: Annotated[Sequence[AnyMessage], add_messages]
    """Messages track the primary execution state of the agent."""

    repo: Repo = Field(default_factory=Repo)
    """Repository details from GitHub """

    def __post_init__(self):
        if isinstance(self.repo, dict):
            self.repo = Repo(**self.repo)

################################################################################

class PackageSuggestions(BaseModel):
    packages: list[str] = Field(default_factory=list)
    """List of packages most relevant to the issue or conversation."""

package_suggestions_format_instuctions = PydanticOutputParser(pydantic_object=PackageSuggestions).get_format_instructions()

class FileSuggestion(BaseModel):
    filepath: str
    """Filepath of the file relevant to the issue or conversation."""

    rationale: str
    """Rationale / reasoning for considering this file relevant."""

class FileSuggestions(BaseModel):
    files: list[FileSuggestion] = Field(default_factory=list)
    """List of files most relevant to the issue or conversation."""

file_suggestions_format_instuctions = PydanticOutputParser(pydantic_object=FileSuggestions).get_format_instructions()

@dataclass(kw_only=True)
class FileContent:
    filepath: str
    """Github file path to be processed."""
    
    content: str
    """Content of the file."""

@dataclass(kw_only=True)
class State(InputState):
    repo_id: int = field(default=0)
    """Repository ID. Defaults to 0 if not provided."""

    package_suggestions: PackageSuggestions = field(default_factory=PackageSuggestions)
    """Suggested packages state."""

    package_name_index: dict[str, Package] = field(default_factory=dict)
    """Dict of {pkg_name: Package} for quick lookup."""

    file_suggestions: FileSuggestions = field(default_factory=FileSuggestions)
    """Suggested files state."""

    file_contents: Annotated[list[FileContent], operator.add] = field(default_factory=list)
    """List of file contents."""
