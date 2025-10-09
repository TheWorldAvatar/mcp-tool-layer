#!/usr/bin/env python3
"""
Top-to-bottom split of output.ttl into per-synthesis subgraphs.

Order of inclusion (hardcoded):
1) ChemicalSynthesis node S
2) Direct inputs/outputs of S via hasChemicalInput / hasChemicalOutput
   + IO attributes (amounts, materials, units, roles)
3) Direct steps of S via hasSynthesisStep
   + step-owned details (conditions, vessels, parameters, amounts, units, agents, reagents, solvents, etc.)
No traversal into other ChemicalSynthesis nodes.

Also writes a manifest JSON with URIs, labels, filenames, and triple counts.
"""

import os
import json
import re
from typing import List, Dict, Iterable
from rdflib import Graph, URIRef, Namespace

ONTOSYN = Namespace("https://www.theworldavatar.com/kg/OntoSyn/")
RDFS    = Namespace("http://www.w3.org/2000/01/rdf-schema#")
RDF     = Namespace("http://www.w3.org/1999/02/22-rdf-syntax-ns#")
XSD     = Namespace("http://www.w3.org/2001/XMLSchema#")

# ------------------ Hardcoded ontology levers ------------------

# Step classes to include
STEP_TYPES = {
    ONTOSYN.Add, ONTOSYN.Dissolve, ONTOSYN.Filter, ONTOSYN.HeatChill,
    ONTOSYN.Separate, ONTOSYN.Sonicate, ONTOSYN.Stir, ONTOSYN.Transfer
}

# From synthesis, only these outbound predicates define membership
SYNTH_EDGE_WHITELIST = {
    ONTOSYN.hasChemicalInput,
    ONTOSYN.hasChemicalOutput,
    ONTOSYN.hasSynthesisStep,
}

# From IO or Step, keep common descriptive predicates
COMMON_DESC_PREDICATES = {
    RDFS.label, RDF.type,
}

# IO attributes to chase (one or two hops)
IO_EDGE_WHITELIST = {
    ONTOSYN.hasAmount, ONTOSYN.hasQuantity, ONTOSYN.hasUnit,
    ONTOSYN.hasMaterial, ONTOSYN.referencesCompound, ONTOSYN.hasRole,
    ONTOSYN.hasName, ONTOSYN.hasIdentifier, ONTOSYN.usesContainer,
    ONTOSYN.hasNote,
}

# Step attributes to chase
STEP_EDGE_WHITELIST = {
    # Participation
    ONTOSYN.hasInput, ONTOSYN.hasOutput, ONTOSYN.hasAgent,
    ONTOSYN.hasReagent, ONTOSYN.hasSolvent, ONTOSYN.hasCatalyst,

    # Apparatus
    ONTOSYN.hasVessel, ONTOSYN.usesApparatus, ONTOSYN.usesInstrument,

    # Conditions / parameters
    ONTOSYN.hasCondition, ONTOSYN.hasTemperature, ONTOSYN.hasPressure,
    ONTOSYN.hasDuration, ONTOSYN.hasSpeed, ONTOSYN.hasRate,
    ONTOSYN.haspH, ONTOSYN.hasAtmosphere,

    # Data/notes
    ONTOSYN.hasObservation, ONTOSYN.hasNote,
}

# Parameter substructure (Amount/Quantity/Unit/Value)
PARAM_EDGE_WHITELIST = {
    ONTOSYN.hasValue, ONTOSYN.hasUnit, ONTOSYN.hasMinValue,
    ONTOSYN.hasMaxValue, ONTOSYN.hasMeanValue, ONTOSYN.hasStdDev,
}

# Classes that are safe to traverse details for (not synth)
SAFE_DETAIL_CLASSES = {
    ONTOSYN.ChemicalInput, ONTOSYN.ChemicalOutput,
    ONTOSYN.ChemicalAmount, ONTOSYN.QuantityValue,
    ONTOSYN.Unit, ONTOSYN.Temperature, ONTOSYN.Pressure,
    ONTOSYN.Duration, ONTOSYN.Speed, ONTOSYN.Rate, ONTOSYN.pH,
    ONTOSYN.Agent, ONTOSYN.Reagent, ONTOSYN.Solvent, ONTOSYN.Catalyst,
    ONTOSYN.Vessel, ONTOSYN.Apparatus, ONTOSYN.Instrument,
}

