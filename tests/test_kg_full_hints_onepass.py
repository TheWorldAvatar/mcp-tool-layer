import json
from pathlib import Path

import pytest

from src.agents.scripts_and_prompts_generation.artifact_surface_contract import (
    derive_main_surface_contract,
)
from src.pipelines.utils.kg_full_hints_onepass import (
    OFFICIAL_CONSTRUCTION_PREFACE,
    build_generic_onepass_kg_prompt,
    build_onepass_kg_prompt,
    collapse_kg_iterations_for_full_hints_onepass,
    combine_hint_ledgers,
    extract_mcp_tool_lines,
    merge_iteration_specs,
    render_runtime_relationship_guidance,
    retarget_official_kg_placeholders,
    resolve_generated_mcp_relationship_contract,
    resolve_generated_mcp_tool_surface,
    stack_official_kg_construction,
)


def test_generic_onepass_prompt_is_domain_neutral() -> None:
    rendered = build_generic_onepass_kg_prompt()

    assert rendered.count("{iteration_hints}") == 1
    assert rendered.count("{entity_uri}") == 1
    assert "actual MCP tool catalog" in rendered
    assert "creator's required parent, dependent, ordering" in rendered


def test_combine_hint_ledgers_matches_ox_sections(tmp_path: Path) -> None:
    (tmp_path / "iter2_hints_route.txt").write_text(
        "SEMANTIC_HINTS_V1\niter2 skeleton\n", encoding="utf-8"
    )
    (tmp_path / "iter3_hints_route.txt").write_text(
        "SEMANTIC_HINTS_V1\niter3 steps\n", encoding="utf-8"
    )
    (tmp_path / "iter4_hints_route.txt").write_text(
        "SEMANTIC_HINTS_V1\niter4 yield\n", encoding="utf-8"
    )

    bundle = combine_hint_ledgers(tmp_path, "route")

    assert bundle.layers == (2, 3, 4)
    assert bundle.text.startswith("SEMANTIC_HINTS_V1")
    assert "=== ITER2 SEMANTIC_HINTS ===" in bundle.text
    assert "=== ITER3 SEMANTIC_HINTS ===" in bundle.text
    assert "=== ITER4 SEMANTIC_HINTS ===" in bundle.text
    assert "iter2 skeleton" in bundle.text
    assert "iter3 steps" in bundle.text
    assert "iter4 yield" in bundle.text


def test_collapse_is_default_off() -> None:
    iterations = [
        {"iteration_number": 1, "kg_building_prompt": "p1"},
        {
            "iteration_number": 2,
            "kg_building_prompt": "p2",
            "responsibilities": {"classes": ["ChemicalInput"], "object_properties": ["hasChemicalInput"]},
        },
        {
            "iteration_number": 3,
            "kg_building_prompt": "p3",
            "responsibilities": {"classes": ["Add"], "object_properties": ["hasSynthesisStep"]},
        },
    ]

    assert collapse_kg_iterations_for_full_hints_onepass(iterations, enabled=False) == iterations
    collapsed = collapse_kg_iterations_for_full_hints_onepass(iterations, enabled=True)
    assert len(collapsed) == 1
    assert collapsed[0]["full_hints_onepass"] is True
    assert collapsed[0]["responsibilities"]["classes"] == ["ChemicalInput", "Add"]
    assert collapsed[0]["responsibilities"]["object_properties"] == [
        "hasChemicalInput",
        "hasSynthesisStep",
    ]


def test_merge_keeps_semantic_scope_union() -> None:
    merged = merge_iteration_specs(
        [
            {
                "iteration_number": 2,
                "kg_building_prompt": "p2",
                "responsibilities": {"classes": ["A"], "object_properties": ["pA"]},
                "semantic_scope": {
                    "classes": [{"local": "A", "iri": "urn:A"}],
                    "object_properties": [{"local": "pA", "iri": "urn:pA"}],
                },
            },
            {
                "iteration_number": 4,
                "kg_building_prompt": "p4",
                "responsibilities": {"classes": ["B"], "object_properties": ["pB"]},
                "linked_materialization_classes": ["C"],
                "semantic_scope": {
                    "classes": [{"local": "B", "iri": "urn:B"}],
                    "object_properties": [{"local": "pB", "iri": "urn:pB"}],
                },
            },
        ]
    )
    assert {row["local"] for row in merged["semantic_scope"]["classes"]} == {"A", "B"}
    assert merged["linked_materialization_classes"] == ["C"]


