from __future__ import annotations

from dataclasses import dataclass, field
from typing import (
    Annotated,
    Any,
    Literal,
    Optional,
    Sequence,
    Union,
    List,
    Set,
)

from pydantic import BaseModel, Field
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.messages import AnyMessage
from langgraph.graph import add_messages

# -----------------------------------------------------------------------------
# General Utility Function
# -----------------------------------------------------------------------------

def add_or_delete(
    existing: Optional[Union[List[Any], Set[Any]]],
    new: Union[Sequence[Any], Set[Any], Literal["delete"]],
) -> Union[List[Any], Set[Any]]:
    """General-purpose reducer for lists, sets, and sequences in state.

    - If `new` is "delete", the collection is reset.
    - If `existing` is None, it is initialized as an empty list or set.
    - If `new` is None or empty, the existing collection is returned.
    - Otherwise, `new` is added to `existing`, preserving type.

    Args:
        existing (Optional[Union[List[Any], Set[Any]]]): Current state value.
        new (Union[Sequence[Any], Set[Any], Literal["delete"]]): New values to add or the "delete" command.

    Returns:
        Union[List[Any], Set[Any]]: Updated collection after applying the reducer.
    """
    if new == "delete":
        return [] if isinstance(existing, list) else set()

    if existing is None:
        existing = list() if isinstance(new, Sequence) else set()

    if not new:  # Handles both `None` and empty collections
        return existing

    if isinstance(existing, list) and isinstance(new, Sequence):
        return existing + list(new)  # Convert sequence to list
    if isinstance(existing, set) and isinstance(new, set):
        return existing | new  # Equivalent to `existing.union(new)`

    raise TypeError(f"Type mismatch: existing is {type(existing)}, but new is {type(new)}")


# -----------------------------------------------------------------------------
# Data Classes and Models
# -----------------------------------------------------------------------------

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
class FileSummary:
    filepath: str
    """Github file path to be processed."""

    summary: str
    """Summary of the file."""


@dataclass(kw_only=True)
class PackageSummary:
    package_id: str
    """ID of the package in agent's storage."""

    summary: str
    """Summary of the package."""


class EventMetadata(BaseModel):
    modified: list[str] = Field(default_factory=list)
    deleted: list[str] = Field(default_factory=list)


class Event(BaseModel):
    event_type: Annotated[
        Literal["repo-onboard", "repo-update"],
        {"__template_metadata__": {"kind": "event"}}
    ] = Field(
        default="repo-onboard",
        description="Type of event: repo-onboard, repo-update"
    )
    meta_data: EventMetadata = Field(
        default_factory=EventMetadata,
        description="Additional metadata about the event."
    )


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
    commit_hash: str = Field(
        default=None,
        examples=["b9f1a8a", "c1b2a3d"],
        description="Commit hash to onboard on / or update to."
    )

class RepoEvent(BaseModel):
    repo: Repo = Field(
        default_factory=Repo,
        description="Repository details."
    )
    event: Event = Field(
        default_factory=Event,
        description="Event details."
    )


@dataclass(kw_only=True)
class FilepathState:
    filepath: str
    """Github file path to be processed."""

    repo: Repo = Field(
        default_factory=Repo,
        description = """
            Repository details from GitHub.
            Should be avaailable for assist_graph processing.
        """
    )

    event: Event = Field(
        default_factory=Event,
        description = """
            Details of the trigger event, e.g., repo-onboard, repo-update.
            Should be avaailable for assist_graph processing.
        """
    )

    repo_dir: str = None
    """Temporary directory where the repository is cloned."""


@dataclass(kw_only=True)
class PackageState:
    package_id: int
    """ID of the package in agent's storage."""

    repo_id: int
    """Repository ID. Defaults to 0 if not provided."""


@dataclass(kw_only=True)
class FileContent:
    filepath: str
    """Github file path to be processed."""

    content: str
    """Content of the file."""


@dataclass(kw_only=True)
class FileSummaryError:
    filepath: str
    """Github file path to be processed."""

    error: str
    """Error message."""