def _slugify(text: str, max_len: int = 80) -> str:
    text = re.sub(r"[^\w\-]+", "_", text.strip(), flags=re.UNICODE)
    text = re.sub(r"_+", "_", text)
    return text[:max_len].strip("_") or "no_label"

def _is_synthesis(node: URIRef, g: Graph) -> bool:
    return (node, RDF.type, ONTOSYN.ChemicalSynthesis) in g

def _has_type(node: URIRef, g: Graph, types: Iterable[URIRef]) -> bool:
    node_types = set(g.objects(node, RDF.type))
    return bool(node_types & set(types))

def get_top_entities_from_ttl(input_file: str = "output.ttl") -> List[Dict[str, str]]:
    g = Graph()
    g.parse(input_file, format="ttl")
    out: List[Dict[str, str]] = []
    for s in g.subjects(RDF.type, ONTOSYN.ChemicalSynthesis):
        labels = list(g.objects(s, RDFS.label))
        label = str(labels[0]) if labels else ""
        out.append({"uri": str(s), "label": label})
    return out

# ------------------ Core split with strict top-down traversal ------------------

def split_knowledge_graph(input_file: str = "output.ttl",
                          output_dir: str = ".",
                          output_prefix: str = "output") -> List[Dict]:
    g = Graph()
    try:
        g.parse(input_file, format="ttl")
        print(f"Loaded {len(g)} triples from {input_file}")
    except Exception as e:
        print(f"Error loading {input_file}: {e}")
        return []

    synths = list(g.subjects(RDF.type, ONTOSYN.ChemicalSynthesis))
    print(f"Found {len(synths)} synthesis objects")
    os.makedirs(output_dir, exist_ok=True)

    manifest: List[Dict] = []

    for idx, s in enumerate(synths, 1):
        print(f"Processing synthesis {idx}: {s}")

        sg = Graph()
        sg.bind("ontosyn", ONTOSYN)
        sg.bind("rdfs", RDFS)
        sg.bind("xsd", XSD)

        visited = set()  # nodes already pushed into sg
        queue   = []     # nodes to expand in later phases

        # ---- Phase 0: seed with the synthesis node S
        def _add_triples_for_subject(subj: URIRef, predicates: Iterable[URIRef] | None = None, allow_all: bool = False):
            """
            Add outbound triples for 'subj' into sg.
            If predicates is provided, only those predicates are considered.
            If allow_all is True, add all outbound triples.
            Returns list of URIRef objects encountered as objects.
            """
            new_objs: List[URIRef] = []
            for p, o in g.predicate_objects(subj):
                if not allow_all and predicates is not None and p not in predicates and p not in COMMON_DESC_PREDICATES:
                    continue
                sg.add((subj, p, o))
                if isinstance(o, URIRef):
                    new_objs.append(o)
            return new_objs

        # S itself: record label/type and whitelisted outbound edges
        visited.add(s)
        _add_triples_for_subject(s, predicates=SYNTH_EDGE_WHITELIST, allow_all=False)
        # ensure label/type are present
        for p, o in g.predicate_objects(s):
            if p in COMMON_DESC_PREDICATES:
                sg.add((s, p, o))

        # ---- Phase 1: collect direct IOs and Steps from S (do not traverse into other syntheses)
        inputs  = list(g.objects(s, ONTOSYN.hasChemicalInput))
        outputs = list(g.objects(s, ONTOSYN.hasChemicalOutput))
        steps   = list(g.objects(s, ONTOSYN.hasSynthesisStep))

        # Filter steps by class whitelist to be strict
        steps = [st for st in steps if _has_type(st, g, STEP_TYPES)]

        # Add IO nodes + their basic description
        for io in inputs + outputs:
            if isinstance(io, URIRef):
                visited.add(io)
                _add_triples_for_subject(io, predicates=IO_EDGE_WHITELIST, allow_all=False)
                # keep label/type
                for p, o in g.predicate_objects(io):
                    if p in COMMON_DESC_PREDICATES:
                        sg.add((io, p, o))

        # Add step nodes + their basic description
        for st in steps:
            if isinstance(st, URIRef):
                visited.add(st)
                _add_triples_for_subject(st, predicates=STEP_EDGE_WHITELIST, allow_all=False)
                for p, o in g.predicate_objects(st):
                    if p in COMMON_DESC_PREDICATES:
                        sg.add((st, p, o))
                queue.append(st)  # expand step-owned details later

        # ---- Phase 2: expand IO-owned details (one hop safe)
        def _expand_io_details(node: URIRef):
            for p, o in g.predicate_objects(node):
                if p in IO_EDGE_WHITELIST or p in COMMON_DESC_PREDICATES:
                    sg.add((node, p, o))
                    if isinstance(o, URIRef) and not _is_synthesis(o, g):
                        # one extra hop for parameter-like nodes
                        if o not in visited:
                            visited.add(o)
                            for pp, oo in g.predicate_objects(o):
                                if pp in PARAM_EDGE_WHITELIST or pp in COMMON_DESC_PREDICATES or pp in IO_EDGE_WHITELIST:
                                    sg.add((o, pp, oo))

        for io in inputs + outputs:
            if isinstance(io, URIRef):
                _expand_io_details(io)

        # ---- Phase 3: expand step-owned details (two hops safe)
        def _expand_step_details(step_node: URIRef, depth: int = 2):
            frontier = [(step_node, 0)]
            seen_local = set([step_node])
            while frontier:
                cur, d = frontier.pop(0)
                if d >= depth:
                    continue
                for p, o in g.predicate_objects(cur):
                    # keep only whitelisted detail edges and descriptions
                    if p in STEP_EDGE_WHITELIST or p in COMMON_DESC_PREDICATES:
                        sg.add((cur, p, o))
                        if isinstance(o, URIRef) and o not in seen_local:
                            # never traverse into other syntheses
                            if _is_synthesis(o, g):
                                continue
                            # traverse only safe detail classes or IO nodes directly tied to THIS synthesis
                            if (_has_type(o, g, SAFE_DETAIL_CLASSES)
                                or o in inputs or o in outputs):
                                seen_local.add(o)
                                frontier.append((o, d + 1))
                                # also attach parameter fan-out for measurement-like nodes
                                for pp, oo in g.predicate_objects(o):
                                    if pp in PARAM_EDGE_WHITELIST or pp in COMMON_DESC_PREDICATES:
                                        sg.add((o, pp, oo))

                # Prevent accidental incoming pulls from outside except from S itself
                for sbj, pp in g.subject_predicates(cur):
                    if sbj == s:
                        sg.add((sbj, pp, cur))
                    # ignore other incoming edges to avoid cross-contamination

        for st in steps:
            if isinstance(st, URIRef):
                _expand_step_details(st, depth=2)

        # ---- Emit subgraph
        labels = list(g.objects(s, RDFS.label))
        label  = str(labels[0]) if labels else f"synthesis_{idx}"
        slug   = _slugify(label)
        filename = f"{output_prefix}_{idx}__{slug}.ttl"
        filepath = os.path.join(output_dir, filename)

        try:
            sg.serialize(destination=filepath, format="ttl")
            print(f"  → Created {filepath} with {len(sg)} triples")
        except Exception as e:
            print(f"  → Error writing {filepath}: {e}")
            continue

        manifest.append({
            "index": idx,
            "uri": str(s),
            "label": label,
            "filename": filename,
            "triple_count": len(sg),
        })

    manifest_path = os.path.join(output_dir, f"{output_prefix}__manifest.json")
    try:
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
        print(f"\nManifest written: {manifest_path}")
    except Exception as e:
        print(f"Error writing manifest: {e}")

    print(f"\nCompleted splitting into {len(manifest)} files")
    return manifest

