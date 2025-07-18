import sqlite3
from typing import Literal, Optional, List
from models.locations import DOCKER_DB_PATH

class DockerResource:
    def __init__(
        self,
        container_id: str,
        container_name: str,
        description: str,
        status: Literal["running", "stopped", "created"],
        meta_task_name: str
    ):
        self.container_id = container_id
        self.container_name = container_name
        self.description = description
        self.status = status
        self.meta_task_name = meta_task_name

    def __str__(self):
        return (
            f"DockerResource(container_id={self.container_id}, "
            f"container_name={self.container_name}, "
            f"description={self.description}, "
            f"status={self.status}, "
            f"meta_task_name={self.meta_task_name})"
        )

class DockerDBOperator:
    """
    Handles registration and lookup of Docker containers in a local sqlite database.
    """
    def __init__(self, db_path: str = DOCKER_DB_PATH):
        self.db_path = db_path
        self.db = sqlite3.connect(db_path)
        self.cursor = self.db.cursor()
        self.initialize_db()

    def initialize_db(self):
        """
        Create the docker_resources table if it does not exist.
        """
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS docker_resources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                container_id TEXT NOT NULL UNIQUE,
                container_name TEXT NOT NULL,
                description TEXT NOT NULL,
                status TEXT NOT NULL,
                meta_task_name TEXT NOT NULL
            )
        """)
        self.db.commit()

    def register_docker_resource(self, docker_resource: DockerResource):
        """
        Insert a DockerResource into the database. Ignore if container_id already exists.
        """
        self.cursor.execute("""
            INSERT OR IGNORE INTO docker_resources (container_id, container_name, description, status, meta_task_name)
            VALUES (?, ?, ?, ?, ?)
        """, (
            docker_resource.container_id,
            docker_resource.container_name,
            docker_resource.description,
            docker_resource.status,
            docker_resource.meta_task_name
        ))
        self.db.commit()

    def get_docker_resource_by_id(self, container_id: str) -> Optional[DockerResource]:
        """
        Retrieve a DockerResource from the database by its container_id.
        """
        self.cursor.execute(
            "SELECT container_id, container_name, description, status, meta_task_name FROM docker_resources WHERE container_id = ?",
            (container_id,)
        )
        row = self.cursor.fetchone()
        if row:
            return DockerResource(*row)
        return None

    def get_all_docker_resources(self, meta_task_name: str) -> List[DockerResource]:
        """
        Retrieve all DockerResource entries from the database.
        """
        self.cursor.execute(
            "SELECT container_id, container_name, description, status, meta_task_name FROM docker_resources WHERE meta_task_name = ?",
            (meta_task_name,)
        )
        rows = self.cursor.fetchall()
        return [DockerResource(*row) for row in rows]

    def update_docker_resource_status(self, container_id: str, new_status: Literal["running", "stopped", "created"]):
        """
        Update the status of a DockerResource by container_id.
        """
        self.cursor.execute(
            "UPDATE docker_resources SET status = ? WHERE container_id = ?",
            (new_status, container_id)
        )
        self.db.commit()

    def delete_docker_resource(self, container_id: str):
        """
        Delete a DockerResource from the database by container_id.
        """
        self.cursor.execute(
            "DELETE FROM docker_resources WHERE container_id = ?",
            (container_id,)
        )
        self.db.commit()

    def close(self):
        """
        Close the database connection.
        """
        self.db.close()


if __name__ == "__main__":
    docker_db_operator = DockerDBOperator()
    # docker_db_operator.register_docker_resource(DockerResource(container_id="123", container_name="test", description="test", status="created", meta_task_name="example_task"))
    docker_db_operator.close()

