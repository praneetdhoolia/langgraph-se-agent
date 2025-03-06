import os
import shutil

from git import Repo

from se_agent.utils.utils_misc import file_extensions_images_and_media
from se_agent.utils.utils_git_api import split_github_url


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

def clone_repository(repo_url: str, branch: str, commit_hash: str = None) -> str:
    """Clone a GitHub repository locally, checking out a specified branch or commit.

    Args:
        repo_url (str): The GitHub repository URL.
        branch (str): The branch to check out.
        commit_hash (str, optional): The commit hash to check out after cloning. Defaults to None.

    Raises:
        RuntimeError: If the repository fails to clone or the checkout fails.

    Returns:
        str: The path to the local cloned repository.
    """
    repo_dir = create_local_repo_dir(repo_url, branch)
    try:
        if commit_hash is not None:
            # Clone without checking out files, then checkout the specific commit.
            repo = Repo.clone_from(repo_url, repo_dir, branch=branch, no_checkout=True)
            repo.git.checkout(commit_hash)
        else:
            repo = Repo.clone_from(repo_url, repo_dir, branch=branch)
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
    with open(file_path, "r") as file:
        return file.read()