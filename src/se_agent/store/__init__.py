from se_agent.store.sqlite_store import SQLiteStore
from se_agent.store.store_interface import (
    StoreInterface,
    RepoRecord,
    PackageRecord,
    FileRecord,
)

def get_store(store_type: str, **kwargs):
    if store_type == "sqlite":
        return SQLiteStore(**kwargs)
    else:
        raise ValueError(f"Unsupported store type: {store_type}")

__all__ = ["get_store", "StoreInterface", "RepoRecord", "PackageRecord", "FileRecord", "SQLiteStore"]