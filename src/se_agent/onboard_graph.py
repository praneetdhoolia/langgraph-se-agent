from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableConfig
from langgraph.graph import START, END, StateGraph
from langgraph.types import Send

from se_agent.config import Configuration
from se_agent.state import (
    FileSummaryError,
    FilepathState,
    FileSummary,
    OnboardInputState,
    OnboardState,
    PackageState,
    PackageSummary,
    PackageSummaryError,
)
from se_agent.utils.utils_misc import (
    extract_code_block_content,
    group_by_top_level_packages,
    load_chat_model,
    shift_markdown_headings
)
from se_agent.utils.utils_git_local import (
    clone_repository,
    get_file_content_from_local,
    get_filepaths_from_local,
    remove_cloned_repository
)
from se_agent.utils.utils_git_api import (
    get_file_content_from_github
)
from se_agent.store import get_store


def decide_onboarding_or_update(state: OnboardState, *, config: RunnableConfig) -> list[str]:
    """Decide whether to fetch filepaths (repo-onboard) or handle updates (repo-update).

    Args:
        state (OnboardState): The current onboarding state, including the event type.
        config (RunnableConfig): The runtime configuration (unused here, but part of the signature).

    Returns:
        list[str]: A single-item list containing either "get_filepaths" or "handle_update".

    Raises:
        ValueError: If the event type is neither "repo-onboard" nor "repo-update".
    """
    event_type = state.event.event_type
    
    if event_type == "repo-onboard":
        return ["get_filepaths"]
    elif event_type == "repo-update":
        return ["handle_update"]
    else:
        raise ValueError(f"Invalid event type: {event_type}")


async def get_filepaths(state: OnboardState, *, config: RunnableConfig) -> dict:
    """Clone the repository locally (if HTTPS) and fetch file paths from the local file system.

    Args:
        state (OnboardState): The current state with repository details (url, branch, src_folder).
        config (RunnableConfig): The runtime configuration.

    Returns:
        dict: A dictionary containing:
            - "filepaths": A list of filepaths discovered locally.
            - "repo_dir": The local directory where the repo is cloned.
    """
    configuration = Configuration.from_runnable_config(config)

    repo_url = state.repo.url
    src_folder = state.repo.src_folder
    branch = state.repo.branch
    commit_hash = state.repo.commit_hash
    token = configuration.gh_token

    if repo_url.startswith("file://"):
        # 1. Convert the file:// path into a local file system path.
        local_path = repo_url.replace("file://", "")
        # 2. We treat local_path as our repo_dir
        repo_dir = local_path

    elif repo_url.startswith("https://"):
        # Insert the token for private repo access
        repo_url = repo_url.replace("https://", f"https://{token}@")
        repo_dir = clone_repository(repo_url, branch, commit_hash)
    
    filepaths = get_filepaths_from_local(repo_dir, src_folder)

    return {
        "filepaths": filepaths,
        "repo_dir": repo_dir
    }


async def handle_update(state: OnboardState, *, config: RunnableConfig) -> dict:
    """
    Handle repository updates by delegating persistence operations to the store.
    This includes:
      1. Fetching the repository record.
      2. Processing deleted files: deleting file records, removing orphan packages,
         and updating package timestamps.
      3. Updating the repository's last_modified_at timestamp.
      4. Returning modified filepaths for further processing.

    Args:
        state (OnboardState): The current onboarding state, including details on deleted files.
        config (RunnableConfig): The runtime configuration.

    Returns:
        dict: A dictionary containing:
            - "repo_id" (int): The ID of the affected repository.
            - "filepaths" (list[str]): The modified file paths.
            - "packages_impacted" (set[int]): Package IDs that were impacted by file deletions.
              If no repo was found, returns an empty "filepaths" list.
    """
    # Get our store instance (we assume "sqlite" for now; the db_path can come from config or be hardcoded)
    store = get_store("sqlite", db_path="store.db")

    repo_record = store.get_repo(state.repo.url, state.repo.src_folder, state.repo.branch)
    if repo_record is None:
        return {"filepaths": []}

    repo_id = repo_record.repo_id
    deleted_files = state.event.meta_data.deleted
    packages_impacted = set()

    if deleted_files:
        packages_with_deletions = store.get_package_ids_for_files(repo_id, deleted_files)
        store.delete_files(repo_id, deleted_files)
        valid_package_ids = store.get_valid_package_ids(repo_id)
        # Remove orphan packages (packages without any remaining files)
        store.delete_orphan_packages(repo_id, valid_package_ids)
        packages_impacted = packages_with_deletions.intersection(valid_package_ids)
        for pkg_id in packages_impacted:
            store.update_package_last_modified(repo_id, pkg_id)

    store.update_repo_last_modified(repo_id)

    return {
        "repo_id": repo_id,
        "filepaths": state.event.meta_data.modified,
        "packages_impacted": packages_impacted
    }


