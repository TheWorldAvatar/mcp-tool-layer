"""Focused tests for entity-first main extraction and KG orchestration."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from src.pipelines.main_kg_building import build as kg_build
from src.pipelines.main_ontology_extractions import extract


class TestEntityFirstPipelineOrder(unittest.TestCase):
    def test_extraction_completes_each_entity_before_the_next(self) -> None:
        calls: list[str] = []
        entities = [
            {"label": "Entity A", "uri": "urn:a"},
            {"label": "Entity B", "uri": "urn:b"},
        ]

        def run_entity(_doi_hash: str, config: dict) -> bool:
            entity_safe = config["_entity_first_entity_safe"]
            calls.extend(
                [
                    f"{entity_safe}:iter2",
                    f"{entity_safe}:iter3",
                    f"{entity_safe}:enrichment3.1",
                    f"{entity_safe}:iter4",
                ]
            )
            config["_entity_first_successful_writes"].append(4)
            return True

        with tempfile.TemporaryDirectory() as tmp:
            marker = str(Path(tmp) / ".main_ontology_extractions_done")
            with patch.object(extract, "run_step", side_effect=run_entity):
                ok = extract._run_extractions_entity_first(
                    doi_hash="doi",
                    config={},
                    top_entities=entities,
                    marker_file=marker,
                )

            self.assertTrue(ok)
            self.assertTrue(Path(marker).exists())

        self.assertEqual(
            calls,
            [
                "Entity_A:iter2",
                "Entity_A:iter3",
                "Entity_A:enrichment3.1",
                "Entity_A:iter4",
                "Entity_B:iter2",
                "Entity_B:iter3",
                "Entity_B:enrichment3.1",
                "Entity_B:iter4",
            ],
        )

    def test_kg_completes_all_iterations_and_publish_per_entity(self) -> None:
        calls: list[str] = []
        known_sets: list[set[str]] = []
        entities = [
            {"label": "Entity A", "uri": "urn:a"},
            {"label": "Entity B", "uri": "urn:b"},
        ]
        iterations = [
            {"iteration_number": 2},
            {"iteration_number": 3},
            {"iteration_number": 4},
        ]

        async def process_entity(**kwargs: object) -> bool:
            known_sets.append(kwargs["known_top_entity_uris"])  # type: ignore[arg-type]
            entity = kwargs["top_entities"][0]  # type: ignore[index]
            label = entity["label"]
            for iteration in kwargs["iterations"]:  # type: ignore[union-attr]
                calls.append(f"{label}:iter{iteration['iteration_number']}")
            calls.append(f"{label}:publish")
            return True

        with (
            patch.object(
                kg_build,
                "_process_iterations_for_entities",
                new=AsyncMock(side_effect=process_entity),
            ),
            patch.object(kg_build.asyncio, "sleep", new=AsyncMock()),
        ):
            ok = asyncio.run(
                kg_build._process_iterations(
                    doi_hash="doi",
                    config={},
                    doi_folder="data/doi",
                    top_entities=entities,
                    iterations=iterations,
                    mcp_run_dir="data/doi/mcp_run",
                    data_dir="data",
                    project_root=".",
                )
            )

        self.assertTrue(ok)
        self.assertEqual(
            calls,
            [
                "Entity A:iter2",
                "Entity A:iter3",
                "Entity A:iter4",
                "Entity A:publish",
                "Entity B:iter2",
                "Entity B:iter3",
                "Entity B:iter4",
                "Entity B:publish",
            ],
        )
        self.assertEqual(known_sets, [{"urn:a", "urn:b"}, {"urn:a", "urn:b"}])


if __name__ == "__main__":
    unittest.main()