def test_onepass_prompt_unions_tools_and_binds_hints() -> None:
    prompts = {
        "p2": (
            "Iter2 only. Do not broaden.\n"
            "  - init_memory(doi: 'str', top_level_entity_name: 'str') -> 'str'\n"
            "  - create_ChemicalInput(label: 'str') -> 'str'\n"
            "  - add_hasChemicalInput(subject_iri: str, object_iri: str)\n"
        ),
        "p3": (
            "Iter3 only. Do not broaden.\n"
            "  - create_Add(label: 'str', hasOrder: 'int') -> 'str'\n"
            "  - add_hasSynthesisStep(subject_iri: str, object_iri: str)\n"
            "  - export_memory(doi: 'str', top_level_entity_name: 'str') -> 'str'\n"
            "Consumption of iteration hints (semantic-text.v1)\n"
            "- Add: create exactly one fresh ChemicalInput via create_ChemicalInput "
            "and assert add_hasAddedChemicalInput(Add, ChemicalInput).\n"
            "- Iteration hints: {iteration_hints}\n"
        ),
    }

    rendered = build_onepass_kg_prompt(
        iterations=[
            {
                "iteration_number": 2,
                "kg_building_prompt": "p2",
                "responsibilities": {
                    "classes": ["ChemicalInput"],
                    "object_properties": ["hasChemicalInput"],
                },
            },
            {
                "iteration_number": 3,
                "kg_building_prompt": "p3",
                "responsibilities": {
                    "classes": ["Add"],
                    "object_properties": ["hasSynthesisStep"],
                },
            },
        ],
        project_root=".",
        load_prompt=lambda path, _root: prompts[path],
    )

    assert rendered.count("{iteration_hints}") == 1
    assert "{doi}" in rendered
    assert "create_ChemicalInput" in rendered
    assert "create_Add" in rendered
    assert "add_hasSynthesisStep" in rendered
    assert "ITER2 owns the synthesis/output/input/document skeleton." in rendered
    assert "Official ITER3 KG construction" in rendered
    assert (
        "create exactly one fresh ChemicalInput via create_ChemicalInput "
        "and assert add_hasAddedChemicalInput(Add, ChemicalInput)."
    ) in rendered
    assert "the all-iteration hints bound above" in rendered
    assert extract_mcp_tool_lines(prompts["p2"])[0].startswith("init_memory")
    assert retarget_official_kg_placeholders("{iteration_hints}").startswith("the all-iteration")
    stacked = stack_official_kg_construction(
        specs=[{"iteration_number": 3, "path": "p3"}],
        project_root=".",
        load_prompt=lambda path, _root: prompts[path],
    )
    assert "add_hasAddedChemicalInput(Add, ChemicalInput)" in stacked


def test_onepass_prefers_generated_onepass_fragments_over_layered_prompts() -> None:
    prompts = {
        "layered2": "Iter2 only. Call init_memory and export_memory.",
        "onepass2": (
            "Focused positive Iter2 semantics.\n"
            "  - create_ChemicalInput(label: str) -> str\n"
        ),
        "layered3": "Ignore ordered-member hints.",
        "onepass3": (
            "Focused positive Iter3 semantics.\n"
            "  - create_Add(label: str, order: int, parent_iri: str) -> str\n"
        ),
    }

    rendered = build_onepass_kg_prompt(
        iterations=[
            {
                "iteration_number": 2,
                "kg_building_prompt": "layered2",
                "kg_building_onepass_prompt": "onepass2",
                "responsibilities": {
                    "classes": ["ChemicalInput"],
                    "object_properties": [],
                },
            },
            {
                "iteration_number": 3,
                "kg_building_prompt": "layered3",
                "kg_building_onepass_prompt": "onepass3",
                "responsibilities": {
                    "classes": ["Add"],
                    "object_properties": [],
                },
            },
        ],
        project_root=".",
        load_prompt=lambda path, _root: prompts[path],
    )

    assert "Focused positive Iter2 semantics" in rendered
    assert "Focused positive Iter3 semantics" in rendered
    assert "Iter2 only" not in rendered
    assert "Ignore ordered-member hints" not in rendered


