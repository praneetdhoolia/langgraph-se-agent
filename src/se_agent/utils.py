import base64
import os
import re
import requests
import shutil
from urllib.parse import urlparse

from git import Repo

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel


file_extensions_images_and_media = [   
        # Image and Media files
        "png", "jpg", "jpeg", "gif", "bmp", "tiff", "svg", "ico", "webp",
        
        # Audio files
        "mp3", "wav", "ogg", "flac", "aac", "m4a",
        
        # Video files
        "mp4", "mkv", "avi", "mov", "wmv", "flv", "webm",
    ]


def split_github_url(repo_url):
    parsed_url = urlparse(repo_url)
    parts = parsed_url.path.strip("/").split("/")
    
    if len(parts) < 2:
        raise ValueError("Invalid GitHub repository URL")

    base_url = f"{parsed_url.scheme}://{parsed_url.netloc}"
    owner, repo = parts[:2]

    return base_url, owner, repo


def create_auth_headers(gh_token: str) -> dict[str, str]:
    """Create authorization headers for the GitHub API.

    Args:
        gh_token (str): GitHub personal access token.

    Returns:
        dict[str, str]: Headers containing the authorization bearer token.
    """
    headers = {"Authorization": f"Bearer {gh_token}"}
    return headers


def get_github_api_endpoint(base_url: str) -> str:
    """Construct the GitHub API endpoint for either public github.com or an enterprise instance.

    Args:
        base_url (str): The base URL of the GitHub instance.

    Returns:
        str: The root URL of the REST API, typically "https://api.github.com" or <base_url>/api/v3.
    """
    api_url = (
        "https://api.github.com"
        if base_url == "https://github.com"
        else f"{base_url}/api/v3"
    )

    return api_url


def get_all_files_from_github(repo_url: str, gh_token: str, path: str = "", branch: str = "main") -> list[str]:
    """Retrieve all file paths from a GitHub repository.

    This function uses the GitHub REST API to recursively traverse a repository directory
    and gather the paths of all files.

    Args:
        repo_url (str): The GitHub repository URL.
        gh_token (str): GitHub personal access token for authorization.
        path (str, optional): Subdirectory path to traverse. Defaults to "".
        branch (str, optional): Branch name. Defaults to "main".

    Returns:
        list[str]: A list of file paths within the specified repository and branch.
    """
    base_url, owner, repo = split_github_url(repo_url)
    api_url = get_github_api_endpoint(base_url)
    headers = create_auth_headers(gh_token)

    filepaths = _get_all_files_from_github_worker(api_url, headers, owner, repo, path, branch)
    return [
        filepath
        for filepath in filepaths
        if filepath.split(".")[-1] not in file_extensions_images_and_media
    ]


def _get_all_files_from_github_worker(api_url: str, headers: dict, owner: str, repo: str, path: str, branch: str) -> list[str]:
    """Helper function to recursively fetch all file paths from a given path.

    Args:
        api_url (str): The GitHub API endpoint.
        headers (dict): The request headers including authorization.
        owner (str): Repository owner.
        repo (str): Repository name.
        path (str): Directory path to traverse.
        branch (str): Branch name.

    Returns:
        list[str]: A list of file paths.
    """
    response = requests.get(f"{api_url}/repos/{owner}/{repo}/contents/{path}?ref={branch}", headers=headers)

    if response.status_code != 200:
        print(f"Error: {response.status_code}, {response.text}")
        return []

    file_list = []
    for item in response.json():
        if item["type"] == "file":
            file_list.append(item["path"])
        elif item["type"] == "dir":
            # Recursive call to gather files in subdirectories
            file_list.extend(get_all_files_from_github(api_url, headers, owner, repo, item["path"], branch))

    return file_list


def get_file_content_from_github(repo_url: str, filepath: str, gh_token: str, branch: str = "main") -> str:
    """Fetch the content of a single file from GitHub (base64-decoded).

    Args:
        repo_url (str): The GitHub repository URL.
        filepath (str): The path to the target file in the repository.
        gh_token (str): GitHub personal access token for authorization.
        branch (str, optional): Branch name. Defaults to "main".

    Returns:
        str: The raw text content of the file, or an empty string if not found.
    """
    base_url, owner, repo = split_github_url(repo_url)
    api_url = get_github_api_endpoint(base_url)
    headers = create_auth_headers(gh_token)

    response = requests.get(f"{api_url}/repos/{owner}/{repo}/contents/{filepath}?ref={branch}", headers=headers)
    if response.status_code != 200:
        print(f"Error: {response.status_code}, {response.text}")
        return ""

    file_data = response.json()
    file_content = base64.b64decode(file_data["content"]).decode("utf-8")
    return file_content


