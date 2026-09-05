from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from src.agents.scripts_and_prompts_generation import agentic_generation_validation
from src.agents.scripts_and_prompts_generation.agentic_generation_validation import (
    _expected_tool_surface_report,
    _is_structured_rejection,
)
from src.agents.scripts_and_prompts_generation.artifact_surface_contract import (
    derive_main_surface_contract,
)
from src.agents.scripts_and_prompts_generation.pure_llm_generation import (
    _artifact_generation_contract,
    _generation_task,
)


def _write_siblings(scripts: Path) -> None:
    (scripts / "synthetic_creation_entities.py").write_text(
        "__all__ = ['create_Owned']\n"
        "def create_Owned(label: str) -> str:\n"
        "    return label\n",
        encoding="utf-8",
    )
    (scripts / "synthetic_creation_relationships.py").write_text(
        "__all__ = []\n", encoding="utf-8"
    )
    (scripts / "synthetic_creation_checks.py").write_text(
        "__all__ = []\n", encoding="utf-8"
    )


def _write_main(scripts: Path, *, omit: str = "", extra: bool = False) -> None:
    lifecycle = {
        "init_memory": "def init_memory() -> str:\n    return '{}'\n",
        "export_memory": "def export_memory() -> str:\n    return '{}'\n",
    }
    lines = [
        "from fastmcp import FastMCP\n",
        "from .synthetic_creation_entities import create_Owned\n",
        "mcp = FastMCP('synthetic')\n",
    ]
    for name, source in lifecycle.items():
        lines.append(source)
        if name != omit:
            lines.append(f"mcp.tool(name='{name}')({name})\n")
    if omit != "create_Owned":
        lines.append("mcp.tool(name='create_Owned')(create_Owned)\n")
    if extra:
        lines.extend(
            [
                "def convenience() -> str:\n    return '{}'\n",
                "mcp.tool(name='convenience')(convenience)\n",
            ]
        )
    lines.append("if __name__ == '__main__':\n    mcp.run()\n")
    (scripts / "main.py").write_text("\n".join(lines), encoding="utf-8")


def _context(scripts: Path) -> SimpleNamespace:
    return SimpleNamespace(
        scripts_dir=str(scripts),
        ontology=SimpleNamespace(name="synthetic", role="main"),
    )


def test_main_contract_is_derived_from_current_sibling_manifests(tmp_path: Path) -> None:
    _write_siblings(tmp_path)
    context = _context(tmp_path)
    context.parsed = {"classes": {"PriorClass": {"iri": "urn:prior"}}}
    context.contract = {
        "relationship_tool_contracts": {
            "priorPredicate": {"public_tool": "add_priorPredicate"}
        },
        "ordered_member_profile": {"ordered_member_classes": ["PriorClass"]},
    }

    surface = _artifact_generation_contract(context, tmp_path / "main.py")

    assert surface["expected_mcp_tools"] == [
        "create_Owned",
        "export_memory",
        "init_memory",
    ]
    assert "PriorClass" not in str(surface)
    assert "priorPredicate" not in str(surface)
    assert "check_ordered_members" not in surface["expected_mcp_tools"]


def test_main_prompt_contains_read_only_sibling_source_and_signatures(
    tmp_path: Path,
) -> None:
    _write_siblings(tmp_path)
    context = _context(tmp_path)
    context.output_root = str(tmp_path)
    context.parsed = {"classes": {}}
    context.contract = {}
    context.ontology.ttl_file = "synthetic.ttl"

    prompt = _generation_task(
        context=context,
        report={"stage_ok": False, "failures": ["main.py is empty"]},
        round_index=1,
        generate_scripts=True,
        generate_prompts=False,
        target=tmp_path / "main.py",
    )

    assert '"status": "read_only"' in prompt
    assert "def create_Owned(label: str) -> str:" in prompt
    assert '"signature": "(label: str) -> str"' in prompt
    assert "parse an envelope before using its iri field" in prompt
    assert "from fastmcp import FastMCP" in prompt
    assert "never implement a custom MCP registry" in prompt


def test_structured_rejection_accepts_json_and_mapping_envelopes() -> None:
    assert _is_structured_rejection('{"status":"rejected","code":"bad_range"}')
    assert _is_structured_rejection({"status": "error"})
    assert not _is_structured_rejection('{"status":"ok"}')


