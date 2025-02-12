import sqlite3
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableConfig
from langgraph.types import Send
from langgraph.graph import (
    StateGraph,
    START,
    END
)
from se_agent.state import (
    FileContent,
    FilepathState,
    InputState,
    Package,
    PackageSuggestions,
    package_suggestions_format_instuctions,
    FileSuggestions,
    file_suggestions_format_instuctions,
    State,
)
from se_agent.config import Configuration
from se_agent.utils import (
    get_file_content,
    load_chat_model,
    shift_markdown_headings,
)


async def localize_packages(state: State, *, config: RunnableConfig):
    configuration = Configuration.from_runnable_config(config)

    package_summaries = []
    package_name_index = {}

    # Connect to database
    conn = sqlite3.connect("store.db")
    
    # --- 1) Fetch repository id ---
    cursor = conn.execute("""SELECT repo_id
        FROM repositories
        WHERE url = ? AND src_path = ? AND branch = ?
    """, (state.repo.url,
          state.repo.src_folder,
          state.repo.branch))
    row = cursor.fetchone()
    repo_id = row[0]

    # --- 2) Fetch package ids, names and summaries for all packages with repo_id ---
    cursor = conn.execute("""SELECT package_id, package_name, summary
        FROM packages
        WHERE repo_id = ?
    """, (repo_id,))
    rows = cursor.fetchall()

    for row in rows:
        package_id, package_name, summary = row
        package_name_index[package_name] = Package(package_id=package_id, name=package_name)
        package_summaries.append(summary)

    conn.close()

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
    package_suggestions = await model.with_structured_output(PackageSuggestions).ainvoke(context, config)

    return {
        "repo_id": repo_id,
        "package_name_index": package_name_index,
        "package_suggestions": package_suggestions
    }

async def localize_files(state: State, *, config: RunnableConfig):
    configuration = Configuration.from_runnable_config(config)

    file_summaries = []
    
    # Connect to database
    conn = sqlite3.connect("store.db")

    # from state.package_name_index get the package_id for each package_name in state.package_suggestions
    package_ids = [state.package_name_index[pkg_name].package_id for pkg_name in state.package_suggestions.packages]
    
    # --- Fetch file paths and summaries for all files with repo_id, and any of the package_ids ---
    cursor = conn.execute("""SELECT file_path, summary
        FROM files
        WHERE repo_id = ? AND package_id IN ({})
    """.format(",".join("?"*len(package_ids))), (state.repo_id, *package_ids))

    rows = cursor.fetchall()
    for row in rows:
        filepath, summary = row
        file_summaries.append(summary)
        file_summaries.append(f"# {filepath}\n{shift_markdown_headings(summary, increment=1)}")

    conn.close()

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
    file_suggestions = await model.with_structured_output(FileSuggestions).ainvoke(context, config)

    return {
        "file_suggestions": file_suggestions
    }

def continue_to_suggest_solution(state: State, *, config: RunnableConfig):
    """ Map out to generate summaries for each package """

    return [
        Send(
            "fetch_file_content",
            FilepathState(filepath=file_suggestion.filepath, repo=state.repo),
        ) 
        for file_suggestion in state.file_suggestions.files
    ]

async def fetch_file_content(state: FilepathState, *, config: RunnableConfig):
    configuration = Configuration.from_runnable_config(config)
    file_content = get_file_content(state.repo.url, state.filepath, configuration.gh_token, state.repo.branch)
    return {"file_contents": [FileContent(filepath=state.filepath, content=file_content)]}

async def suggest_solution(state: State, *, config: RunnableConfig):
    configuration = Configuration.from_runnable_config(config)

    code_files = []
    for file_suggestion in state.file_suggestions.files:
        filepath = file_suggestion.filepath
        rationale = file_suggestion.rationale
        extn = file_suggestion.filepath.split(".")[-1]
        content = next((
            file_content.content
            for file_content in state.file_contents
            if file_content.filepath == file_suggestion.filepath
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

    return {"messages": [response]}


# Initialize the state with default values
builder = StateGraph(state_schema=State, input=InputState, config_schema=Configuration)

builder.add_node(localize_packages)
builder.add_node(localize_files)
builder.add_node(fetch_file_content)
builder.add_node(suggest_solution)

builder.add_edge(START, "localize_packages")
builder.add_edge("localize_packages", "localize_files")
builder.add_conditional_edges("localize_files", continue_to_suggest_solution, ["fetch_file_content"])
builder.add_edge("fetch_file_content", "suggest_solution")
builder.add_edge("suggest_solution", END)

graph = builder.compile()

graph.name = "AssistGraph"