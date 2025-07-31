from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableConfig
from langgraph.graph import START, END, StateGraph
from langgraph.types import Send

from se_agent.config import Configuration
from se_agent.state import (
    FilepathState,
    OnboardInputState,
    VectorOnboardState,
)
from se_agent.utils.utils_misc import (
    load_chat_model,
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
from se_agent.store import get_store, get_vector_store


def decide_onboarding_or_update(state: VectorOnboardState, *, config: RunnableConfig) -> list[str]:
    """Decide whether to fetch filepaths (repo-onboard) or handle updates (repo-update).

    Args:
        state (VectorOnboardState): The current onboarding state, including the event type.
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


async def get_filepaths(state: VectorOnboardState, *, config: RunnableConfig) -> dict:
    """Clone the repository locally (if HTTPS) and fetch file paths from the local file system.

    Args:
        state (VectorOnboardState): The current state with repository details (url, branch, src_folder).
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


async def handle_update(state: VectorOnboardState, *, config: RunnableConfig) -> dict:
    """
    Handle repository updates by delegating persistence operations to the store.
    This includes:
      1. Fetching the repository record.
      2. Processing deleted files: deleting vector embeddings.
      3. Updating the repository's last_modified_at timestamp.
      4. Returning modified filepaths for further processing.

    Args:
        state (VectorOnboardState): The current onboarding state, including details on deleted files.
        config (RunnableConfig): The runtime configuration.

    Returns:
        dict: A dictionary containing:
            - "repo_id" (int): The ID of the affected repository.
            - "filepaths" (list[str]): The modified file paths.
              If no repo was found, returns an empty "filepaths" list.
    """
    # Get our store instance
    store = get_store()
    vector_store = get_vector_store()

    repo_record = store.get_repo(state.repo.url, state.repo.src_folder, state.repo.branch)
    if repo_record is None:
        return {"filepaths": []}

    repo_id = repo_record.repo_id
    deleted_files = state.event.meta_data.deleted

    if deleted_files:
        # Delete vector embeddings for the deleted files
        vector_store.delete_vector_embeddings(repo_id, deleted_files)

    return {
        "repo_id": repo_id,
        "filepaths": state.event.meta_data.modified
    }


def continue_to_embed_files(state: VectorOnboardState, *, config: RunnableConfig):
    """Direct the flow to create vector embeddings for files.

    Args:
        state (VectorOnboardState): The current onboarding state.
        config (RunnableConfig): The runtime configuration (unused here, but part of the signature).

    Returns:
        list[Send]: A list of instructions to create vector embeddings for each file.
    """
    # Map out to create embeddings for each file in parallel
    if not state.filepaths:
        return []

    # Create a single FilepathState with all filepaths
    return [
        Send(
            "create_vector_embedding", 
            FilepathState(
                filepath=state.filepaths,
                repo_dir=state.repo_dir,
                repo=state.repo,
                event=state.event
            )
        )
    ]


async def create_vector_embedding(state: FilepathState, *, config: RunnableConfig) -> dict:
    """Create and store vector embeddings for files with error handling.

    This function:
    1. Fetches the file content (from local clone or GitHub API) for each filepath
    2. Generates a vector embedding for the content
    3. Stores the embedding in the vector store

    Args:
        state (FilepathState): Contains the filepaths, repo directory, and repo event details.
        config (RunnableConfig): The runtime configuration.

    Returns:
        dict: A dictionary with success/failure information for each filepath.
    """
    configuration = Configuration.from_runnable_config(config)
    
    # Get store and vector store instances
    store = get_store()
    vector_store = get_vector_store()
    
    # Decide if we are in 'onboard' or 'update' mode
    event_type = state.event.event_type
    configuration.repo_id=state.repo.commit_hash
    repo_id = configuration.repo_id
    # Get repository ID (create if doesn't exist)
    # repo_record = store.get_repo(state.repo.url, state.repo.src_folder, state.repo.branch)
    # if repo_record is None:
    #     repo_data = {
    #         "url": state.repo.url,
    #         "src_path": state.repo.src_folder,
    #         "branch": state.repo.branch
    #     }
    #     repo_id = store.insert_repo(repo_data)
    # else:
    #     repo_id = repo_record.repo_id
    
    results = []
    
    # Process each filepath
    for filepath in state.filepath:
        try:
            # Get file content - same logic as in generate_file_summary
            if event_type == "repo-update":
                # We do NOT have a local clone => fetch via GitHub API
                file_content = get_file_content_from_github(
                    state.repo.url,
                    filepath,
                    configuration.gh_token,
                    state.repo.branch,
                    state.repo.commit_hash
                )
            else:
                # Default is 'repo-onboard': we have a local clone
                file_content = get_file_content_from_local(state.repo_dir, filepath)

            if file_content is None or file_content.strip() == "":
                results.append({"success": False, "filepath": filepath, "error": "Empty file content"})
                continue

            # Create and store the vector embedding
            vector_store.store_code_embedding(
                repo_id=repo_id,
                filepath=filepath,
                code_content=file_content
            )
            
            results.append({"success": True, "filepath": filepath})
        except Exception as e:
            error_msg = str(e)
            results.append({"success": False, "filepath": filepath, "error": error_msg})
    
    return {"results": results}


async def cleanup(state: VectorOnboardState, *, config: RunnableConfig) -> dict:
    """Remove the cloned repository from the local file system and clear the event.

    Args:
        state (VectorOnboardState): Contains the repo_dir path to remove.
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
builder = StateGraph(state_schema=VectorOnboardState, input=OnboardInputState, config_schema=Configuration)

builder.add_node(get_filepaths)
builder.add_node(handle_update)
builder.add_node(create_vector_embedding)
builder.add_node(cleanup)

builder.add_conditional_edges(START, decide_onboarding_or_update, ["handle_update", "get_filepaths"])
builder.add_conditional_edges("get_filepaths", continue_to_embed_files, ["create_vector_embedding"])
builder.add_conditional_edges("handle_update", continue_to_embed_files, ["create_vector_embedding"])
builder.add_edge("create_vector_embedding", "cleanup")
builder.add_edge("cleanup", END)

graph = builder.compile()

graph.name = "VectorOnboardGraph"
