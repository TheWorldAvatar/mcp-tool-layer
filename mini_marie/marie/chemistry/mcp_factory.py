"""Build a per-namespace chemistry FastMCP server."""

from __future__ import annotations

from fastmcp import FastMCP

from mini_marie.marie.chemistry.competency_tools import register_competency_tools
from mini_marie.marie.chemistry import operations as op
from mini_marie.marie.chemistry.limits import ONLINE_PROBE_LIMIT
from mini_marie.marie.chemistry.registry import NAMESPACES, tbox_paths
from mini_marie.marie.chemistry.tbox_index import list_classes_text, list_properties_text
from mini_marie.marie.chemistry.workflow_mcp import (
    list_competency_workflows_text,
    replay_competency_offline,
    run_competency_online,
)


def create_chemistry_mcp(namespace: str) -> FastMCP:
    if namespace not in NAMESPACES:
        raise KeyError(f"Unknown chemistry namespace: {namespace}")
    meta = NAMESPACES[namespace]
    mcp = FastMCP(name=f"chemistry-{namespace}")
    paths = tbox_paths(namespace)

    @mcp.prompt(name="instruction")
    def instruction_prompt() -> str:
        extra = ""
        if namespace == "ontokin":
            extra = "\nOntoKin extension: traverse_mechanism_reactions."
        elif namespace == "ontocompchem":
            extra = "\nOntoCompChem extension: query_calculation_results."
        elif namespace == "ontozeolite":
            extra = "\nOntoZeolite extension: query_zeolite_property (also uses oc: OntoCrystal)."
        elif namespace == "ontomops":
            extra = "\nOntoMOPs: use ontomops_instance_routing; instance data on twa-mops/mof-twa."
        return (
            f"Chemistry KG namespace `{namespace}` ({meta['label']}).\n"
            f"SPARQL: https://theworldavatar.io/chemistry/blazegraph/namespace/{namespace}/sparql\n"
            f"Use GET queries with User-Agent curl/8.0.\n"
            f"T-Box: mini_marie/docs/resources/tbox/{namespace}/\n"
            f"Marie example questions: mini_marie/docs/resources/marie/marie_competency_questions.md\n"
            f"Coverage: mini_marie/marie/chemistry/COMPETENCY_COVERAGE.md\n"
            f"All live SELECT probes return at most {ONLINE_PROBE_LIMIT} rows.\n"
            f"Generic competency tools: lookup_individuals, get_linked_values, "
            f"filter_by_literal, count_instances.{extra}\n"
            f"Discover class/property local names via list_ontology_classes / list_ontology_properties.\n"
            f"Competency workflows: list_competency_workflows, run_competency_online, "
            f"replay_competency_offline (offline replay is for orchestrator, not the LLM).\n"
            f"{meta.get('endpoint_note', '')}"
        )

    @mcp.tool(name="get_namespace_info", description="Endpoint, ontology prefix, and T-Box file stats")
    def get_namespace_info() -> str:
        return op.namespace_info(namespace)

    @mcp.tool(name="list_ontology_classes", description="OWL classes from local T-Box file(s)")
    def list_ontology_classes(limit: int = 40) -> str:
        return list_classes_text(paths, limit=min(limit, 200))

    @mcp.tool(name="list_ontology_properties", description="OWL properties from local T-Box file(s)")
    def list_ontology_properties(limit: int = 40) -> str:
        return list_properties_text(paths, limit=min(limit, 200))

    @mcp.tool(name="get_live_triple_count", description="COUNT(*) on the live Blazegraph namespace")
    def get_live_triple_count() -> str:
        return op.live_triple_count(namespace)

    @mcp.tool(name="get_top_instance_types", description="Most common rdf:type values in live data")
    def get_top_instance_types(limit: int = ONLINE_PROBE_LIMIT) -> str:
        return op.top_instance_types(namespace, limit=min(limit, ONLINE_PROBE_LIMIT))

    @mcp.tool(name="sample_live_triples", description="Sample A-box triples from live endpoint")
    def sample_live_triples(limit: int = ONLINE_PROBE_LIMIT) -> str:
        return op.sample_triples(namespace, limit=min(limit, ONLINE_PROBE_LIMIT))

    register_competency_tools(mcp, namespace)

    @mcp.tool(
        name="list_competency_workflows",
        description="List Marie chemistry competency workflows (workflow_id + title)",
    )
    def list_competency_workflows() -> str:
        return list_competency_workflows_text()

    @mcp.tool(
        name="run_competency_online",
        description=(
            "Run a chemistry competency workflow online (LIMIT 5). "
            "Returns compact TSV + recording_path for offline replay."
        ),
    )
    def run_competency_online_tool(workflow_id: str, online_limit: int = ONLINE_PROBE_LIMIT) -> str:
        return run_competency_online(workflow_id, online_limit=online_limit)

    @mcp.tool(
        name="replay_competency_offline",
        description="Replay chemistry competency workflow from recording_path at full cache tier.",
    )
    def replay_competency_offline_tool(recording_path: str) -> str:
        return replay_competency_offline(recording_path)

    return mcp