def test_exact_runtime_manifest_surface_passes(tmp_path: Path) -> None:
    _write_siblings(tmp_path)
    (tmp_path / "_fixed_rdf_runtime.py").write_text(
        "def init_memory(doi: str, top_level_entity_name: str) -> str:\n"
        "    return '{}'\n"
        "def export_memory(doi: str, top_level_entity_name: str) -> str:\n"
        "    return '{}'\n",
        encoding="utf-8",
    )
    _write_main(tmp_path)
    main = tmp_path / "main.py"
    text = main.read_text(encoding="utf-8")
    text = text.replace(
        "from fastmcp import FastMCP\n",
        "from fastmcp import FastMCP\n"
        "from ._fixed_rdf_runtime import init_memory, export_memory\n",
    )
    start = text.index("def init_memory()")
    end = text.index("def materialize_hints") if "def materialize_hints" in text else -1
    if end < 0:
        end = text.index("mcp.tool(name='create_Owned')")
    lifecycle_source = text[start:end]
    for name in ("init_memory", "export_memory"):
        marker = f"def {name}()"
        if marker in lifecycle_source:
            fn_start = text.index(marker)
            registration = f"mcp.tool(name='{name}')({name})\n"
            fn_end = text.index(registration, fn_start)
            text = text[:fn_start] + text[fn_end:]
    main.write_text(text, encoding="utf-8")

    failures, _, _ = _expected_tool_surface_report(_context(tmp_path))

    assert failures == []


@pytest.mark.parametrize(
    ("omit", "extra", "needle"),
    [
        ("create_Owned", False, "missing=['create_Owned']"),
        ("", True, "extra=['convenience']"),
    ],
)
def test_runtime_surface_rejects_missing_and_extra_tools(
    tmp_path: Path, omit: str, extra: bool, needle: str
) -> None:
    _write_siblings(tmp_path)
    _write_main(tmp_path, omit=omit, extra=extra)

    failures, _, _ = _expected_tool_surface_report(_context(tmp_path))

    assert any(needle in failure for failure in failures)


def test_runtime_surface_rejects_missing_stdio_entry_point(tmp_path: Path) -> None:
    _write_siblings(tmp_path)
    _write_main(tmp_path)
    main = tmp_path / "main.py"
    main.write_text(
        main.read_text(encoding="utf-8").replace(
            "if __name__ == '__main__':\n    mcp.run()\n",
            "",
        ),
        encoding="utf-8",
    )

    failures, _, _ = _expected_tool_surface_report(_context(tmp_path))

    assert any("must keep stdio service alive" in failure for failure in failures)


def test_safe_tool_name_cannot_hide_generic_runtime_handler(tmp_path: Path) -> None:
    _write_siblings(tmp_path)
    (tmp_path / "_fixed_rdf_runtime.py").write_text(
        "def generic(label: str) -> str:\n    return label\n", encoding="utf-8"
    )
    _write_main(tmp_path, omit="create_Owned")
    main = tmp_path / "main.py"
    main.write_text(
        main.read_text(encoding="utf-8")
        + "\nfrom ._fixed_rdf_runtime import generic\n"
        + "mcp.tool(name='create_Owned')(generic)\n",
        encoding="utf-8",
    )

    failures, _, _ = _expected_tool_surface_report(_context(tmp_path))

    assert any("handler provenance" in failure for failure in failures)


def test_three_startups_must_have_stable_inventory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_siblings(tmp_path)
    (tmp_path / "main.py").write_text("", encoding="utf-8")
    expected = derive_main_surface_contract(tmp_path)["expected_mcp_tools"]
    calls = 0

    def fake_import(*_args: object) -> SimpleNamespace:
        nonlocal calls
        calls += 1
        names = expected if calls != 2 else expected + ["unstable"]
        tools = {
            name: SimpleNamespace(
                fn=lambda: None,
                parameters={"type": "object", "properties": {}},
            )
            for name in names
        }

        async def get_tools() -> dict[str, SimpleNamespace]:
            return tools

        return SimpleNamespace(mcp=SimpleNamespace(get_tools=get_tools))

    monkeypatch.setattr(
        agentic_generation_validation,
        "_import_generated_main_module",
        fake_import,
    )

    failures, _, _ = _expected_tool_surface_report(_context(tmp_path))

    assert any("unstable MCP tool surface" in failure for failure in failures)