def continue_to_save_file_summaries(state: OnboardState, *, config: RunnableConfig):
    """Direct the flow to generate file summaries or skip to saving package summaries.

    If no filepaths remain (but some packages were impacted), we skip directly to
    generating package summaries. Otherwise, we send a message to generate file summaries.

    Args:
        state (OnboardState): The current onboarding state.
        config (RunnableConfig): The runtime configuration (unused here, but part of the signature).

    Returns:
        list[Send] or dict: If filepaths are present, returns a list of instructions (Send)
            to generate summaries. If no filepaths remain but we have impacted packages,
            we call `continue_to_save_package_summaries`.
    """
    # For 'repo-update', with only deletes let's call continue_to_save_package_summaries.
    if not state.filepaths and state.packages_impacted:
        return continue_to_save_package_summaries(state, config=config)

    # Map out to generate summaries for each file.
    return [
        Send (
            "generate_file_summary", 
            FilepathState(filepath=filepath, repo_dir=state.repo_dir, repo=state.repo, event=state.event)
        ) 
        for filepath in state.filepaths
    ]


async def generate_file_summary(state: FilepathState, *, config: RunnableConfig) -> dict:
    """Generate a semantic summary for a single file with error handling

    This function fetches the file content (either from a local clone or via GitHub API
    for an update event), then uses a language model to create a concise summary.
    In case of an exception, an empty summary is returned along with a
    FileSummaryError recording the error.

    Args:
        state (FilepathState): Contains the filepath, repo directory, and repo event details.
        config (RunnableConfig): The runtime configuration.

    Returns:
        dict: A dictionary with key "file_summaries", which is a list of FileSummary objects.
              and in case of errors "file_summary_errors" which is a list of 
              FileSummaryError objects.
    """
    configuration = Configuration.from_runnable_config(config)
    
    file_type = state.filepath.split(".")[-1]
    # Decide if we are in 'onboard' or 'update' mode
    event_type = state.event.event_type

    try:
        if event_type == "repo-update":
            # We do NOT have a local clone => fetch via GitHub API
            file_content = get_file_content_from_github(
                state.repo.url,
                state.filepath,
                configuration.gh_token,
                state.repo.branch,
                state.repo.commit_hash
            )
        else:
            # Default is 'repo-onboard': we have a local clone
            file_content = get_file_content_from_local(state.repo_dir, state.filepath)

        if file_content is None or file_content.strip() == "":
            return {"file_summaries": []}

        # Generate the file summary
        template = ChatPromptTemplate.from_messages([
            ("human", configuration.file_summary_system_prompt),
        ])
        model = load_chat_model(configuration.code_summary_model)
        context = await template.ainvoke({
            "file_path": state.filepath,
            "file_type": file_type,
            "file_content": file_content
        }, config)
        response = await model.ainvoke(context, config)
        
        return {
            "file_summaries": [FileSummary(filepath=state.filepath, summary=extract_code_block_content(response.content))]
        }
    except Exception as e:
        error_msg = str(e)
        # Record an empty summary and the error details
        empty_summary = FileSummary(filepath=state.filepath, summary="")
        file_error = FileSummaryError(filepath=state.filepath, error=error_msg)
        return {
            "file_summaries": [empty_summary],
            "file_summary_errors": [file_error]
        }

async def save_file_summaries(state: OnboardState, *, config: RunnableConfig) -> dict:
    """
    Save generated file summaries using the store interface. This method:
      1. Fetches or inserts the repository record.
      2. Groups filepaths by their top-level package.
      3. For each package, inserts or updates file records.

    Args:
        state (OnboardState): Contains `file_summaries` to be saved, along with filepaths and repo info.
        config (RunnableConfig): The runtime configuration.

    Returns:
        dict: A dictionary containing:
            - "repo_id" (int): The repository ID for the current repo.
            - "packages_impacted" (list[int]): A list of packages impacted by the file changes.
            - "filepaths" and "file_summaries" set to "delete" to clear them from the state.
    """
    store = get_store("sqlite", db_path="store.db")

    repo_record = store.get_repo(state.repo.url, state.repo.src_folder, state.repo.branch)
    if repo_record is None:
        repo_data = {
            "url": state.repo.url,
            "src_path": state.repo.src_folder,
            "branch": state.repo.branch
        }
        repo_id = store.insert_repo(repo_data)
    else:
        repo_id = repo_record.repo_id
        store.update_repo_last_modified(repo_id)

    pkg_dict = group_by_top_level_packages(state.filepaths, src_folder=state.repo.src_folder)
    packages_impacted = []

    for pkg_name, file_list in pkg_dict.items():
        package_record = store.get_package(repo_id, pkg_name)
        if package_record is None:
            package_id = store.insert_package(repo_id, pkg_name)
        else:
            package_id = package_record.package_id
        packages_impacted.append(package_id)

        for fsum in state.file_summaries:
            if fsum.filepath in file_list:
                store.insert_or_update_file(repo_id, package_id, fsum.filepath, fsum.summary)

    return {
        "repo_id": repo_id,
        "packages_impacted": packages_impacted,
        "filepaths": "delete",
        "file_summaries": "delete"
    }


