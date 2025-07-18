from src.utils.resource_db_operations import ResourceDBOperator
from src.utils.file_management import scan_base_folders_recursively
import os
from models.locations import TEST_DATA_DIR

def test_initial_resource_scan():
    # Create a one-time sqlite file for testing
    temp_db_path = os.path.join(TEST_DATA_DIR, "test_resource_db.sqlite")

    # Initialize the resource db operator with the test db path
    resource_db_operator = ResourceDBOperator(db_path=temp_db_path)
    resource_db_operator.reset_db()

    # Scan resources and register them
    resources = scan_base_folders_recursively()
    resource_db_operator.register_resources_bulk(resources)

    # Fetch all resources from the database
    resource_db_operator.cursor.execute("SELECT * FROM resources")
    all_resources = resource_db_operator.cursor.fetchall()

    # Assert that all resource paths exist and collect them for duplicate check
    resource_paths = []
    for res in all_resources:
        # Assuming the resource path is in the second column (index 1)
        resource_path = res[2]
        resource_paths.append(resource_path)
       
        assert os.path.exists(resource_path), f"Resource path does not exist: {resource_path}"

    # Assert there are no duplicate resource paths
    assert len(resource_paths) == len(set(resource_paths)), "Duplicate resource entries found in the database"

    # delete the test db file
    os.remove(temp_db_path)

if __name__ == "__main__":
    test_initial_resource_scan()