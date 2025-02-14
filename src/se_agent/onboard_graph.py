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
    OnboardInputState,
    OnboardState,
    PackageState,
    PackageSummary,
)
from se_agent.config import Configuration
from se_agent.utils import (
    clone_repository,
    extract_code_block_content,
    get_file_content_from_local,
    get_file_content_from_github,
    get_filepaths_from_local,
    group_by_top_level_packages,
    load_chat_model,
    remove_cloned_repository,
    shift_markdown_headings,
)


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
    event_type = state.repo_event.event.event_type
    
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

    repo_url = state.repo_event.repo.url
    src_folder = state.repo_event.repo.src_folder
    branch = state.repo_event.repo.branch
    token = configuration.gh_token

    # If it's an HTTPS URL, insert the token for private repo access
    if repo_url.startswith("https://"):
        repo_url = repo_url.replace("https://", f"https://{token}@")

    repo_dir = clone_repository(repo_url, branch)

    filepaths = get_filepaths_from_local(repo_dir, src_folder)

    return {
        "filepaths": filepaths,
        "repo_dir": repo_dir
    }


async def handle_update(state: OnboardState, *, config: RunnableConfig) -> dict:
    """Handle repository updates by removing deleted files/packages and updating timestamps.

    1. If there are deleted files, remove them and any empty packages from the DB.
    2. Update 'last_modified_at' for:
       - the repository if ANY file has been deleted or modified
       - only those packages that lost files but remain in the DB (i.e., not orphaned).
    3. Return the 'filepaths' = event.modified so we can run the rest of the flow.

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
    if not state.repo_event:
        # No event present, so do nothing special
        return {"filepaths": []}

    # If there's a repo row for this URL, get the repo_id:
    repo_url = state.repo_event.repo.url
    conn = sqlite3.connect("store.db")
    conn.execute("PRAGMA foreign_keys = ON;")

    # 1) Fetch the repository ID
    cursor = conn.execute(
        "SELECT repo_id FROM repositories WHERE url = ?",
        (repo_url,)
    )
    row = cursor.fetchone()
    if not row:
        # Repo not found => do nothing.
        conn.close()
        return {"filepaths": []}

    repo_id = row[0]

    # 2) If we have deleted files, handle them
    deleted_files = state.repo_event.event.meta_data.deleted
    package_ids_with_files_deleted_and_remaining = set()
    if deleted_files:
        # Find package_ids for these files before deleting
        # so we know which packages might need updating.
        cursor = conn.execute(
            f"""
            SELECT package_id
            FROM files
            WHERE repo_id = ?
                AND file_path IN ({','.join(['?']*len(deleted_files))})
            """,
            [repo_id] + deleted_files
        )
        package_ids_with_file_deletes = {r[0] for r in cursor.fetchall()}

        # Delete those files
        conn.execute(
            f"""
            DELETE FROM files
            WHERE repo_id = ?
                AND file_path IN ({','.join(['?']*len(deleted_files))})
            """,
            [repo_id] + deleted_files
        )

        # Delete any orphan packages (no files remain)
        cursor = conn.execute(
            "SELECT DISTINCT package_id FROM files WHERE repo_id = ?",
            (repo_id,)
        )
        package_ids_with_files_remaining = {row[0] for row in cursor.fetchall()}

        if package_ids_with_files_remaining:
            conn.execute(
                f"""
                DELETE FROM packages
                WHERE repo_id = ?
                    AND package_id NOT IN ({','.join(['?']*len(package_ids_with_files_remaining))})
                """,
                [repo_id] + list(package_ids_with_files_remaining)
            )
        else:
            # If no files remain, delete all packages for that repo
            conn.execute("DELETE FROM packages WHERE repo_id = ?", (repo_id,))

        # changed and remaining packages are of interest to us
        package_ids_with_files_deleted_and_remaining = package_ids_with_file_deletes & package_ids_with_files_remaining
        if package_ids_with_files_deleted_and_remaining:
            conn.execute(f"""
                UPDATE packages
                SET last_modified_at = CURRENT_TIMESTAMP
                WHERE repo_id = ?
                    AND package_id IN ({','.join(['?']*len(package_ids_with_files_deleted_and_remaining))})
            """, [repo_id] + list(package_ids_with_files_deleted_and_remaining))
    
    # 3) Since we've confirmed there were changes (deleted or modified),
    #    update the repository's last_modified_at
    conn.execute("""
        UPDATE repositories
           SET last_modified_at = CURRENT_TIMESTAMP
         WHERE repo_id = ?
    """, (repo_id,))

    conn.commit()
    conn.close()

    # 4) Return the modified filepaths so the rest of the flow can process them
    return {
        "repo_id": repo_id,
        "filepaths": state.repo_event.event.meta_data.modified,
        "packages_impacted": package_ids_with_files_deleted_and_remaining
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
            FilepathState(filepath=filepath, repo_dir=state.repo_dir, repo_event=state.repo_event)
        ) 
        for filepath in state.filepaths
    ]


async def generate_file_summary(state: FilepathState, *, config: RunnableConfig) -> dict:
    """Generate a semantic summary for a single file.

    This function fetches the file content (either from a local clone or via GitHub API
    for an update event), then uses a language model to create a concise summary.

    Args:
        state (FilepathState): Contains the filepath, repo directory, and repo event details.
        config (RunnableConfig): The runtime configuration.

    Returns:
        dict: A dictionary with a single key "file_summaries", which is a list of FileSummary objects.
    """
    configuration = Configuration.from_runnable_config(config)
    
    file_type = state.filepath.split(".")[-1]
    # Decide if we are in 'onboard' or 'update' mode
    event_type = state.repo_event.event.event_type
    if event_type == "repo-update":
        # We do NOT have a local clone => fetch via GitHub API
        file_content = get_file_content_from_github(
            state.repo_event.repo.url,
            state.filepath,
            configuration.gh_token,
            state.repo_event.repo.branch
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


async def save_file_summaries(state: OnboardState, *, config: RunnableConfig) -> dict:
    """Save generated file summaries into a SQLite database.

    Creates the necessary database schema (repositories, packages, files) if it doesn't exist.
    Fetches or inserts the repository row, updates the last_modified_at column, and associates
    file summaries with their respective packages.

    Args:
        state (OnboardState): Contains `file_summaries` to be saved, along with filepaths and repo info.
        config (RunnableConfig): The runtime configuration.

    Returns:
        dict: A dictionary containing:
            - "repo_id" (int): The repository ID for the current repo.
            - "packages_impacted" (list[int]): A list of packages impacted by the file changes.
            - "filepaths" and "file_summaries" set to "delete" to clear them from the state.
    """
    conn = sqlite3.connect("store.db")
    conn.execute("PRAGMA foreign_keys = ON;")

    # 1) Create tables if they do not exist ---
    conn.execute("""CREATE TABLE IF NOT EXISTS repositories (
        repo_id     INTEGER PRIMARY KEY AUTOINCREMENT,
        url         TEXT NOT NULL,
        src_path    TEXT NOT NULL,
        branch      TEXT NOT NULL,
        created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
        last_modified_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(url, src_path, branch)
    );
    """)
    conn.execute("""CREATE TABLE IF NOT EXISTS packages (
        package_id   INTEGER PRIMARY KEY AUTOINCREMENT,
        repo_id      INTEGER NOT NULL,
        package_name TEXT NOT NULL,
        summary      TEXT,
        created_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
        last_modified_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (repo_id) REFERENCES repositories(repo_id),
        UNIQUE(repo_id, package_name)
    );
    """)
    conn.execute("""CREATE TABLE IF NOT EXISTS files (
        file_id     INTEGER PRIMARY KEY AUTOINCREMENT,
        repo_id     INTEGER NOT NULL,
        package_id  INTEGER NOT NULL,
        file_path   TEXT NOT NULL,
        summary     TEXT,
        created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
        last_modified_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (repo_id) REFERENCES repositories(repo_id),
        FOREIGN KEY (package_id) REFERENCES packages(package_id),
        UNIQUE(repo_id, package_id, file_path)
    );
    """)

    # 2) Insert (or fetch) the repository row ---
    cursor = conn.execute("""SELECT repo_id
        FROM repositories
        WHERE url = ? AND src_path = ? AND branch = ?
    """, (state.repo_event.repo.url,
          state.repo_event.repo.src_folder,
          state.repo_event.repo.branch))
    row = cursor.fetchone()

    if row is None:
        # Insert a new repository
        cursor = conn.execute("""INSERT INTO repositories (url, src_path, branch, last_modified_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
        """, (state.repo_event.repo.url,
              state.repo_event.repo.src_folder,
              state.repo_event.repo.branch))
        repo_id = cursor.lastrowid
    else:
        repo_id = row[0]
        # Update last_modified_at if repo already exists
        conn.execute("""UPDATE repositories SET last_modified_at = CURRENT_TIMESTAMP
            WHERE repo_id = ?""", (repo_id,))

    # 3) Group filepaths by their top-level package
    pkg_dict = group_by_top_level_packages(state.filepaths, src_folder=state.repo_event.repo.src_folder)

    packages_impacted = []

    # For each package in pkg_dict, insert or fetch the package row
    for pkg_name, file_list in pkg_dict.items():
        cursor = conn.execute("""SELECT package_id
            FROM packages
            WHERE repo_id = ? AND package_name = ?
        """, (repo_id, pkg_name))
        pkg_row = cursor.fetchone()

        if pkg_row is None:
            # Insert new row with summary = NULL
            cursor = conn.execute("""INSERT INTO packages (repo_id, package_name, summary, last_modified_at)
                VALUES (?, ?, NULL, CURRENT_TIMESTAMP)
            """, (repo_id, pkg_name))
            package_id = cursor.lastrowid
        else:
            package_id = pkg_row[0]

        packages_impacted.append(package_id)

        # 4) Associate file summaries with this package
        for fsum in state.file_summaries:
            if fsum.filepath in file_list:
                conn.execute("""INSERT INTO files (repo_id, package_id, file_path, summary, last_modified_at)
                    VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(repo_id, package_id, file_path) DO UPDATE SET 
                        summary = excluded.summary,
                        last_modified_at = CURRENT_TIMESTAMP
                """, (repo_id, package_id, fsum.filepath, fsum.summary))

    conn.commit()
    conn.close()

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
    """Generate a summary for a single package by aggregating file summaries.

    1. Fetch the file summaries for (package_id, repo_id).
    2. Use a language model to create a consolidated package summary.

    Args:
        state (PackageState): Contains the package ID and repo ID.
        config (RunnableConfig): The runtime configuration.

    Returns:
        dict: A dictionary with "package_summaries" as a list containing one PackageSummary object.
    """
    configuration = Configuration.from_runnable_config(config)
    # 1) Fetch the file summaries for this (package_id, repo_id)
    conn = sqlite3.connect("store.db")
    conn.execute("PRAGMA foreign_keys = ON;")
    cursor = conn.execute("""SELECT file_path, summary
        FROM files
        WHERE repo_id = ? AND package_id = ?
    """, (state.repo_id, state.package_id))
    file_rows = cursor.fetchall()

    package_name = conn.execute("""SELECT package_name
        FROM packages
        WHERE repo_id = ? AND package_id = ?
    """, (state.repo_id, state.package_id)).fetchone()[0]
    
    conn.close()
    
    file_summaries = []
    for row in file_rows:
        filepath, summary = row
        file_summaries.append(f"# {filepath}\n{shift_markdown_headings(summary, increment=1)}")

    # 2) Generate the package summary
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
        "package_summaries": [PackageSummary(package_id=state.package_id, summary=extract_code_block_content(response.content))]
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
    conn = sqlite3.connect("store.db")
    conn.execute("PRAGMA foreign_keys = ON;")

    for psum in state.package_summaries:
        conn.execute("""UPDATE packages
            SET summary = ?, last_modified_at = CURRENT_TIMESTAMP
            WHERE repo_id = ? AND package_id = ?
        """, (psum.summary, state.repo_id, psum.package_id))

    conn.commit()
    conn.close()

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
        dict: A dictionary setting `repo_dir` and `repo_event` to `None`.
    """
    if state.repo_dir:
        remove_cloned_repository(state.repo_dir)
        
    return {
        "repo_dir": None,
        "repo_event": None
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
