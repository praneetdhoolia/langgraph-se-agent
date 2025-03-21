import sqlite3
from datetime import datetime
from typing import Optional, List, Tuple, Dict, Any

from se_agent.store.store_interface import StoreInterface, RepoRecord, PackageRecord, FileRecord

class SQLiteStore(StoreInterface):
    def __init__(self, db_path: str):
        """
        Initialize the SQLiteStore with the given database path.
        """
        self.db_path = db_path
        self.connection = sqlite3.connect(self.db_path)
        self.connection.row_factory = sqlite3.Row
        self.create_tables()

    def create_tables(self) -> None:
        """
        Create the repositories, packages, and files tables if they do not already exist.
        """
        c = self.connection.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS repositories (
                repo_id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL,
                src_path TEXT NOT NULL,
                branch TEXT NOT NULL,
                created_at TEXT NOT NULL,
                last_modified_at TEXT NOT NULL
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS packages (
                package_id INTEGER PRIMARY KEY AUTOINCREMENT,
                repo_id INTEGER NOT NULL,
                package_name TEXT NOT NULL,
                summary TEXT,
                created_at TEXT NOT NULL,
                last_modified_at TEXT NOT NULL,
                FOREIGN KEY(repo_id) REFERENCES repositories(repo_id)
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS files (
                file_id INTEGER PRIMARY KEY AUTOINCREMENT,
                repo_id INTEGER NOT NULL,
                package_id INTEGER NOT NULL,
                file_path TEXT NOT NULL,
                summary TEXT NOT NULL,
                created_at TEXT NOT NULL,
                last_modified_at TEXT NOT NULL,
                FOREIGN KEY(repo_id) REFERENCES repositories(repo_id),
                FOREIGN KEY(package_id) REFERENCES packages(package_id)
            )
        """)
        self.connection.commit()

    # Repository Operations

    def get_all_repos(self) -> List[RepoRecord]:
        """
        Fetch all repository records from the database.
        :return: A list of RepoRecord objects.
        """
        c = self.connection.cursor()
        c.execute("SELECT * FROM repositories")
        rows = c.fetchall()
        return [
            RepoRecord(
                repo_id=row["repo_id"],
                url=row["url"],
                src_path=row["src_path"],
                branch=row["branch"],
                created_at=row["created_at"],
                last_modified_at=row["last_modified_at"]
            )
            for row in rows]

    def get_repo(self, url: str, src_path: Optional[str] = None, branch: Optional[str] = None) -> Optional[RepoRecord]:
        query = "SELECT * FROM repositories WHERE url = ?"
        params = [url]
        
        if src_path is not None:
            query += " AND src_path = ?"
            params.append(src_path)
            
        if branch is not None:
            query += " AND branch = ?"
            params.append(branch)
        
        c = self.connection.cursor()
        c.execute(query, params)
        row = c.fetchone()
        
        if row:
            return RepoRecord(
                repo_id=row["repo_id"],
                url=row["url"],
                src_path=row["src_path"],
                branch=row["branch"],
                created_at=row["created_at"],
                last_modified_at=row["last_modified_at"]
            )
        return None

    def insert_repo(self, repo_data: Dict[str, Any]) -> int:
        now = datetime.utcnow().isoformat()
        c = self.connection.cursor()
        c.execute("""
            INSERT INTO repositories (url, src_path, branch, created_at, last_modified_at)
            VALUES (?, ?, ?, ?, ?)
        """, (repo_data["url"], repo_data["src_path"], repo_data["branch"], now, now))
        self.connection.commit()
        return c.lastrowid

    def update_repo_last_modified(self, repo_id: int) -> None:
        now = datetime.utcnow().isoformat()
        c = self.connection.cursor()
        c.execute("""
            UPDATE repositories
            SET last_modified_at = ?
            WHERE repo_id = ?
        """, (now, repo_id))
        self.connection.commit()

    # Package Operations

    def get_package(self, repo_id: int, package_name: str) -> Optional[PackageRecord]:
        c = self.connection.cursor()
        c.execute("""
            SELECT * FROM packages
            WHERE repo_id = ? AND package_name = ?
        """, (repo_id, package_name))
        row = c.fetchone()
        if row:
            return PackageRecord(
                package_id=row["package_id"],
                repo_id=row["repo_id"],
                package_name=row["package_name"],
                summary=row["summary"],
                created_at=row["created_at"],
                last_modified_at=row["last_modified_at"]
            )
        return None

    def insert_package(self, repo_id: int, package_name: str) -> int:
        now = datetime.utcnow().isoformat()
        c = self.connection.cursor()
        c.execute("""
            INSERT INTO packages (repo_id, package_name, summary, created_at, last_modified_at)
            VALUES (?, ?, ?, ?, ?)
        """, (repo_id, package_name, None, now, now))
        self.connection.commit()
        return c.lastrowid

    def update_package_last_modified(self, repo_id: int, package_id: int) -> None:
        now = datetime.utcnow().isoformat()
        c = self.connection.cursor()
        c.execute("""
            UPDATE packages
            SET last_modified_at = ?
            WHERE repo_id = ? AND package_id = ?
        """, (now, repo_id, package_id))
        self.connection.commit()

    def update_package_summary(self, repo_id: int, package_id: int, summary: str) -> None:
        now = datetime.utcnow().isoformat()
        c = self.connection.cursor()
        c.execute("""
            UPDATE packages
            SET summary = ?, last_modified_at = ?
            WHERE repo_id = ? AND package_id = ?
        """, (summary, now, repo_id, package_id))
        self.connection.commit()

    def delete_orphan_packages(self, repo_id: int, valid_package_ids: List[int]) -> None:
        c = self.connection.cursor()
        if valid_package_ids:
            placeholders = ','.join('?' for _ in valid_package_ids)
            query = f"""
                DELETE FROM packages
                WHERE repo_id = ? AND package_id NOT IN ({placeholders})
            """
            c.execute(query, (repo_id, *valid_package_ids))
        else:
            # If no valid package IDs provided, delete all packages for the repo.
            c.execute("DELETE FROM packages WHERE repo_id = ?", (repo_id,))
        self.connection.commit()

    # File Operations

    def insert_or_update_file(self, repo_id: int, package_id: int, file_path: str, summary: str) -> None:
        now = datetime.utcnow().isoformat()
        c = self.connection.cursor()
        # Check if the file record already exists.
        c.execute("""
            SELECT file_id FROM files
            WHERE repo_id = ? AND file_path = ?
        """, (repo_id, file_path))
        row = c.fetchone()
        if row:
            # Update the existing record.
            c.execute("""
                UPDATE files
                SET summary = ?, last_modified_at = ?
                WHERE file_id = ?
            """, (summary, now, row["file_id"]))
        else:
            # Insert a new file record.
            c.execute("""
                INSERT INTO files (repo_id, package_id, file_path, summary, created_at, last_modified_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (repo_id, package_id, file_path, summary, now, now))
        self.connection.commit()

    def delete_files(self, repo_id: int, file_paths: List[str]) -> None:
        c = self.connection.cursor()
        if file_paths:
            placeholders = ','.join('?' for _ in file_paths)
            query = f"DELETE FROM files WHERE repo_id = ? AND file_path IN ({placeholders})"
            c.execute(query, (repo_id, *file_paths))
            self.connection.commit()

    def get_file_summaries_for_package(self, repo_id: int, package_id: int) -> List[Tuple[str, str]]:
        c = self.connection.cursor()
        c.execute("""
            SELECT file_path, summary FROM files
            WHERE repo_id = ? AND package_id = ?
        """, (repo_id, package_id))
        rows = c.fetchall()
        return [(row["file_path"], row["summary"]) for row in rows]

    # Additional Fetch Methods

    def fetch_repo_data(self, repo_id: int) -> Optional[RepoRecord]:
        c = self.connection.cursor()
        c.execute("""
            SELECT * FROM repositories
            WHERE repo_id = ?
        """, (repo_id,))
        row = c.fetchone()
        if row:
            return RepoRecord(
                repo_id=row["repo_id"],
                url=row["url"],
                src_path=row["src_path"],
                branch=row["branch"],
                created_at=row["created_at"],
                last_modified_at=row["last_modified_at"]
            )
        return None

    def fetch_package_data(self, repo_id: int) -> List[PackageRecord]:
        c = self.connection.cursor()
        c.execute("""
            SELECT * FROM packages
            WHERE repo_id = ?
        """, (repo_id,))
        rows = c.fetchall()
        return [
            PackageRecord(
                package_id=row["package_id"],
                repo_id=row["repo_id"],
                package_name=row["package_name"],
                summary=row["summary"],
                created_at=row["created_at"],
                last_modified_at=row["last_modified_at"]
            )
            for row in rows
        ]

    def fetch_file_data(self, package_id: int) -> List[FileRecord]:
        c = self.connection.cursor()
        c.execute("""
            SELECT * FROM files
            WHERE package_id = ?
        """, (package_id,))
        rows = c.fetchall()
        return [
            FileRecord(
                file_id=row["file_id"],
                repo_id=row["repo_id"],
                package_id=row["package_id"],
                file_path=row["file_path"],
                summary=row["summary"],
                created_at=row["created_at"],
                last_modified_at=row["last_modified_at"]
            )
            for row in rows
        ]
    
    # Helper Methods for Update Handling
    def get_package_ids_for_files(self, repo_id: int, file_paths: List[str]) -> set:
        """
        Retrieve the set of package IDs for the given repository and file paths.
        """
        c = self.connection.cursor()
        if not file_paths:
            return set()
        placeholders = ','.join('?' for _ in file_paths)
        query = f"""
            SELECT DISTINCT package_id FROM files
            WHERE repo_id = ? AND file_path IN ({placeholders})
        """
        params = [repo_id] + file_paths
        c.execute(query, params)
        rows = c.fetchall()
        return {row["package_id"] for row in rows}

    def get_valid_package_ids(self, repo_id: int) -> set:
        """
        Retrieve the set of package IDs that still have at least one file in the repository.
        """
        c = self.connection.cursor()
        c.execute("""
            SELECT DISTINCT package_id FROM files
            WHERE repo_id = ?
        """, (repo_id,))
        rows = c.fetchall()
        return {row["package_id"] for row in rows}


    def __del__(self):
        if self.connection:
            self.connection.close()