# ------------------ Summary and batch helpers (unchanged APIs) ------------------

def get_synthesis_summary(input_file: str = "output.ttl"):
    g = Graph()
    try:
        g.parse(input_file, format="ttl")
    except Exception as e:
        print(f"Error loading {input_file}: {e}")
        return

    print(f"Summary of {input_file}:")
    print(f"Total triples: {len(g)}")

    synths = list(g.subjects(RDF.type, ONTOSYN.ChemicalSynthesis))
    print(f"Synthesis objects: {len(synths)}")

    for i, s in enumerate(synths, 1):
        labels = list(g.objects(s, RDFS.label))
        label = str(labels[0]) if labels else "No label"
        inputs  = list(g.objects(s, ONTOSYN.hasChemicalInput))
        outputs = list(g.objects(s, ONTOSYN.hasChemicalOutput))
        steps   = list(g.objects(s, ONTOSYN.hasSynthesisStep))
        print(f"  {i}. {label}")
        print(f"     URI: {s}")
        print(f"     Inputs: {len(inputs)}, Outputs: {len(outputs)}, Steps: {len(steps)}")

def find_doi_folders(data_dir: str = "data") -> List[str]:
    if not os.path.exists(data_dir):
        print(f"Data directory not found: {data_dir}")
        return []
    doi_folders = []
    skip_folders = {'log', 'ontologies', 'subgraphs'}
    for item in os.listdir(data_dir):
        p = os.path.join(data_dir, item)
        if os.path.isdir(p) and item not in skip_folders and not item.startswith('.'):
            doi_folders.append(item)
    return sorted(doi_folders)

