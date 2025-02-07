from __future__ import annotations
import operator

from dataclasses import dataclass, field
from typing import Annotated, Sequence

from pydantic import BaseModel, Field
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.messages import AnyMessage
from langgraph.graph import add_messages


@dataclass(kw_only=True)
class OnboardState:
    repo_id: int = field(default=0)
    """Repository ID. Defaults to 0 if not provided."""

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
    """Represents the input state for the agent.

    This class defines the structure of the input state, which includes
    the messages exchanged between the user and the agent. It serves as
    a restricted version of the full State, providing a narrower interface
    to the outside world compared to what is maintained internally.
    """

    messages: Annotated[Sequence[AnyMessage], add_messages]
    """Messages track the primary execution state of the agent.

    Typically accumulates a pattern of Human/AI/Human/AI messages; if
    you were to combine this template with a tool-calling ReAct agent pattern,
    it may look like this:

    1. HumanMessage - user input
    2. AIMessage with .tool_calls - agent picking tool(s) to use to collect
         information
    3. ToolMessage(s) - the responses (or errors) from the executed tools
    
        (... repeat steps 2 and 3 as needed ...)
    4. AIMessage without .tool_calls - agent responding in unstructured
        format to the user.

    5. HumanMessage - user responds with the next conversational turn.

        (... repeat steps 2-5 as needed ... )
    
    Merges two lists of messages, updating existing messages by ID.

    By default, this ensures the state is "append-only", unless the
    new message has the same ID as an existing message.

    Returns:
        A new list of messages with the messages from `right` merged into `left`.
        If a message in `right` has the same ID as a message in `left`, the
        message from `right` will replace the message from `left`."""

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
