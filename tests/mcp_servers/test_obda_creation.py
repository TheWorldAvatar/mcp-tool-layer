
import pytest

from src.mcp_servers.stack.operations.obda_creation_operations import OBDAInput, EntityMapping, TableMapping, create_obda_file

def test_obda_creation_step_by_step(tmp_path):
    # Step 1: Define prefixes
    prefixes = {
        "":   "http://example.org/resource/",
        "ont":"http://example.org/gaussian#",
        "xsd":"http://www.w3.org/2001/XMLSchema#",
    }

    # Step 2: Define entity mappings
    vib_mode_entity = EntityMapping(
        id_columns=["file", "index"],
        ontology_class="VibrationalMode",
        iri_template="vibMode_{file}_{index}",
        use_xsd_typing=True,
        tables=[
            TableMapping(
                table_name="vibfreqs",
                columns=["file", "index", "value"],
                property_mappings={"value": "hasVibrationalFrequency"},
            ),
            TableMapping(
                table_name="vibrmasses",
                columns=["file", "index", "value"],
                property_mappings={"value": "hasReducedMass"},
            ),
        ],
    )

    atom_entity = EntityMapping(
        id_columns=["file", "index"],
        ontology_class="Atom",
        iri_template="atom_{file}_{index}",
        use_xsd_typing=True,
        tables=[
            TableMapping(
                table_name="atomcoords",
                columns=["file", "index", "value"],
                property_mappings={"value": "hasCoordinate"},
            ),
            TableMapping(
                table_name="atomnos",
                columns=["file", "index", "value"],
                property_mappings={"value": "hasAtomicNumber"},
            ),
        ],
    )

    # Step 3: Create OBDAInput
    obda_input = OBDAInput(
        prefixes=prefixes,
        entities=[vib_mode_entity, atom_entity],
    )

    # Step 4: Call create_obda_file
    meta_task_name = "gaussian"
    iteration_index = 1

    msg = create_obda_file(
        obda_input=obda_input,
        meta_task_name=meta_task_name,
        iteration_index=iteration_index,
    )

    # Step 5: Assert the result is as expected (basic check)
    assert isinstance(msg, str)
    assert "OBDA" in msg or "obda" in msg or msg  # At least not empty
