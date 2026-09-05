from src.pipelines.utils.characterisation_gt_scope import (
    filter_characterisation_document,
    synthesis_outputs_from_steps,
)


def test_drops_ligand_and_keeps_synthesis_product() -> None:
    steps = {
        "Synthesis": [
            {
                "productNames": ["VMOC-6"],
                "productCCDCNumber": "2278526",
            }
        ]
    }
    characterisation = {
        "Devices": [
            {
                "Characterisation": [
                    {
                        "productNames": ["H4PBPTA"],
                        "productCCDCNumber": "N/A",
                    },
                    {
                        "productNames": ["VMOC-6"],
                        "productCCDCNumber": "2278526",
                    },
                ]
            }
        ]
    }

    filtered, removed = filter_characterisation_document(characterisation, steps)

    assert [item["productNames"] for item in removed] == [["H4PBPTA"]]
    kept = filtered["Devices"][0]["Characterisation"]
    assert [item["productNames"][0] for item in kept] == ["VMOC-6"]


def test_core_name_matches_parenthetical_alias() -> None:
    outputs = synthesis_outputs_from_steps(
        {"Synthesis": [{"productNames": ["VMOP-α"], "productCCDCNumber": "1590349"}]}
    )
    assert "1590349" in outputs.ccdcs
    assert "vmop-alpha" in outputs.cores