@dataclass(kw_only=True)
class PackageSummaryError:
    package_id: int
    """ID of the package for which the summary generation failed."""
    
    package_name: str
    """Name of the package."""
    
    error: str
    """Error message."""


# -----------------------------------------------------------------------------
# Input and Graph Managed State for Repository Event Handling
# -----------------------------------------------------------------------------

@dataclass(kw_only=True)
class OnboardInputState:
    repo: Repo = Field(default_factory=Repo, description="Repository details from GitHub.")

    event: Event = Field(default_factory=Event, description="Details of the onboarding event.")

    def __post_init__(self):
        if isinstance(self.repo, dict):
            self.repo = Repo(**self.repo)
        if isinstance(self.event, dict):
            self.event = Event(**self.event)


@dataclass(kw_only=True)
class OnboardState(OnboardInputState):
    repo_id: int = field(default=0)
    """Repository ID. Defaults to 0 if not provided."""

    repo_dir: str = None
    """Temporary directory where the repository is cloned."""

    filepaths: Annotated[list, add_or_delete] = field(default_factory=list)
    """List of github file paths to be processed."""

    file_summaries: Annotated[list[FileSummary], add_or_delete] = field(default_factory=list)
    """List of file summaries."""

    file_summary_errors: Annotated[list[FileSummaryError], add_or_delete] = field(default_factory=list)
    """List of file summary errors."""

    packages_impacted: set[int] = field(default_factory=set)
    """Set of packages impacted by the event."""

    package_summaries: Annotated[list[PackageSummary], add_or_delete] = field(default_factory=list)
    """List of package summaries."""

    package_summary_errors: Annotated[list[PackageSummaryError], add_or_delete] = field(default_factory=list)
    """List of package summary errors."""


# -----------------------------------------------------------------------------
# Suggestion Models
# -----------------------------------------------------------------------------

class PackageSuggestion(BaseModel):
    package_name: str = Field(
        description="Name of the package relevant to the issue or conversation."
    )

    rationale: str = Field(
        description="Rationale / reasoning for considering this package relevant."
    )


class PackageSuggestions(BaseModel):
    packages: list[PackageSuggestion] = Field(
        default_factory=list,
        description="List of packages most relevant to the issue or conversation."
    )


package_suggestions_format_instuctions = PydanticOutputParser(pydantic_object=PackageSuggestions).get_format_instructions()


class FileSuggestion(BaseModel):
    filepath: str = Field(
        description="Filepath of the file relevant to the issue or conversation."
    )

    rationale: str = Field(
        description="Rationale / reasoning for considering this file relevant."
    )


class FileSuggestions(BaseModel):
    files: list[FileSuggestion] = Field(
        default_factory=list,
        description="List of files most relevant to the issue or conversation."
    )


file_suggestions_format_instuctions = PydanticOutputParser(pydantic_object=FileSuggestions).get_format_instructions()

# -----------------------------------------------------------------------------
# Input and Graph Managed State for Assistance
# -----------------------------------------------------------------------------

@dataclass(kw_only=True)
class InputState:
    messages: Annotated[Sequence[AnyMessage], add_messages] = field(default_factory=list)
    """Messages track the primary execution state of the agent."""

    repo: Repo = Field(default_factory=Repo)
    """Repository details from GitHub """

    def __post_init__(self):
        if isinstance(self.repo, dict):
            self.repo = Repo(**self.repo)


@dataclass(kw_only=True)
class State(InputState):
    repo_id: int = field(default=0)
    """Repository ID from agent's database"""

    package_suggestions: PackageSuggestions = field(default_factory=lambda: PackageSuggestions())
    """Suggested packages state."""

    package_name_index: dict[str, Package] = field(default_factory=dict)
    """Dict of {pkg_name: Package} for quick lookup."""

    file_suggestions: FileSuggestions = field(default_factory=lambda: FileSuggestions())
    """Suggested files state."""

    file_contents: Annotated[list[FileContent], add_or_delete] = field(default_factory=list)
    """List of file contents."""