def continue_to_save_package_summaries(state: OnboardState, *, config: RunnableConfig):
    """Create instructions (Send) to generate package summaries for each impacted package.

    Args:
        state (OnboardState): The current onboarding state containing impacted packages.
        config (RunnableConfig): The runtime configuration (unused here).

    Returns:
        list[Send]: A list of messages instructing the system to generate summaries for each package.
    """
    return [
        Send(
            "generate_package_summary",
            PackageState(package_id=package_id, repo_id=state.repo_id)
        ) 
        for package_id in state.packages_impacted
    ]


async def generate_package_summary(state: PackageState, *, config: RunnableConfig) -> dict:
    """Generate a summary for a single package by aggregating file summaries (with error handling).

    1. Fetch the file summaries for (package_id, repo_id).
    2. Use a language model to create a consolidated package summary.
    3. In case of an error, return an empty summary along with a PackageSummaryError.

    Args:
        state (PackageState): Contains the package ID and repo ID.
        config (RunnableConfig): The runtime configuration.

    Returns:
        dict: A dictionary with "package_summaries" as a list containing one PackageSummary object.
    """
    try:
        store = get_store("sqlite", db_path="store.db")
        file_summaries_list = store.get_file_summaries_for_package(state.repo_id, state.package_id)
        # Fetch package data to obtain the package name.
        packages = store.fetch_package_data(state.repo_id)
        package_name = next((pkg.package_name for pkg in packages if pkg.package_id == state.package_id), "")
        
        file_summaries = []
        for file_path, summary in file_summaries_list:
            file_summaries.append(f"# {file_path}\n{shift_markdown_headings(summary, increment=1)}")

        configuration = Configuration.from_runnable_config(config)
        template = ChatPromptTemplate.from_messages([
            ("human", configuration.package_summary_system_prompt),
        ])
        model = load_chat_model(configuration.code_summary_model)
        context = await template.ainvoke({
            "package_name": package_name,
            "file_summaries": "\n\n".join(file_summaries)
        }, config)
        response = await model.ainvoke(context, config)
        
        return {
            "package_summaries": [
                PackageSummary(
                    package_id=state.package_id,
                    summary=extract_code_block_content(response.content)
                )
            ]
        }
    except Exception as e:
        error_msg = str(e)
        # In case of error, record an empty summary and the error details
        empty_summary = PackageSummary(package_id=state.package_id, summary="")
        package_error = PackageSummaryError(
            package_id=state.package_id,
            package_name=package_name if 'package_name' in locals() else "",
            error=error_msg
        )
        return {
            "package_summaries": [empty_summary],
            "package_summary_errors": [package_error]
        }


async def save_package_summaries(state: OnboardState, *, config: RunnableConfig) -> dict:
    """Save package summaries to the SQLite database.

    For each package summary, update the `summary` column in the packages table.

    Args:
        state (OnboardState): Contains the newly generated "package_summaries" and the current repo_id.
        config (RunnableConfig): The runtime configuration.

    Returns:
        dict: A dictionary with "packages_impacted" and "package_summaries" set to "delete" to clear them from the state.
    """
    store = get_store("sqlite", db_path="store.db")

    for psum in state.package_summaries:
        store.update_package_summary(state.repo_id, psum.package_id, psum.summary)

    return {
        "packages_impacted": "delete",
        "package_summaries": "delete"
    }


async def cleanup(state: OnboardState, *, config: RunnableConfig) -> dict:
    """Remove the cloned repository from the local file system and clear the event.

    Args:
        state (OnboardState): Contains the repo_dir path to remove.
        config (RunnableConfig): The runtime configuration (unused).

    Returns:
        dict: A dictionary setting `repo_dir` and `event` to `None`.
    """
    if state.repo_dir and not state.repo.url.startswith("file://"):
        remove_cloned_repository(state.repo_dir)
        
    return {
        "repo_dir": None,
        "event": None
    }


# Initialize the state with default values
builder = StateGraph(state_schema=OnboardState, input=OnboardInputState, config_schema=Configuration)

builder.add_node(get_filepaths)
builder.add_node(handle_update)
builder.add_node(generate_file_summary)
builder.add_node(save_file_summaries)
builder.add_node(generate_package_summary)
builder.add_node(save_package_summaries)
builder.add_node(cleanup)

builder.add_conditional_edges(START, decide_onboarding_or_update, ["handle_update", "get_filepaths"])
builder.add_conditional_edges("get_filepaths", continue_to_save_file_summaries, ["generate_file_summary"])
builder.add_conditional_edges("handle_update", continue_to_save_file_summaries, ["generate_file_summary", "generate_package_summary"])
builder.add_edge("generate_file_summary", "save_file_summaries")
builder.add_conditional_edges("save_file_summaries", continue_to_save_package_summaries, ["generate_package_summary"])
builder.add_edge("generate_package_summary", "save_package_summaries")
builder.add_edge("save_package_summaries", "cleanup")
builder.add_edge("cleanup", END)

graph = builder.compile()

graph.name = "OnboardGraph"