def test_onepass_preface_keeps_split_relationship_writers() -> None:
    prompts = {
        "p2": (
            "  - init_memory(doi: 'str', top_level_entity_name: 'str') -> 'str'\n"
        ),
        "p3": (
            "Add: create_Add then add_hasAddedChemicalInput.\n"
            "  - create_Add(label: str, hasOrder: int) -> str\n"
            "  - add_hasAddedChemicalInput(subject_iri: str, object_iri: str)\n"
            "  - export_memory(doi: 'str', top_level_entity_name: 'str') -> 'str'\n"
        ),
    }

    rendered = build_onepass_kg_prompt(
        iterations=[
            {
                "iteration_number": 2,
                "kg_building_prompt": "p2",
                "responsibilities": {"classes": [], "object_properties": []},
            },
            {
                "iteration_number": 3,
                "kg_building_prompt": "p3",
                "responsibilities": {
                    "classes": ["Add"],
                    "object_properties": ["hasAddedChemicalInput"],
                },
            },
        ],
        project_root=".",
        load_prompt=lambda path, _root: prompts[path],
        allowed_tool_names={
            "init_memory",
            "create_Add",
            "add_hasAddedChemicalInput",
            "export_memory",
        },
    )

    assert "create_Add" in rendered
    assert "add_hasAddedChemicalInput" in rendered
    assert "add_hasAddedChemicalInput on every Add" in OFFICIAL_CONSTRUCTION_PREFACE
    assert "Do not invent a composite/atomic creator" in rendered
    assert "never call or invent a standalone writer" not in rendered


def test_runtime_relationship_guidance_uses_validator_contract() -> None:
    contract = {
        "creator_owned_relationships": {
            "urn:hasOwnedInput": [
                {
                    "public_tool": "create_Member",
                    "owner_class_iri": "urn:Member",
                    "role": "owned_dependent",
                }
            ]
        },
        "object_properties": [
            {
                "property_iri": "urn:hasTopInput",
                "domain_iris": ["urn:Root"],
                "range_iris": ["urn:Input"],
            },
            {
                "property_iri": "urn:hasOwnedInput",
                "domain_iris": ["urn:Member"],
                "range_iris": ["urn:Input"],
            },
        ],
    }

    rendered = render_runtime_relationship_guidance(
        contract,
        owned_property_names={"hasTopInput", "hasOwnedInput"},
    )

    assert "hasTopInput: subject type [Root]; object type [Input]" in rendered
    assert "bound root IRI is the only valid subject" in rendered
    assert "Keep each creator result's `iri` (owner) distinct from `dependent_iri`" in rendered
    assert "hasOwnedInput: creator-owned by create_Member" in rendered
    assert "never issue a separate relationship write" in rendered


def test_onepass_prompt_embeds_runtime_relationship_guidance() -> None:
    prompts = {
        "p2": "  - add_hasTopInput(subject_iri: str, object_iri: str)\n",
        "p3": "  - create_Member(label: str, parent_iri: str) -> str\n",
    }
    contract = {
        "creator_owned_relationships": {},
        "object_properties": [
            {
                "property_iri": "urn:hasTopInput",
                "domain_iris": ["urn:Root"],
                "range_iris": ["urn:Input"],
            }
        ],
    }

    rendered = build_onepass_kg_prompt(
        iterations=[
            {
                "iteration_number": 2,
                "kg_building_prompt": "p2",
                "responsibilities": {
                    "classes": ["Input"],
                    "object_properties": ["hasTopInput"],
                },
            },
            {
                "iteration_number": 3,
                "kg_building_prompt": "p3",
                "responsibilities": {"classes": ["Member"], "object_properties": []},
            },
        ],
        project_root=".",
        load_prompt=lambda path, _root: prompts[path],
        allowed_tool_names={"add_hasTopInput", "create_Member"},
        runtime_relationship_contract=contract,
    )

    assert "# Runtime-derived relationship endpoint contract" in rendered
    assert "hasTopInput: subject type [Root]; object type [Input]" in rendered
    assert "verify its subject and object against the runtime-derived endpoint matrix" in rendered


