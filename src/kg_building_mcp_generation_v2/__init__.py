"""v2 copy of KG-building MCP generation.

Adds one structural compile rule: a public create_* may omit parent_iri
only for an extension adopted focus. Classes whose unique incoming parent
cannot be selected are nested-only.

    python -m src.kg_building_mcp_generation_v2 --from-existing-pack generated/runs/<pack> --output-root generated/runs/<new>
"""