def create_local_repo_dir(repo_url: str, branch: str) -> str:
    """Create a local directory structure for cloning a GitHub repository.

    Args:
        repo_url (str): The GitHub repository URL.
        branch (str): The branch name to be cloned.

    Returns:
        str: The path to the newly created local directory.
    """
    _, owner, repo = split_github_url(repo_url)
    repo_dir = os.path.join(os.getcwd(), "tmp", owner, repo, branch)
    if os.path.exists(repo_dir):
        shutil.rmtree(repo_dir)
    os.makedirs(repo_dir, exist_ok=True)
    return repo_dir


def clone_repository(repo_url: str, branch: str) -> str:
    """Clone a GitHub repository locally, checking out a specified branch.

    Args:
        repo_url (str): The GitHub repository URL.
        branch (str): The branch to check out.

    Raises:
        RuntimeError: If the repository fails to clone.

    Returns:
        str: The path to the local cloned repository.
    """
    repo_dir = create_local_repo_dir(repo_url, branch)
    try:
        Repo.clone_from(repo_url, repo_dir, branch=branch)
    except Exception as e:
        raise RuntimeError(f"Failed to clone repository: {e}")

    return repo_dir


def remove_cloned_repository(repo_dir: str) -> None:
    """Remove a previously cloned repository from the local filesystem.

    Args:
        repo_dir (str): The path to the local repository directory.
    """
    if os.path.exists(repo_dir):
        shutil.rmtree(repo_dir)


def get_filepaths_from_local(repo_dir: str, src_folder: str) -> list[str]:
    """Retrieve all file paths under a given source folder, excluding images/media.

    Args:
        repo_dir (str): The local repository directory.
        src_folder (str): The subfolder within the repo directory to scan for files.

    Returns:
        list[str]: Relative file paths (excluding images and media) from the specified source folder.
    """
    filepaths = []
    for root, _, files in os.walk(os.path.join(repo_dir, src_folder)):
        for file in files:
            if file.split(".")[-1] not in file_extensions_images_and_media:
                filepaths.append(os.path.relpath(os.path.join(root, file), repo_dir))
    return filepaths


def get_file_content_from_local(repo_dir: str, filepath: str) -> str:
    """Read the content of a file from the local filesystem.

    Args:
        repo_dir (str): The path to the local repository directory.
        filepath (str): Relative path to the file within the repository.

    Returns:
        str: The contents of the file as a string.
    """
    file_path = os.path.join(repo_dir, filepath)
    with open(file_path, "r", encoding="utf-8") as file:
        return file.read()


def load_chat_model(fully_specified_name: str) -> BaseChatModel:
    """Load a chat model from a fully specified name.

    Args:
        fully_specified_name (str): String in the format 'provider/model'.
    """
    if "/" in fully_specified_name:
        provider, model = fully_specified_name.split("/", maxsplit=1)
    else:
        provider = ""
        model = fully_specified_name
    return init_chat_model(model, model_provider=provider)


def extract_code_block_content(input_string: str) -> str:
    """Extracts the content inside a code block fence from the input string.

    Args:
        input_string (str): The string that may contain a code block fenced with triple backticks.

    Returns:
        str: The content inside the code block fence if found; otherwise, returns the original input string.
    """
    # Compile regex pattern to match exactly one opening and one closing code fence
    pattern = re.compile(r'^```(?:\w+)?\r?\n(.*?)\r?\n```$', re.DOTALL)

    # Check if the input_string is entirely wrapped in a code block fence
    match = pattern.match(input_string)
    if match:
        # If matched, extract the content inside the fences
        input_string = match.group(1)

    return input_string


def group_by_top_level_packages(filepaths: list[str], src_folder: str) -> dict[str, list[str]]:
    """Groups filepaths by their top-level package.

    Args:
        filepaths (list[str]): List of filepaths.

    Returns:
        dict[str, list[str]]: A dictionary mapping package names to their respective filepaths.
    """
    pkg_dict = {}
    for file_path in filepaths:
        rel_path = os.path.relpath(file_path, src_folder)
        
        if os.sep in rel_path:
            package = rel_path.split(os.sep)[0]
        else:
            package = "base"

        if package not in pkg_dict:
            pkg_dict[package] = []

        pkg_dict[package].append(file_path)

    return pkg_dict


def shift_markdown_headings(content: str, increment: int = 1) -> str:
    """
    Shifts all Markdown heading levels in 'content' by 'increment'.
    For example, '# Title' -> '## Title' if increment=1, and so on.
    """
    def replacer(match):
        hashes = match.group(1)        # The string of '#' characters
        heading_text = match.group(2)  # The remainder of the line after the '#'
        
        # Increase the number of '#' by 'increment'
        new_hashes = '#' * (len(hashes) + increment)
        return f"{new_hashes} {heading_text}"

    # Regex explanation:
    #   ^(#+)\s+(.*)$
    #   ^(#+)       -> one or more '#' at the start of the line (capturing group 1)
    #   \s+         -> one or more whitespace characters
    #   (.*)$       -> the rest of the line (capturing group 2)
    # The MULTILINE flag (^ matches start of line rather than start of the entire string)
    return re.sub(r'^(#+)\s+(.*)$', replacer, content, flags=re.MULTILINE)
