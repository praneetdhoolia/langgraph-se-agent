import base64
import requests
from datetime import datetime
from urllib.parse import urlparse


def split_github_url(repo_url: str) -> tuple[str, str, str]:
    """Parse a GitHub repository URL into base URL, owner, and repo name.

    Args:
        repo_url (str): The GitHub repository URL. e.g., "https://github.com/owner/repo".

    Raises:
        ValueError: If the provided URL does not contain at least two segments (owner, repo).

    Returns:
        tuple[str, str, str]: A 3-tuple of (base_url, owner, repo).
    """
    parsed_url = urlparse(repo_url)
    parts = parsed_url.path.strip("/").split("/")

    if len(parts) < 2:
        raise ValueError("Invalid GitHub repository URL")

    base_url = f"{parsed_url.scheme}://{parsed_url.netloc}"
    owner, repo = parts[:2]

    return base_url, owner, repo

def create_auth_headers(token: str) -> dict[str, str]:
    """Create authorization headers for the GitHub API.

    Args:
        token (str): GitHub personal access token.

    Returns:
        dict[str, str]: Headers containing the authorization bearer token.
    """
    headers = {}
    if token:
        headers["Authorization"] = f"token {token}"
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

def get_all_files(repo_url: str, gh_token: str, path: str = "", branch: str = "main") -> list[str]:
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

    return _get_all_files_worker(api_url, headers, owner, repo, path, branch)

def _get_all_files_worker(api_url: str, headers: dict, owner: str, repo: str, path: str, branch: str) -> list[str]:
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
            file_list.extend(get_all_files(api_url, headers, owner, repo, item["path"], branch))

    return file_list

def get_file_content_from_github(
    repo_url: str,
    filepath: str,
    gh_token: str,
    branch: str = "main",
    commit_hash: str = None
) -> str:
    """Fetch the content of a single file from GitHub (base64-decoded).

    Args:
        repo_url (str): The GitHub repository URL.
        filepath (str): The path to the target file in the repository.
        gh_token (str): GitHub personal access token for authorization.
        branch (str, optional): Branch name. Defaults to "main".
        commit_hash (str, optional): Commit hash. If provided, fetches the file as of that commit.
    
    Returns:
        str: The raw text content of the file, or an empty string if not found.
    """
    base_url, owner, repo = split_github_url(repo_url)
    api_url = get_github_api_endpoint(base_url)
    headers = create_auth_headers(gh_token)

    # Use commit_hash as ref if provided; otherwise, fall back to branch.
    ref = commit_hash if commit_hash is not None else branch

    response = requests.get(
        f"{api_url}/repos/{owner}/{repo}/contents/{filepath}?ref={ref}",
        headers=headers
    )
    if response.status_code != 200:
        print(f"Error: {response.status_code}, {response.text}")
        return ""

    file_data = response.json()
    file_content = base64.b64decode(file_data["content"]).decode("utf-8")
    return file_content


def post_issue_comment(repo_url: str, issue_number: int, comment_body: str, gh_token: str) -> dict:
    """
    Posts a comment to a GitHub issue.

    Args:
        repo_url (str): The GitHub repository URL.
        issue_number (int): The issue number to comment on.
        comment_body (str): The content of the comment.
        gh_token (str): GitHub personal access token for authorization.

    Returns:
        dict: The JSON response from the GitHub API.
    """
    base_url, owner, repo = split_github_url(repo_url)
    api_url = get_github_api_endpoint(base_url)
    headers = create_auth_headers(gh_token)
    comment_url = f"{api_url}/repos/{owner}/{repo}/issues/{issue_number}/comments"

    response = requests.post(comment_url, json={"body": comment_body}, headers=headers)
    response.raise_for_status()
    return response.json()