def process_all_dois(data_dir: str = "data", subgraphs_base: str = "data/subgraphs"):
    doi_folders = find_doi_folders(data_dir)
    if not doi_folders:
        print(f"No DOI folders found in {data_dir}")
        return
    print(f"Found {len(doi_folders)} DOI folder(s) to process")
    print("=" * 80)

    processed = 0
    skipped = 0
    for doi in doi_folders:
        print(f"\n📁 Processing DOI: {doi}")
        print("-" * 80)
        doi_path = os.path.join(data_dir, doi)
        output_ttl = os.path.join(doi_path, "output.ttl")
        if not os.path.exists(output_ttl):
            print(f"⏭️  Skipping {doi}: output.ttl not found")
            skipped += 1
            continue
        out_dir = os.path.join(subgraphs_base, doi)
        try:
            manifest = split_knowledge_graph(
                input_file=output_ttl,
                output_dir=out_dir,
                output_prefix=doi
            )
            if manifest:
                print(f"✅ Successfully split {doi} into {len(manifest)} subgraphs")
                processed += 1
            else:
                print(f"⚠️  No subgraphs created for {doi}")
                skipped += 1
        except Exception as e:
            print(f"❌ Error processing {doi}: {e}")
            skipped += 1

    print("\n" + "=" * 80)
    print(f"Summary: Processed {processed} DOI(s), Skipped {skipped} DOI(s)")
    print(f"Subgraphs saved to: {subgraphs_base}/")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Split knowledge graph by synthesis objects")
    parser.add_argument("--input", "-i", default="output.ttl", help="Input TTL file (default: output.ttl)")
    parser.add_argument("--output-dir", "-d", default=".", help="Output directory (default: current directory)")
    parser.add_argument("--output-prefix", "-o", default="output", help="Output file prefix (default: output)")
    parser.add_argument("--summary", "-s", action="store_true", help="Show summary only, don't split")
    parser.add_argument("--all", "-a", action="store_true", help="Process all DOI folders in data/ directory")
    parser.add_argument("--data-dir", default="data", help="Data directory containing DOI folders (default: data)")
    parser.add_argument("--subgraphs-dir", default="data/subgraphs", help="Base directory for subgraphs output (default: data/subgraphs)")
    args = parser.parse_args()

    if args.all:
        process_all_dois(args.data_dir, args.subgraphs_dir)
    elif args.summary:
        get_synthesis_summary(args.input)
    else:
        split_knowledge_graph(args.input, args.output_dir, args.output_prefix)
