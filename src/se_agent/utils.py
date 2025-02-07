import base64
import os
import re
import requests
from urllib.parse import urlparse

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel


def split_github_url(repo_url):
    parsed_url = urlparse(repo_url)
    parts = parsed_url.path.strip("/").split("/")
    
    if len(parts) < 2:
        raise ValueError("Invalid GitHub repository URL")

    base_url = f"{parsed_url.scheme}://{parsed_url.netloc}"
    owner, repo = parts[:2]

    return base_url, owner, repo


def get_all_files(api_url, headers, owner, repo, path="", branch="main"):
    response = requests.get(f"{api_url}/repos/{owner}/{repo}/contents/{path}?ref={branch}", headers=headers)
    
    if response.status_code != 200:
        print(f"Error: {response.status_code}, {response.text}")
        return []

    file_list = []
    for item in response.json():
        if item["type"] == "file":
            file_list.append(item["path"])
        elif item["type"] == "dir":
            file_list.extend(get_all_files(api_url, headers, owner, repo, item["path"], branch))  # Recursive call
    
    return file_list


def get_file_content(api_url, headers, owner, repo, filepath, branch="main"):
    response = requests.get(f"{api_url}/repos/{owner}/{repo}/contents/{filepath}?ref={branch}", headers=headers)
    
    if response.status_code != 200:
        print(f"Error: {response.status_code}, {response.text}")
        return ""

    file_data = response.json()
    file_content = base64.b64decode(file_data["content"]).decode("utf-8")
    return file_content


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
