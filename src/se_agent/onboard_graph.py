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
    FilepathState,
    FileSummary,
    OnboardState,
    Package,
    PackageState,
    PackageSummary,
)
from se_agent.config import Configuration
from se_agent.utils import (
    extract_code_block_content,
    get_all_files,
    get_file_content,
    group_by_top_level_packages,
    load_chat_model,
    shift_markdown_headings,
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
    """
    Saves file_summaries to a SQLite database (store.db).
    Also creates the database schema if it's not present.
    """

    configuration = Configuration.from_runnable_config(config)
    conn = sqlite3.connect("store.db")
    conn.execute("PRAGMA foreign_keys = ON;")

    # Create tables if they do not exist ---
    conn.execute("""CREATE TABLE IF NOT EXISTS repositories (
        repo_id     INTEGER PRIMARY KEY AUTOINCREMENT,
        url         TEXT NOT NULL,
        src_path    TEXT NOT NULL,
        branch      TEXT NOT NULL
    );
    """)
    conn.execute("""CREATE TABLE IF NOT EXISTS packages (
        package_id   INTEGER PRIMARY KEY AUTOINCREMENT,
        repo_id      INTEGER NOT NULL,
        package_name TEXT NOT NULL,
        summary      TEXT,
        FOREIGN KEY (repo_id) REFERENCES repositories(repo_id)
    );
    """)
    conn.execute("""CREATE TABLE IF NOT EXISTS files (
        file_id     INTEGER PRIMARY KEY AUTOINCREMENT,
        repo_id     INTEGER NOT NULL,
        package_id  INTEGER NOT NULL,
        file_path   TEXT NOT NULL,
        summary     TEXT,
        FOREIGN KEY (repo_id) REFERENCES repositories(repo_id),
        FOREIGN KEY (package_id) REFERENCES packages(package_id)
    );
    """)

    # Insert (or fetch) the repository row ---
    base_url, owner, repo = split_github_url(configuration.gh_repository_url)
    cursor = conn.execute("""SELECT repo_id
        FROM repositories
        WHERE url = ? AND src_path = ? AND branch = ?
    """, (configuration.gh_repository_url,
          configuration.gh_src_folder,
          configuration.gh_repository_branch))
    row = cursor.fetchone()

    if row is None:
        # Insert a new repository
        cursor = conn.execute("""INSERT INTO repositories (url, src_path, branch)
            VALUES (?, ?, ?)
        """, (configuration.gh_repository_url,
              configuration.gh_src_folder,
              configuration.gh_repository_branch))
        repo_id = cursor.lastrowid
    else:
        repo_id = row[0]

    # Group filepaths by top-level packages {top_level_package: [filepaths]}
    pkg_dict = group_by_top_level_packages(state.filepaths, src_folder=configuration.gh_src_folder)

    # We'll store {pkg_name: Package} in the state for quick lookup
    package_name_index = {}

    # For each package (key in pkg_dict), insert or fetch the package row
    for pkg_name, file_list in pkg_dict.items():
        # Check if package already exists
        cursor = conn.execute("""SELECT package_id
            FROM packages
            WHERE repo_id = ? AND package_name = ?
        """, (repo_id, pkg_name))
        pkg_row = cursor.fetchone()

        if pkg_row is None:
            # Insert new row with summary = NULL
            cursor = conn.execute("""INSERT INTO packages (repo_id, package_name, summary)
                VALUES (?, ?, NULL)
            """, (repo_id, pkg_name))
            package_id = cursor.lastrowid
        else:
            package_id = pkg_row[0]

        package_name_index[pkg_name] = Package(package_id=package_id, name=pkg_name, filepaths=file_list)

        # Now insert the file summaries that belong to this package
        # We have the file_list of paths that belong to pkg_name.
        # We'll find matching summaries in state.file_summaries.
        for fsum in state.file_summaries:
            if fsum.filepath in file_list:
                # Insert the file row using the known package_id
                conn.execute("""INSERT INTO files (repo_id, package_id, file_path, summary)
                    VALUES (?, ?, ?, ?)
                """, (repo_id, package_id, fsum.filepath, fsum.summary))

    conn.commit()
    conn.close()

    return {
        "repo_id": repo_id,
        "package_name_index": package_name_index
    }


def continue_to_save_package_summaries(state: OnboardState, *, config: RunnableConfig):
    """ Map out to generate summaries for each package """

    def get_file_summaries(filepaths):
        return [fsum for fsum in state.file_summaries if fsum.filepath in filepaths]

    return [
        Send(
            "generate_package_summary",
            PackageState(package_name=package_name, file_summaries=get_file_summaries(package.filepaths))
        ) 
        for package_name, package in state.package_name_index.items()
    ]


async def generate_package_summary(state: PackageState, *, config: RunnableConfig):
    configuration = Configuration.from_runnable_config(config)

    package_name = state.package_name
    
    file_summaries = []
    for fsum in state.file_summaries:
        file_summaries.append(f"# {fsum.filepath}\n{shift_markdown_headings(fsum.summary, increment=1)}")

    # Generate the package summary
    template = ChatPromptTemplate.from_messages([
        ("human", configuration.package_summary_system_prompt),
    ])
    model = load_chat_model(configuration.code_summary_model)
    context = await template.ainvoke({
        "package_name": package_name,
        "file_summaries": "\n\n".join(file_summaries)
    }, config)
    response = await model.ainvoke(context, config)
    return {"package_summaries": [PackageSummary(package_name=package_name, summary=extract_code_block_content(response.content))]}


async def save_package_summaries(state: OnboardState, *, config: RunnableConfig):
    """ Saves package_summaries to a SQLite database (store.db). """

    configuration = Configuration.from_runnable_config(config)

    # Connect to (or create) the store.db file
    # If it doesn't exist, sqlite3.connect(...) will create it automatically.
    conn = sqlite3.connect("store.db")
    conn.execute("PRAGMA foreign_keys = ON;")

    # let's upsert packages
    for psum in state.package_summaries:
        package_id = state.package_name_index[psum.package_name].package_id
        conn.execute("""UPDATE packages
            SET summary = ?
            WHERE repo_id = ? AND package_id = ?
        """, (psum.summary, state.repo_id, package_id))

    conn.commit()
    conn.close()



# Initialize the state with default values
builder = StateGraph(state_schema=OnboardState, config_schema=Configuration)

builder.add_node(get_filepaths)
builder.add_node(generate_file_summary)
builder.add_node(save_file_summaries)
builder.add_node(generate_package_summary)
builder.add_node(save_package_summaries)

builder.add_edge(START, "get_filepaths")
builder.add_conditional_edges("get_filepaths", continue_to_save_file_summaries, ["generate_file_summary"])
builder.add_edge("generate_file_summary", "save_file_summaries")
builder.add_conditional_edges("save_file_summaries", continue_to_save_package_summaries, ["generate_package_summary"])
builder.add_edge("generate_package_summary", "save_package_summaries")
builder.add_edge("save_package_summaries", END)

graph = builder.compile()

graph.name = "OnboardGraph"