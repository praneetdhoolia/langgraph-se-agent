from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from se_agent.config import Configuration
from se_agent.state import (
    FileContent,
    FilepathState,
    FileSuggestions,
    InputState,
    Package,
    PackageSuggestions,
    State,
    file_suggestions_format_instuctions,
    package_suggestions_format_instuctions,
)
from se_agent.utils.utils_misc import (
    load_chat_model,
    shift_markdown_headings
)
from se_agent.utils.utils_git_api import (
    get_file_content_from_github
)
from se_agent.utils.utils_git_local import (
    get_file_content_from_local
)
from se_agent.store import get_store


async def localize_packages(state: State, *, config: RunnableConfig) -> dict:
    """Localize package summaries by fetching existing packages from the database and prompting an LLM.

    This function:
    1. Retrieves the repository ID based on state.repo.
    2. Fetches all packages for that repository.
    3. Uses a language model to generate suggestions on which packages are relevant to the conversation

    Args:
        state (State): The current state, including messages and repository details.
        config (RunnableConfig): The runtime configuration.

    Returns:
        dict: relevant properties to add to the state. Containing:
            - "repo_id" (int): The repository ID.
            - "package_name_index" (dict[str, Package]): A mapping of package_name to Package objects.
            - "package_suggestions" (PackageSuggestions): Suggestions about packages relevant to the conversation.
    """
    configuration = Configuration.from_runnable_config(config)

    package_summaries = []
    package_name_index = {}

    # Connect to database
    store = get_store("sqlite", db_path="store.db")
    
    # --- 1) Fetch repository id ---
    repo_record = store.get_repo(state.repo.url, state.repo.src_folder, state.repo.branch)
    if repo_record is None:
        raise Exception("Repository not onboarded.")
    repo_id = repo_record.repo_id


    # --- 2) Fetch package ids, names, and summaries for all packages with repo_id ---
    packages = store.fetch_package_data(repo_id)
    for pkg in packages:
        package_name_index[pkg.package_name] = Package(
            package_id=pkg.package_id,
            name=pkg.package_name
        )
        package_summaries.append(pkg.summary if pkg.summary else "")

    # --- 3) Use LLM to suggest relevant packages ---
    # Prepare a localization prompt
    template = ChatPromptTemplate.from_messages([
        ("system", configuration.package_localization_system_prompt),
        ("placeholder", "{messages}"),
    ])
    model = load_chat_model(configuration.localization_model)
    context = await template.ainvoke({
        "messages": state.messages,
        "package_summaries": "\n\n".join(package_summaries),
        "format_instructions": package_suggestions_format_instuctions,
    }, config)

    # Parse structured LLM output as PackageSuggestions
    package_suggestions = await model.with_structured_output(PackageSuggestions).ainvoke(context, config)

    return {
        "repo_id": repo_id,
        "package_name_index": package_name_index,
        "package_suggestions": package_suggestions
    }


async def localize_files(state: State, *, config: RunnableConfig) -> dict:
    """Localize which files in the relevant packages are relevant to the conversation.

    This function:
    1. Fetches file paths and summaries for relevant packages suggested earlier.
    2. Uses a language model to determine which files need changes.

    Args:
        state (State): The current state, containing suggested packages and repository details.
        config (RunnableConfig): The runtime configuration.

    Returns:
        file_suggestions (FileSuggestions): relevant files to add to state.
    """
    configuration = Configuration.from_runnable_config(config)
    file_summaries = []

    # Get the store instance
    store = get_store("sqlite", db_path="store.db")
    # Gather package_ids from the suggested packages
    package_ids = [
        state.package_name_index[pkg.package_name].package_id
        for pkg in state.package_suggestions.packages
    ]

    # --- For each suggested package, fetch file summaries ---
    for pkg_id in package_ids:
        summaries = store.get_file_summaries_for_package(state.repo_id, pkg_id)
        for file_path, summary in summaries:
            file_summaries.append(summary)
            file_summaries.append(f"# {file_path}\n{shift_markdown_headings(summary, increment=1)}")

    # Prepare an LLM prompt
    template = ChatPromptTemplate.from_messages([
        ("system", configuration.file_localization_system_prompt),
        ("placeholder", "{messages}"),
    ])
    model = load_chat_model(configuration.localization_model)
    context = await template.ainvoke({
        "messages": state.messages,
        "file_summaries": "\n\n".join(file_summaries),
        "format_instructions": file_suggestions_format_instuctions,
    }, config)
    # Parse structured LLM output as FileSuggestions
    file_suggestions = await model.with_structured_output(FileSuggestions).ainvoke(context, config)

    return {
        "file_suggestions": file_suggestions
    }


