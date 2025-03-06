from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, List, Tuple, Dict, Any

@dataclass
class RepoRecord:
    repo_id: int
    url: str
    src_path: str
    branch: str
    created_at: str
    last_modified_at: str

@dataclass
class PackageRecord:
    package_id: int
    repo_id: int
    package_name: str
    summary: Optional[str]
    created_at: str
    last_modified_at: str

@dataclass
class FileRecord:
    file_id: int
    repo_id: int
    package_id: int
    file_path: str
    summary: str
    created_at: str
    last_modified_at: str

class StoreInterface(ABC):
    """
    Functional interface for repository, package, and file persistence operations.
    This abstracts the storage concerns so that different backend implementations (e.g.,
    SQLite, filesystem-based) can be swapped with minimal changes to the business logic.
    """

    # Initialization
    @abstractmethod
    def create_tables(self) -> None:
        """
        Initialize the persistence store.
        For SQL-based stores, this could create the necessary tables.
        For a filesystem store, this might create directories or files as needed.
        """
        pass

    # Repository Operations
    @abstractmethod
    def get_repo(self, url: str, src_path: str, branch: str) -> Optional[RepoRecord]:
        """
        Fetch a repository record by url, source path, and branch.
        :return: A RepoRecord if found, else None.
        """
        pass

    @abstractmethod
    def insert_repo(self, repo_data: Dict[str, Any]) -> int:
        """
        Insert a new repository record.
        :param repo_data: Dictionary with keys such as 'url', 'src_path', and 'branch'.
        :return: The new repository's ID.
        """
        pass

    @abstractmethod
    def update_repo_last_modified(self, repo_id: int) -> None:
        """
        Update the last modified timestamp for the repository.
        """
        pass

    # Package Operations
    @abstractmethod
    def get_package(self, repo_id: int, package_name: str) -> Optional[PackageRecord]:
        """
        Fetch a package record for a given repository and package name.
        :return: A PackageRecord if found, else None.
        """
        pass

    @abstractmethod
    def insert_package(self, repo_id: int, package_name: str) -> int:
        """
        Insert a new package record for the repository.
        :return: The new package's ID.
        """
        pass

    @abstractmethod
    def update_package_last_modified(self, repo_id: int, package_id: int) -> None:
        """
        Update the last modified timestamp for a package.
        """
        pass

    @abstractmethod
    def update_package_summary(self, repo_id: int, package_id: int, summary: str) -> None:
        """
        Update the summary for the specified package.
        """
        pass

    @abstractmethod
    def delete_orphan_packages(self, repo_id: int, valid_package_ids: List[int]) -> None:
        """
        Delete packages for a repository that do not have any remaining files.
        :param valid_package_ids: List of package IDs that should remain.
        """
        pass

    # File Operations
    @abstractmethod
    def insert_or_update_file(self, repo_id: int, package_id: int, file_path: str, summary: str) -> None:
        """
        Insert a new file record or update an existing one.
        """
        pass

    @abstractmethod
    def delete_files(self, repo_id: int, file_paths: List[str]) -> None:
        """
        Delete file records from the repository based on a list of file paths.
        """
        pass

    @abstractmethod
    def get_file_summaries_for_package(self, repo_id: int, package_id: int) -> List[Tuple[str, str]]:
        """
        Fetch file summaries for a given package.
        :return: A list of tuples containing file paths and their summaries.
        """
        pass

    # Additional Fetch Methods (if needed)
    @abstractmethod
    def fetch_repo_data(self, repo_id: int) -> Optional[RepoRecord]:
        """
        Retrieve the full repository record for a given repository ID.
        """
        pass

    @abstractmethod
    def fetch_package_data(self, repo_id: int) -> List[PackageRecord]:
        """
        Retrieve all package records for the specified repository.
        """
        pass

    @abstractmethod
    def fetch_file_data(self, package_id: int) -> List[FileRecord]:
        """
        Retrieve all file records for a given package.
        """
        pass

    # Helper Methods for Update Handling
    @abstractmethod
    def get_package_ids_for_files(self, repo_id: int, file_paths: List[str]) -> set:
        """
        Retrieve the set of package IDs for a given repository and list of file paths.
        :param repo_id: The repository identifier.
        :param file_paths: A list of file paths.
        :return: A set of package IDs corresponding to those file paths.
        """
        pass

    @abstractmethod
    def get_valid_package_ids(self, repo_id: int) -> set:
        """
        Retrieve the set of valid (non-orphaned) package IDs for the given repository.
        A valid package is one that still has at least one file record.
        :param repo_id: The repository identifier.
        :return: A set of package IDs that have at least one file associated.
        """
        pass