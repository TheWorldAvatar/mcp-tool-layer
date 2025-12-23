import json
import os
import argparse
from pathlib import Path
import sys

# Ensure project root is on sys.path so 'scripts' package imports work when run directly
_repo_root = Path(__file__).resolve().parents[1]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from rdflib import Graph

from scripts.output_conversion_ttl_to_json.ttl_merge import (
    merge_for_hash,
    build_link_graph,
    remove_orphan_entities,
)
from scripts.output_conversion_ttl_to_json.ontosynthesis_cbu_conversion import (
    build_cbu_json_from_graph,
)
from scripts.output_conversion_ttl_to_json.ontosynthesis_characterisation_conversion import (
    get_namespaces as char_get_namespaces,
    query_characterisation_devices as char_query_devices,
    query_characterisation_data as char_query_data,
    build_json_structure as char_build_json,
)
from scripts.output_conversion_ttl_to_json.ontosynthesis_chemicals_conversion import (
    get_namespaces as chem_get_namespaces,
    query_synthesis_procedures as chem_query_syntheses,
    query_all_ontomops_data as chem_query_ontomops,
    build_json_structure as chem_build_json,
)
from scripts.output_conversion_ttl_to_json.ontosynthesis_step_conversion import (
    get_namespaces as step_get_namespaces,
    query_chemical_syntheses as step_query_syntheses,
    query_syntheses_via_steps as step_query_syntheses_fallback,
    build_json_structure as step_build_json,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Merge TTLs and generate debug/cbu outputs")
    parser.add_argument("--actual-model", action="store_true", help="Disable heuristic label/filename-based linking")
    parser.add_argument("--debug", action="store_true", help="Include debug fields (IRIs) in JSON outputs where supported")
    parser.add_argument("--hash", type=str, help="Process only the specified hash (e.g., '3a4646d4')")
    # Allow running only one stage when specified
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--merge", action="store_true", help="Only perform merge/linking stage and write TTLs")
    group.add_argument("--conversion", action="store_true", help="Only perform conversion stage from existing merged TTLs")
    args = parser.parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    data_root = repo_root / "data"
    
    # Discover all hash directories from data folder
    hash_dirs = [p for p in data_root.iterdir() if p.is_dir() and not p.name.startswith('.')]
    hash_values = sorted([p.name for p in hash_dirs])
    
    # Filter to specific hash if requested
    if args.hash:
        if args.hash in hash_values:
            hash_values = [args.hash]
            print(f"Processing only hash: {args.hash}")
        else:
            print(f"Error: Hash '{args.hash}' not found in {data_root}")
            print(f"Available hashes: {', '.join(hash_values[:10])}{'...' if len(hash_values) > 10 else ''}")
            return
    else:
        print(f"Found {len(hash_values)} hash directories in {data_root}")

    output_root = repo_root / "evaluation" / "data" / "merged_tll"
    output_root.mkdir(parents=True, exist_ok=True)

    do_merge = args.merge or (not args.merge and not args.conversion)
    do_conversion = args.conversion or (not args.merge and not args.conversion)

    for hash_value in hash_values:
        hash_dir = data_root / hash_value
        if not hash_dir.exists():
            # skip silently if data for hash does not exist
            continue

        out_dir = output_root / hash_value
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{hash_value}.ttl"

        pruned_g: Graph | None = None

        if do_merge:
            g: Graph = merge_for_hash(
                hash_value=hash_value,
                data_root=str(data_root),
                add_links=True,
                enable_heuristic_linking=not args.__dict__.get("actual_model", False),
            )

            # Export link-only subgraph for debugging
            link_g = build_link_graph(g)
            link_path = out_dir / "link.ttl"
            link_g.serialize(destination=str(link_path), format="turtle")

            # Prune orphans in the full graph (not applied to link.ttl)
            pruned_g = remove_orphan_entities(g)
            pruned_g.serialize(destination=str(out_path), format="turtle")

        if do_conversion:
            # Load from existing merged TTL if not produced in this run
            if pruned_g is None:
                if not out_path.exists():
                    # No TTL to convert from; skip
                    continue
                pruned_g = Graph()
                pruned_g.parse(str(out_path), format="turtle")

            # Emit CBU JSON per hash, built from pruned graph
            cbu_json = build_cbu_json_from_graph(pruned_g)
            cbu_out_path = out_dir / "cbu.json"
            with open(cbu_out_path, "w", encoding="utf-8") as f:
                json.dump(cbu_json, f, indent=2, ensure_ascii=False)

            # Emit Characterisation JSON per hash from pruned graph (OntoSpecies side)
            namespaces = char_get_namespaces(pruned_g)
            devices = char_query_devices(pruned_g, namespaces)
            characterisations = char_query_data(pruned_g, namespaces)
            char_json = char_build_json(devices, characterisations)
            char_out_path = out_dir / "characterisation.json"
            with open(char_out_path, "w", encoding="utf-8") as f:
                json.dump(char_json, f, indent=2, ensure_ascii=False)

            # Emit Chemicals JSON per hash
            chem_ns = chem_get_namespaces(pruned_g)
            ontomops_info = chem_query_ontomops(pruned_g, chem_ns)
            syntheses = chem_query_syntheses(pruned_g, chem_ns)
            chem_json = chem_build_json(pruned_g, chem_ns, syntheses, ontomops_info, debug=args.debug)
            chem_out_path = out_dir / "chemicals.json"
            with open(chem_out_path, "w", encoding="utf-8") as f:
                json.dump(chem_json, f, indent=2, ensure_ascii=False)

            # Emit Steps JSON per hash (now simplified to only chemicals per synthesis)
            step_ns = step_get_namespaces(pruned_g)
            step_syntheses = step_query_syntheses(pruned_g, step_ns)
            if not step_syntheses:
                step_syntheses = step_query_syntheses_fallback(pruned_g, step_ns)
            steps_json = step_build_json(pruned_g, step_ns, step_syntheses, debug=args.debug)
            steps_out_path = out_dir / "steps.json"
            with open(steps_out_path, "w", encoding="utf-8") as f:
                json.dump(steps_json, f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()


