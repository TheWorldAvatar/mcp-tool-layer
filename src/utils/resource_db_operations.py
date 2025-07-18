from models.locations import RESOURCE_DB_PATH, ROOT_DIR
from models.Resource import Resource
import sqlite3
from typing import List, Optional
import os   

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
                iteration INTEGER DEFAULT -1,
                description TEXT DEFAULT ''
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
            "SELECT type, relative_path, absolute_path, uri, meta_task_name, iteration, description FROM resources WHERE uri = ?",
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
        self.cursor.execute("""
            INSERT OR IGNORE INTO resources (type, relative_path, absolute_path, uri, meta_task_name, iteration, description)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            resource.type,
            resource.relative_path,
            resource.absolute_path,
            resource.uri,
            resource.meta_task_name,
            resource.iteration,
            resource.description,
        ))
        self.db.commit()

    def get_initial_resource_or_iteration_specific_resource(self, meta_task_name: str, iteration: int) -> List[Resource]:
        """
        Retrieve the resources with the same meta_task_name and iteration or same meta_task_name and iteration = -1
        """
        self.cursor.execute(
            "SELECT type, relative_path, absolute_path, uri, meta_task_name, iteration, description FROM resources WHERE meta_task_name = ? AND (iteration = ? OR iteration = -1)",
            (meta_task_name, iteration)
        )
        rows = self.cursor.fetchall()
        return [Resource(*row) for row in rows]

    def get_resources_by_meta_task_name(self, meta_task_name: str) -> List[Resource]:
        """
        Retrieve resources from the database by meta_task_name.
        """
        self.cursor.execute(
            "SELECT type, relative_path, absolute_path, uri, meta_task_name, iteration, description FROM resources WHERE meta_task_name = ?",
            (meta_task_name,)
        )
        rows = self.cursor.fetchall()
        return [Resource(*row) for row in rows]

    def get_resources_by_meta_task_name_and_iteration(self, meta_task_name: str, iteration: int) -> List[Resource]:
        """
        Retrieve resources from the database by meta_task_name and iteration.
        """
        self.cursor.execute(
            "SELECT type, relative_path, absolute_path, uri, meta_task_name, iteration, description FROM resources WHERE meta_task_name = ? AND iteration = ?",
            (meta_task_name, iteration)
        )
        rows = self.cursor.fetchall()
        return [Resource(*row) for row in rows]

    def get_all_resources(self) -> List[Resource]:
        """
        Retrieve all resources from the database.
        """
        self.cursor.execute("SELECT type, relative_path, absolute_path, uri, meta_task_name, iteration, description FROM resources")
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
            INSERT OR IGNORE INTO resources (type, relative_path, absolute_path, uri, meta_task_name, iteration, description)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, [
            (
                r.type,
                r.relative_path,
                r.absolute_path,
                r.uri,
                r.meta_task_name,
                r.iteration,
                r.description
            ) for r in resources
        ])
        self.db.commit()

    def close(self):
        """
        Close the database connection.
        """
        self.db.close()


    def scan_and_register_new_files(self, folder_path: str, task_meta_name: str, iteration_index: int):
        """
        Scan the specified folder for files and register any new files as resources in the database.
        Only files not already registered (by URI) will be added.
        """
 
        if not os.path.isdir(folder_path):
            os.makedirs(folder_path, exist_ok=True)
        

        relative_folder_path = os.path.relpath(folder_path, start=ROOT_DIR)



        for file_name in os.listdir(folder_path):
            file_path = os.path.join(folder_path, file_name)
            if os.path.isfile(file_path):
                uri = f"file://{os.path.abspath(file_path)}"
                # Check if this file is already registered
                self.cursor.execute("SELECT 1 FROM resources WHERE uri = ?", (uri,))
                if not self.cursor.fetchone():
                    resource = Resource(
                        type="file",
                        relative_path=os.path.relpath(file_path, start=ROOT_DIR),
                        absolute_path=os.path.abspath(file_path),
                        uri=uri,
                        meta_task_name=task_meta_name,
                        iteration=iteration_index,
                        description="File created during the execution of previous tasks"
                    )
                    self.register_resource(resource)


if __name__ == "__main__":
    import os
    from models.locations import ROOT_DIR
    from src.utils.file_management import scan_base_folders_recursively

    # Remove the existing database file if it exists to start from scratch
    db_path = os.path.join(ROOT_DIR, "resource_db.sqlite3")
    if os.path.exists(db_path):
        os.remove(db_path)

    # Initialize the ResourceDBOperator (this will create a new DB)
    resource_db_operator = ResourceDBOperator()
    resources = scan_base_folders_recursively()
    resource_db_operator.register_resources_bulk(resources)

 
    # Retrieve and print all resources
    resources = resource_db_operator.get_all_resources()
    for resource in resources:
        print(resource)