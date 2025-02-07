import sqlite3
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableConfig
from langgraph.graph import (
    StateGraph,
    START
)
from se_agent.state import (
    Package,
    PackageSuggestions,
    package_suggestions_format_instuctions,
    State,
)
from se_agent.config import Configuration
from se_agent.utils import (
    load_chat_model
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
    """, (configuration.gh_repository_url,
          configuration.gh_src_folder,
          configuration.gh_repository_branch))
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


# Initialize the state with default values
builder = StateGraph(state_schema=State, config_schema=Configuration)

builder.add_node(localize_packages)

builder.add_edge(START, "localize_packages")

graph = builder.compile()

graph.name = "AssistGraph"