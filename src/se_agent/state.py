from __future__ import annotations
import operator

from dataclasses import dataclass, field
from typing import Annotated



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