def test_official_atomic_artifact_prompt_matches_runtime_contract() -> None:
    repo = Path(__file__).resolve().parents[1]
    artifact = repo / "ai_generated_contents_inferred_atomic_script_generation_20260831"
    iterations = json.loads(
        (artifact / "iterations/ontosynthesis/iterations.json").read_text(encoding="utf-8")
    )["iterations"]
    relationship_contract = json.loads(
        (artifact / "scripts/ontosynthesis/_relationship_contract.json").read_text(
            encoding="utf-8"
        )
    )
    surface = set(
        derive_main_surface_contract(
            artifact / "scripts/ontosynthesis"
        )["expected_mcp_tools"]
    )

    def load_artifact_prompt(path: str, _root: str) -> str:
        relative = path.replace("ai_generated_contents/", "", 1)
        return (artifact / relative).read_text(encoding="utf-8")

    rendered = build_onepass_kg_prompt(
        iterations=iterations,
        project_root=repo,
        load_prompt=load_artifact_prompt,
        allowed_tool_names=surface,
        runtime_relationship_contract=relationship_contract,
    )

    assert "add_hasChemicalInput" in rendered
    assert (
        "hasChemicalInput: subject type [ChemicalSynthesis]; "
        "object type [ChemicalInput]"
    ) in rendered
    assert (
        "hasSynthesisStep: creator-owned by create_Add, create_Dry, "
        "create_Evaporate, create_Filter, create_HeatChill, create_Separate, "
        "create_Sonicate, create_Stir, create_Transfer"
    ) in rendered
    assert "hasAddedChemicalInput: creator-owned by create_Add" in rendered


def test_onepass_uses_manifest_surface_when_atomic_prompts_omit_legacy_tool_lines() -> None:
    prompts = {
        "p2": (
            "Use the generated MCP tools.\n"
            "Mandatory Tool Sequence:\n"
            "1. Materialize every source-grounded occurrence.\n"
        ),
        "p3": (
            "Atomic Create Tool Contract:\n"
            "- `create_Add` owns `hasSynthesisStep` and `hasAddedChemicalInput`.\n"
        ),
    }

    rendered = build_onepass_kg_prompt(
        iterations=[
            {
                "iteration_number": 2,
                "kg_building_prompt": "p2",
                "responsibilities": {"classes": [], "object_properties": []},
            },
            {
                "iteration_number": 3,
                "kg_building_prompt": "p3",
                "responsibilities": {
                    "classes": ["Add"],
                    "object_properties": ["hasSynthesisStep"],
                },
            },
        ],
        project_root=".",
        load_prompt=lambda path, _root: prompts[path],
        allowed_tool_names={"init_memory", "create_Add", "export_memory"},
    )

    assert "Closed-world MCP tool surface" in rendered
    assert "  - create_Add" in rendered
    assert "  - export_memory" in rendered
    assert "  - init_memory" in rendered


def test_onepass_rejects_prompt_tools_absent_from_selected_mcp() -> None:
    prompts = {
        "p2": "  - init_memory(doi: str, top_level_entity_name: str) -> str\n",
        "p3": (
            "  - create_Member(label: str, parent_iri: str) -> str\n"
            "  - add_containsMember(subject_iri: str, object_iri: str) -> str\n"
            "  - export_memory(doi: str, top_level_entity_name: str) -> str\n"
        ),
    }
    iterations = [
        {
            "iteration_number": 2,
            "kg_building_prompt": "p2",
            "responsibilities": {"classes": [], "object_properties": []},
        },
        {
            "iteration_number": 3,
            "kg_building_prompt": "p3",
            "responsibilities": {
                "classes": ["Member"],
                "object_properties": ["containsMember"],
            },
        },
    ]

    with pytest.raises(ValueError, match="does not publish"):
        build_onepass_kg_prompt(
            iterations=iterations,
            project_root=".",
            load_prompt=lambda path, _root: prompts[path],
            allowed_tool_names={
                "init_memory",
                "create_Member",
                "export_memory",
            },
        )


