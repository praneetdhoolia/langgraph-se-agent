from __future__ import annotations
import os
from dataclasses import dataclass, field, fields
from typing import Annotated, Optional, Type, TypeVar

from langchain_core.runnables import RunnableConfig, ensure_config

from se_agent import prompts

@dataclass(kw_only=True)
class Configuration:
    """
    Configuration for the se-agent
    """

    file_summary_system_prompt: str = field (
        default=prompts.FILE_SUMMARY_SYSTEM_PROMPT,
        metadata={"description": "System prompt for file-level semantic summary generation task."},
    )

    package_summary_system_prompt: str = field (
        default=prompts.PACKAGE_SUMMARY_SYSTEM_PROMPT,
        metadata={"description": "System prompt for package-level semantic summary generation task."},
    )

    code_summary_model: Annotated[str, {"__template_metadata__": {"kind": "llm"}}] = field(
        default="openai/gpt-4o",
        metadata={
            "description": "Language model to use for generating file / package level semantic summaries. Should be in the form: provider/model-name."
        },
    )

    package_localization_system_prompt: str = field (
        default=prompts.PACKAGE_LOCALIZATION_SYSTEM_PROMPT,
        metadata={"description": "System prompt for package-level localization task."},
    )

    file_localization_system_prompt: str = field (
        default=prompts.FILE_LOCALIZATION_SYSTEM_PROMPT,
        metadata={"description": "System prompt for file-level localization task."},
    )

    localization_model: Annotated[
        str,
        {"__template_metadata__": {"kind": "llm"}}
    ] = field(
        default="openai/gpt-4o",
        metadata={
            "description": "Language model to use for file / package level localization. Should be in the form: provider/model-name."
        },
    )

    code_suggestions_system_prompt: str = field (
        default=prompts.CODE_SUGGESTION_SYSTEM_PROMPT,
        metadata={"description": "System prompt for code change suggestions task."},
    )

    code_suggestions_model: Annotated[
        str,
        {"__template_metadata__": {"kind": "llm"}}
    ] = field(
        default="openai/gpt-4o",
        metadata={
            "description": "Language model to use for code change suggestions task. Should be in the form: provider/model-name."
        },
    )

    gh_token: str = field(
        default=os.getenv('GH_TOKEN'),
        metadata={"description": "GitHub token for the se-agent to use."},
    )

    @classmethod
    def from_runnable_config(
        cls: Type[T], config: Optional[RunnableConfig] = None
    ) -> T:
        """Create an Configuration instance from a RunnableConfig object.

        Args:
            cls (Type[T]): The class itself.
            config (Optional[RunnableConfig]): The configuration object to use.

        Returns:
            T: An instance of Configuration with the specified configuration.
        """
        config = ensure_config(config)
        configurable = config.get("configurable") or {}
        _fields = {f.name for f in fields(cls) if f.init}
        return cls(**{k: v for k, v in configurable.items() if k in _fields})

T = TypeVar("T", bound=Configuration)