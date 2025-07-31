from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from se_agent.config import Configuration
from se_agent.state import (
    FileContent,
    FilepathState,
    InputState,
    State,
    FileSuggestion,
    FileSuggestions,
)
from se_agent.utils.utils_misc import (
    load_chat_model,
)
from se_agent.utils.utils_git_api import (
    get_file_content_from_github
)
from se_agent.utils.utils_git_local import (
    get_file_content_from_local
)
from se_agent.store import get_store, get_vector_store
import os

async def find_relevant_files(state: State, *, config: RunnableConfig) -> dict:
    """Find files relevant to the user query using vector search.
    
    This function:
    1. Extracts the user query from the messages
    2. Uses the vector store to search for similar code
    3. Filters and ranks the results
    
    Args:
        state (State): The current state, including messages and repository details.
        config (RunnableConfig): The runtime configuration.
        
    Returns:
        dict: A dictionary containing:
            - "repo_id" (int): The repository ID
            - "file_suggestions" (FileSuggestions): Structured file suggestions with rationale
    """
    configuration = Configuration.from_runnable_config(config)
    
    # Connect to database and vector store
    store = get_store()
    vector_store = get_vector_store()
    
    # # Get repo ID
    # repo_record = store.get_repo(state.repo.url, state.repo.src_folder, state.repo.branch)
    # if repo_record is None:
    #     raise Exception("Repository not onboarded.")
    configuration.repo_id=state.repo.commit_hash
    repo_id = configuration.repo_id

    # Extract the last user message as the query
    user_query = ""
    for message in reversed(state.messages):
        # Check if the message has a role attribute and if it's a user message
        if hasattr(message, 'role') and message.role.lower() == "user":
            user_query = message.content
            break
        # For dict-like messages
        elif hasattr(message, 'get') and message.get('role', '').lower() == 'user':
            user_query = message.get('content', '')
            break
        # For AnyMessage type (using type-specific access)
        elif hasattr(message, 'type') and message.type.lower() == 'human':
            user_query = message.content
            break
    
    if not user_query:
        raise ValueError("No user query found in messages")
    
    # Search for similar code using vector search
    search_results = vector_store.search_similar_code(repo_id, user_query, limit=5)
    
    # If we don't have enough results, try a more generic search
    if len(search_results) < 3:
        # Try with a more general search by extracting keywords
        model = load_chat_model(configuration.localization_model)
        template = ChatPromptTemplate.from_messages([
            ("system", configuration.vector_search_system_prompt),
            ("user", user_query),
        ])
        response = await model.ainvoke(template, config)
        generic_query = response.content
        
        # Perform the search with the refined query
        additional_results = vector_store.search_similar_code(repo_id, generic_query, limit=5)
        
        # Merge results, avoiding duplicates
        existing_filepaths = {result["filepath"] for result in search_results}
        for result in additional_results:
            if result["filepath"] not in existing_filepaths:
                search_results.append(result)
                existing_filepaths.add(result["filepath"])
    
    # Process the search results to create file suggestions
    file_suggestions = []
    for result in search_results:
        file_suggestions.append(
            FileSuggestion(
                filepath=result["filepath"],
                rationale=f"Similarity score: {result['score']:.2f}. Contains code relevant to the query."
            )
        )
    
    return {
        "repo_id": repo_id,
        "file_suggestions": FileSuggestions(files=file_suggestions)
    }


def continue_to_fetch_files(state: State, *, config: RunnableConfig) -> list[Send]:
    """Maps out to fetch the content of each suggested file.
    
    Args:
        state (State): Current state containing file suggestions.
        config (RunnableConfig): The runtime configuration.
        
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
    """Fetch the content of a single file.
    
    Args:
        state (FilepathState): State containing the filepath and repo details.
        config (RunnableConfig): The runtime configuration.
        
    Returns:
        dict: A dictionary containing file contents to add to the state.
    """
    configuration = Configuration.from_runnable_config(config)

    if state.repo.url.startswith("file://"):
        # Use repo_dir if available; otherwise derive the local path by stripping "file://"
        local_repo_dir = state.repo_dir if state.repo_dir else state.repo.url.replace("file://", "")
            
        if not os.path.exists(local_repo_dir):
            raise FileNotFoundError(f"Local repository directory not found: {local_repo_dir}")
                
        file_path = os.path.join(local_repo_dir, state.filepath)
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")
                
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
    """Suggest a solution or improvement for each file based on vector search results.
    
    This function:
    1. Formats the file contents from vector search
    2. Uses an LLM to generate a solution based on the retrieved files
    
    Args:
        state (State): Contains file suggestions and the actual file content.
        config (RunnableConfig): The runtime configuration.
        
    Returns:
        dict: With Agent's suggestions with code enhancements to handle the user query.
    """
    configuration = Configuration.from_runnable_config(config)
    
    code_files = []
    for file_suggestion in state.file_suggestions.files:
        filepath = file_suggestion.filepath
        rationale = file_suggestion.rationale
        extn = filepath.split(".")[-1] if "." in filepath else ""
        
        content = next((
            file_content.content
            for file_content in state.file_contents
            if file_content.filepath == filepath
        ), None)
        
        if content:
            code_files.append(f"filepath: {filepath}\nrationale: {rationale}\n```{extn}\n{content}\n```")
    
    template = ChatPromptTemplate.from_messages([
        ("system", configuration.vector_solution_system_prompt),
        ("placeholder", "{messages}"),
    ])
    model = load_chat_model(configuration.vector_solution_model)
    context = await template.ainvoke({
        "messages": state.messages,
        "code_files": "\n\n".join(code_files),
    }, config)
    response = await model.ainvoke(context, config)
    
    return {
        "messages": [response]
    }


async def cleanup(state: State, *, config: RunnableConfig) -> dict:
    """Clean up the current state after suggestion.
    
    Args:
        state (State): The current state.
        config (RunnableConfig): The runtime configuration.
        
    Returns:
        dict: Of properties to cleanup from the state.
    """
    return {
        "file_contents": "delete",
        "file_suggestions": None
    }


# Initialize the graph
builder = StateGraph(state_schema=State, input=InputState, config_schema=Configuration)

builder.add_node(find_relevant_files)
builder.add_node(fetch_file_content)
builder.add_node(suggest_solution)
builder.add_node(cleanup)

builder.add_edge(START, "find_relevant_files")
builder.add_conditional_edges("find_relevant_files", continue_to_fetch_files, ["fetch_file_content"])
builder.add_edge("fetch_file_content", "suggest_solution")
builder.add_edge("suggest_solution", "cleanup")
builder.add_edge("cleanup", END)

graph = builder.compile()

graph.name = "VectorAssistGraph" 