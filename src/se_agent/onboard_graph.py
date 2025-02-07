from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableConfig
from langgraph.types import Send
from langgraph.graph import (
    StateGraph,
    START
)
from se_agent.state import (
    FilepathState,
    FileSummary,
    OnboardState
)
from se_agent.config import Configuration
from se_agent.utils import (
    extract_code_block_content,
    get_all_files,
    get_file_content,
    load_chat_model,
    split_github_url,
)


async def get_filepaths(_: OnboardState, *, config: RunnableConfig):
    configuration = Configuration.from_runnable_config(config)
    base_url, owner, repo = split_github_url(configuration.gh_repository_url)
    api_url = "https://api.github.com" if base_url == "https://github.com" else f"{base_url}/api/v3"
    headers = {"Authorization": f"Bearer {configuration.gh_token}"}

    filepaths = get_all_files(
        api_url,
        headers,
        owner,
        repo, 
        path=configuration.gh_src_folder, 
        branch=configuration.gh_repository_branch
    )

    return {"filepaths": filepaths}


def continue_to_save_file_summaries(state: OnboardState, *, config: RunnableConfig):
    """
    Here we define the logic to map out over the filepaths.
    We will return a list of `Send` objects
    Each `Send` object consists of the name of a node in the graph
    as well as the state to send to that node.
    """
    return [Send("generate_file_summary", FilepathState(filepath=filepath)) for filepath in state.filepaths]


async def generate_file_summary(state: FilepathState, *, config: RunnableConfig):
    # Get the file content
    configuration = Configuration.from_runnable_config(config)
    base_url, owner, repo = split_github_url(configuration.gh_repository_url)
    api_url = "https://api.github.com" if base_url == "https://github.com" else f"{base_url}/api/v3"
    headers = {"Authorization": f"Bearer {configuration.gh_token}"}
    file_type = state.filepath.split(".")[-1]
    file_content = get_file_content(
        api_url,
        headers,
        owner,
        repo, 
        filepath=state.filepath,
        branch=configuration.gh_repository_branch
    )

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
    return {"file_summaries": [FileSummary(filepath=state.filepath, summary=extract_code_block_content(response.content))]}


async def save_file_summaries(state: OnboardState, *, config: RunnableConfig):
    pass


# Initialize the state with default values
builder = StateGraph(state_schema=OnboardState, config_schema=Configuration)

builder.add_node(get_filepaths)
builder.add_node(generate_file_summary)
builder.add_node(save_file_summaries)

builder.add_edge(START, "get_filepaths")
builder.add_conditional_edges("get_filepaths", continue_to_save_file_summaries, ["generate_file_summary"])
builder.add_edge("generate_file_summary", "save_file_summaries")

graph = builder.compile()

graph.name = "OnboardGraph"