def test_resolve_generated_mcp_tool_surface_from_literal_manifests(
    tmp_path: Path,
) -> None:
    scripts_dir = tmp_path / "generated" / "scripts" / "demo"
    scripts_dir.mkdir(parents=True)
    (scripts_dir / "main.py").write_text("", encoding="utf-8")
    (scripts_dir / "demo_creation_entities.py").write_text(
        "__all__ = ['create_Member']\n", encoding="utf-8"
    )
    (scripts_dir / "demo_creation_relationships.py").write_text(
        "__all__ = []\n", encoding="utf-8"
    )
    (scripts_dir / "demo_creation_checks.py").write_text(
        "__all__ = ['check_members']\n", encoding="utf-8"
    )
    configs_dir = tmp_path / "configs"
    configs_dir.mkdir()
    (configs_dir / "mcp.json").write_text(
        json.dumps(
            {
                "demo_mcp": {
                    "command": "python",
                    "args": ["-m", "generated.scripts.demo.main"],
                }
            }
        ),
        encoding="utf-8",
    )

    assert resolve_generated_mcp_tool_surface(
        mcp_set_name="mcp.json",
        mcp_tools=["demo_mcp"],
        project_root=tmp_path,
    ) == {
        "init_memory",
        "export_memory",
        "create_Member",
        "check_members",
    }


def test_resolve_generated_mcp_relationship_contract(tmp_path: Path) -> None:
    scripts_dir = tmp_path / "generated" / "scripts" / "demo"
    scripts_dir.mkdir(parents=True)
    (scripts_dir / "main.py").write_text("", encoding="utf-8")
    for suffix in (
        "_creation_entities.py",
        "_creation_relationships.py",
        "_creation_checks.py",
    ):
        (scripts_dir / f"demo{suffix}").write_text("__all__ = []\n", encoding="utf-8")
    expected = {
        "creator_owned_relationships": {},
        "object_properties": [
            {
                "property_iri": "urn:hasMember",
                "domain_iris": ["urn:Root"],
                "range_iris": ["urn:Member"],
            }
        ],
    }
    (scripts_dir / "_relationship_contract.json").write_text(
        json.dumps(expected),
        encoding="utf-8",
    )
    configs_dir = tmp_path / "configs"
    configs_dir.mkdir()
    (configs_dir / "mcp.json").write_text(
        json.dumps(
            {
                "demo_mcp": {
                    "command": "python",
                    "args": ["-m", "generated.scripts.demo.main"],
                }
            }
        ),
        encoding="utf-8",
    )

    assert resolve_generated_mcp_relationship_contract(
        mcp_set_name="mcp.json",
        mcp_tools=["demo_mcp"],
        project_root=tmp_path,
    ) == expected


def test_resolve_generated_mcp_tool_surface_from_artifact_launcher(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "artifact"
    scripts_dir = artifact / "scripts" / "demo"
    scripts_dir.mkdir(parents=True)
    (scripts_dir / "main.py").write_text("", encoding="utf-8")
    (scripts_dir / "demo_creation_entities.py").write_text(
        "__all__ = ['create_Member']\n", encoding="utf-8"
    )
    (scripts_dir / "demo_creation_relationships.py").write_text(
        "__all__ = []\n", encoding="utf-8"
    )
    (scripts_dir / "demo_creation_checks.py").write_text(
        "__all__ = []\n", encoding="utf-8"
    )
    launcher = artifact / "_launch_demo_mcp.py"
    launcher.write_text("", encoding="utf-8")
    configs_dir = tmp_path / "configs"
    configs_dir.mkdir()
    (configs_dir / "mcp.json").write_text(
        json.dumps(
            {
                "demo_mcp": {
                    "command": "python",
                    "args": [str(launcher)],
                    "env": {"TWA_GENERATED_ARTIFACT_ROOT": str(artifact)},
                }
            }
        ),
        encoding="utf-8",
    )

    assert resolve_generated_mcp_tool_surface(
        mcp_set_name="mcp.json",
        mcp_tools=["demo_mcp"],
        project_root=tmp_path,
    ) == {"init_memory", "export_memory", "create_Member"}
