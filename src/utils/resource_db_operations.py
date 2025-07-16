from models.locations import RESOURCE_DB_PATH
from models.Resource import Resource
import sqlite3
from typing import List, Optional


class ResourceDBOperator:
    """
    This class handles all resource registration within the system.
    Now supports optional meta_task_name (str) and iteration (int) fields.
    """

    def __init__(
        self,
        db_path: str = RESOURCE_DB_PATH
    ):
        self.db_path = db_path
        self.db = sqlite3.connect(db_path)
        self.cursor = self.db.cursor()
        self.initialize_db()

    def initialize_db(self):
        """
        Create the resources table if it does not exist.
        Adds meta_task_name and iteration columns if not present.
        """
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS resources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type TEXT NOT NULL,
                relative_path TEXT NOT NULL,
                absolute_path TEXT NOT NULL,
                uri TEXT NOT NULL UNIQUE,
                meta_task_name TEXT DEFAULT '',
                iteration INTEGER DEFAULT -1
            )
        """)
        self.db.commit()

    def reset_db(self):
        """
        Delete all entries in the resources table, but keep the schema.
        """
        self.cursor.execute("DELETE FROM resources")
        self.db.commit()

    def get_resource_by_uri(self, uri: str) -> Optional[Resource]:
        """
        Retrieve a resource from the database by its URI.
        """
        self.cursor.execute(
            "SELECT type, relative_path, absolute_path, uri FROM resources WHERE uri = ?",
            (uri,)
        )
        row = self.cursor.fetchone()
        return Resource(*row) if row else None

    def register_resource(
        self,
        resource: Resource
    ):
        """
        Insert a resource into the database. Ignore if URI already exists.
        Optionally set meta_task_name and iteration.
        """
 
        # Assuming the two new fields are called 'field1' and 'field2'
        # You must also ensure the table schema is updated elsewhere to include these fields:
        #   field1 TEXT DEFAULT '',
        #   field2 TEXT DEFAULT ''
        # and that the Resource class has .field1 and .field2 attributes.

        self.cursor.execute("""
            INSERT OR IGNORE INTO resources (type, relative_path, absolute_path, uri, meta_task_name, iteration)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            resource.type,
            resource.relative_path,
            resource.absolute_path,
            resource.uri,
            resource.meta_task_name,
            resource.iteration,
        ))
        self.db.commit()

    def get_resources_by_meta_task_name(self, meta_task_name: str) -> List[Resource]:
        """
        Retrieve resources from the database by meta_task_name.
        """
        self.cursor.execute("SELECT type, relative_path, absolute_path, uri, meta_task_name, iteration FROM resources WHERE meta_task_name = ?", (meta_task_name,))
        rows = self.cursor.fetchall()
        return [Resource(*row) for row in rows]

    def get_resources_by_meta_task_name_and_iteration(self, meta_task_name: str, iteration: int) -> List[Resource]:
        """
        Retrieve resources from the database by meta_task_name and iteration.
        """
        self.cursor.execute("SELECT type, relative_path, absolute_path, uri, meta_task_name, iteration FROM resources WHERE meta_task_name = ? AND iteration = ?", (meta_task_name, iteration))
        rows = self.cursor.fetchall()
        return [Resource(*row) for row in rows]

    def get_all_resources(self) -> List[Resource]:
        """
        Retrieve all resources from the database.
        """
        self.cursor.execute("SELECT type, relative_path, absolute_path, uri FROM resources")
        rows = self.cursor.fetchall()
        return [Resource(*row) for row in rows]

    def register_resources_bulk(
        self,
        resources: List[Resource],
        meta_task_name: Optional[str] = None,
        iteration: Optional[int] = None
    ):
        """
        Insert a list of resources into the database. Ignore duplicates based on URI.
        Optionally set meta_task_name and iteration for all.
        """
        self.cursor.executemany("""
            INSERT OR IGNORE INTO resources (type, relative_path, absolute_path, uri, meta_task_name, iteration)
            VALUES (?, ?, ?, ?, ?, ?)
        """, [
            (
                r.type,
                r.relative_path,
                r.absolute_path,
                r.uri,
                r.meta_task_name,
                r.iteration
            ) for r in resources
        ])
        self.db.commit()

    def close(self):
        """
        Close the database connection.
        """
        self.db.close()


if __name__ == "__main__":
    from src.utils.file_management import scan_base_folders_recursively, fuzzy_repo_file_search
    resource_db_operator = ResourceDBOperator()
    resource_db_operator.reset_db()

    resources = scan_base_folders_recursively()
    # for resource in resources:
    #     print(resource)
    #     print("-"*100)
    resource_db_operator.register_resources_bulk(resources)

    # all_resources = resource_db_operator.get_all_resources()
    # for resource in all_resources:
    #     print(resource)
    #     print("-"*100)

    # print(f"Total resources: {len(all_resources)}")

    # print(fuzzy_repo_file_search("data/jiying/data_sniffing_report.md"))
    # print(fuzzy_repo_file_search("/sandbox/codex/"))

    resources = resource_db_operator.get_resources_by_meta_task_name(meta_task_name="jiying")
    for resource in resources:
        print(resource)
        print("-"*100)