def continue_to_suggest_solution(state: State, *, config: RunnableConfig) -> list[Send]:
    """Maps out to fetch the content of each suggested file.

    Args:
        state (State): Current state containing file suggestions.
        config (RunnableConfig): The runtime configuration (unused here).

    Returns:
        list[Send]: A list of commands to fork in parallel to fetch file contents.
    """
    return [
        Send(
            "fetch_file_content",
            FilepathState(filepath=file_suggestion.filepath, repo=state.repo)
        )
        for file_suggestion in state.file_suggestions.files
    ]


async def fetch_file_content(state: FilepathState, *, config: RunnableConfig) -> dict:
    """Fetch the content of a single file from GitHub.

    Args:
        state (FilepathState): State containing the filepath and repo details.
        config (RunnableConfig): The runtime configuration.

    Returns:
        "file_contents", property to reduce back to the state
    """
    configuration = Configuration.from_runnable_config(config)

    if state.repo.url.startswith("file://"):
        # Use repo_dir if available; otherwise derive the local path by stripping "file://"
        local_repo_dir = state.repo_dir if state.repo_dir else state.repo.url.replace("file://", "")
        file_content = get_file_content_from_local(local_repo_dir, state.filepath)
    else:
        file_content = get_file_content_from_github(
            state.repo.url,
            state.filepath,
            configuration.gh_token,
            state.repo.branch,
            state.repo.commit_hash
        )

    return {
        "file_contents": [
            FileContent(filepath=state.filepath, content=file_content)
        ]
    }


async def suggest_solution(state: State, *, config: RunnableConfig) -> dict:
    """Suggest a solution or improvement for each file by combining code content with user messages.

    This function:
    1. Gathers the content and rationale for each suggested file.
    2. Sends all code sections to a language model for potential improvements.

    Args:
        state (State): Contains file suggestions (filepath and rationale) and the actual file content.
        config (RunnableConfig): The runtime configuration.

    Returns:
        dict: with Agent's suggestions with code enhancements to handle the conversation.
    """
    configuration = Configuration.from_runnable_config(config)

    code_files = []
    for file_suggestion in state.file_suggestions.files:
        filepath = file_suggestion.filepath
        rationale = file_suggestion.rationale
        extn = filepath.split(".")[-1]
        content = next((
            file_content.content
            for file_content in state.file_contents
            if file_content.filepath == filepath
        ), None)
        code_files.append(f"filepath: {filepath}\nrationale: {rationale}\n```{extn}\n{content}\n```")

    template = ChatPromptTemplate.from_messages([
        ("system", configuration.code_suggestions_system_prompt),
        ("placeholder", "{messages}"),
    ])
    model = load_chat_model(configuration.code_suggestions_model)
    context = await template.ainvoke({
        "messages": state.messages,
        "code_files": "\n\n".join(code_files),
    }, config)
    response = await model.ainvoke(context, config)

    return {
        "messages": [response]
    }


async def cleanup(state: State, *, config: RunnableConfig) -> dict:
    """Clean up the current state after localization and solution suggestions.

    This function clears out certain state attributes related to packages and files.

    Args:
        state (State): The current state.
        config (RunnableConfig): The runtime configuration (unused).

    Returns:
        dict: of properties to cleanup from the state.
    """
    return {
        "package_name_index": None,
        "file_contents": "delete"
    }


# Initialize the state with default values
builder = StateGraph(state_schema=State, input=InputState, config_schema=Configuration)

builder.add_node(localize_packages)
builder.add_node(localize_files)
builder.add_node(fetch_file_content)
builder.add_node(suggest_solution)
builder.add_node(cleanup)

builder.add_edge(START, "localize_packages")
builder.add_edge("localize_packages", "localize_files")
builder.add_conditional_edges("localize_files", continue_to_suggest_solution, ["fetch_file_content"])
builder.add_edge("fetch_file_content", "suggest_solution")
builder.add_edge("suggest_solution", "cleanup")
builder.add_edge("cleanup", END)

graph = builder.compile()

graph.name = "AssistGraph"
