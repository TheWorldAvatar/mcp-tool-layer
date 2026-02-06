### Grounding (Ontology IRI canonicalization) — end-to-end design

This directory implements a **two-stage grounding mechanism**:

- **Stage A (LLM-generated tooling)**: generate + run three scripts (A/B/C) that turn a target ontology’s SPARQL endpoint into a **local fuzzy-lookup index** and a **query interface**.
- **Stage B (deterministic grounding runtime)**: given a *source TTL*, extract candidate labels and use the target ontology’s MCP lookup server (backed by Script C + label index) to map source IRIs → canonical IRIs.

This README serves two purposes:
- **Developer guidance**: how to extend the grounding mechanism safely.
- **Paper methodology**: a clear, reproducible description of the grounding pipeline, inputs/outputs, and determinism.

---

### Quick glossary
- **Source TTL**: the TTL file whose IRIs you want to canonicalize (e.g., OntoSyn-derived output).
- **Target ontology / KG**: the canonical KG we ground *to* (e.g., OntoSpecies).
- **T-Box TTL**: schema TTL for the target ontology; used as the only “domain input” for script generation.
- **Label index**: locally cached label/identifier strings for instances in the target KG, used for fuzzy lookup.
- **Scripts A/B/C**: auto-generated python programs/modules that contain SPARQL query logic for the target KG.
- **MCP server**: exposes Script C’s capabilities as MCP tools for consumption by the grounding runtime.

---

### Repository wiring (what runs in this repo today)

The default grounding runtime is configured in:
- **MCP config**: `configs/grounding.json`
- **Server key**: `ontospecies`
- **Server implementation**: `src/mcp_servers/ontospecies/main.py`
- **Script C path (example)**: `sandbox/script_c_query_client.py`
- **Label cache dir (example)**: `data/grounding_cache/ontospecies/labels`

So grounding works by starting an OntoSpecies MCP server that loads Script C and points it at the local labels directory.

---

### Stage A — generation + data preparation (LLM involved)

Stage A is how we obtain the label cache + query module for a target ontology.

#### Inputs (Stage A)
- **T-Box TTL** (target ontology schema), path chosen by user.
- **Target SPARQL endpoint URL**, provided by user at runtime.
- **Ontology short name** (e.g., `"ontospecies"`), provided by user.

#### Outputs (Stage A)
- **Sampling JSON** (what predicates exist for “important” classes, and which are label-like)
- **Label cache** (JSONL files; potentially sharded) under `data/grounding_cache/<ontology>/labels/`
- **Script C** python module (contains SPARQL helpers + atomic query functions + fuzzy lookup over label cache)
- Optional: an MCP wrapper server for Script C (Agent 4), although OntoSpecies already has one in this repo.

---

### Agent 1 — generates Script A (sampling-only)

- **Agent file**: `src/agents/grounding/agent1_sampling_script_agent.py`
- **What it does**: prompts the LLM to emit **Script A**, a standalone python script that:
  - parses the T-Box to find OWL classes
  - queries the endpoint to estimate class instance counts
  - samples predicates actually present on instances
  - suggests “lookup predicates” (label/name/identifier-like)
  - optionally discovers **2-hop literal payload predicates** for object-valued lookup predicates

#### Hardcoded content in Agent 1 (generator)
- **Generation model default**: `"gpt-4.1"` (CLI overrideable via `--model`).
- **Prompt contract**: imposes the output JSON schema and the general SPARQL query shapes.
- **Safety convention**: prompt requests avoiding Python f-strings with SPARQL braces; prefers strings + `.format`.
- **Validation**: `generate_python_module_with_repair(...)` enforces basic importability and required substrings.

#### Inputs (to Agent 1)
- `--ontology-name <name>`
- `--ttl <path>`
- `--endpoint <url>`
- `--out <path>`: where to write **Script A** (python file)
- `--model <model>` (optional)

#### Outputs (from Agent 1)
- A python file (Script A) at the path provided by `--out`.

#### Outputs (when you execute Script A)
- A **sampling JSON** file, at the location Script A is invoked with (Script A has its own `--out` for JSON output).

---

### Agent 2 — generates Script B (label collection / batch downloader)

- **Agent file**: `src/agents/grounding/agent2_label_collection_script_agent.py`
- **What it does**: prompts the LLM to emit **Script B**, a standalone python script that:
  - counts `COUNT(DISTINCT ?s)` per class (progress metric)
  - pages subjects via **keyset pagination** (`FILTER(STR(?s) > last_s) ORDER BY STR(?s)`)
  - downloads labels for subjects using `VALUES ?s { ... }` batching
  - writes JSONL rows of `{classLocalName, s, label, source}` and resume state for recovery

#### Hardcoded content in Agent 2 (generator)
- **Generation model default**: `"gpt-4.1"` (CLI overrideable).
- **Prompt contract**: fixed IO schema for JSONL rows + resume fields; mandates non-overlapping batches.
- **Safety convention**: same SPARQL brace guidance (use `.format` with doubled braces).
- **Validation**: requires `def main` and script entrypoint presence.

#### Inputs (to Agent 2)
- `--ontology-name <name>`
- `--ttl <path>`
- `--endpoint <url>`
- `--sampling <path>` (sampling JSON produced by Script A execution)
- `--out <path>`: where to write **Script B** (python file)
- `--model <model>` (optional)

#### Outputs (from Agent 2)
- A python file (Script B) at the path provided by `--out`.

#### Outputs (when you execute Script B)
- **Label cache**: typically written to:
  - `data/grounding_cache/<ontology>/labels/**.jsonl`
- **Resume state**: typically written to a per-class resume directory (path depends on how Script B is invoked).

---

### Agent 3 — generates Script C (final query interface + fuzzy lookup)

- **Agent file**: `src/agents/grounding/agent3_query_interface_script_agent.py`
- **What it does**: prompts the LLM to emit **Script C**, a python module that provides:
  - `execute_sparql(query, ...)` helper
  - atomic query functions `list_*`, `get_*`, `lookup_*`
  - **local fuzzy lookup** functions that load the label cache produced by Script B and return ranked matches

#### Hardcoded content in Agent 3 (generator)
- **Generation model default**: `"gpt-4.1"` (CLI overrideable).
- **Prompt contract**:
  - Script C must not download labels; it only reads local JSONL cache under `LABELS_DIR`.
  - Requires `ENDPOINT_URL` constant in the output.
  - Requires `execute_sparql` symbol.
- **Validation**: checks `ENDPOINT_URL` and `def execute_sparql` are present; repairs invalid code via LLM loop.

#### Inputs (to Agent 3)
- `--ontology-name <name>`
- `--ttl <path>`
- `--endpoint <url>`
- `--sampling <path>`
- `--out <path>`: where to write **Script C** (python module)
- `--model <model>` (optional)

#### Outputs (from Agent 3)
- Script C module at `--out`.

#### Note on SPARQL queries
The SPARQL query strings used in atomic query functions are **generated by the LLM into Script C**. There are no separate static `.sparql` files in this grounding subsystem.

---

### Agent 4 — generates an MCP server wrapper for Script C (optional)

- **Agent file**: `src/agents/grounding/agent4_mcp_server_agent.py`
- **What it does**: prompts the LLM to generate `src/mcp_servers/<server_name>/main.py` that:
  - loads Script C dynamically by path
  - exposes selected Script C functions as MCP tools
  - writes tool-call logs
  - optionally writes/updates an MCP config JSON entry to launch the server

#### Hardcoded content in Agent 4 (generator)
- **Default config file to update**: `configs/chemistry.json` (CLI overrideable by `--config`).
- **Style reference**: uses `src/mcp_servers/ccdc/main.py` as the server style/template reference in the prompt.
- **Generation model default**: `"gpt-4.1"` (CLI overrideable).
- **Validation**: requires `FastMCP`, `mcp.run`, `def main`, and `->` return annotations.

#### Inputs (to Agent 4)
- `--script-c <path>`: Script C module to wrap
- `--server-name <name>`: output folder name under `src/mcp_servers/`
- `--config <path>` and `--config-key <key>` (optional)
- `--labels-dir <path>` (optional; forwarded to server)

#### Outputs (from Agent 4)
- `src/mcp_servers/<server_name>/main.py`
- A JSON config entry (in the chosen config file) to run that MCP server via stdio.

---

### Stage B — grounding runtime (deterministic; no LLM)

#### Grounding runtime entrypoint
- **Runtime file**: `src/agents/grounding/grounding_agent.py`

#### What it does
- **Extract candidates** from a source TTL:
  - selects URI subjects that have `rdfs:label` OR `ontosyn:hasAlternativeNames`
  - builds an ordered label list per subject (primary label first, then alternative names)
- **Call target MCP tools** to resolve each candidate:
  - get available fuzzy-lookup classes
  - run `fuzzy_lookup_<Class>(query)` over label cache
  - choose the best match deterministically: highest score → tie-break by IRI/class name
- **Materialize grounding**:
  - **replace mode**: rewrite all occurrences of mapped IRIs
  - **sameas mode**: add `<old> owl:sameAs <new>` triples
- Supports **batch mode** over a directory of TTLs, with an optional **internal merge** pass that canonicalizes duplicate entities across files before grounding.

#### Hardcoded content in grounding runtime (domain coupling)
- **Default example TTL path**:
  - `evaluation/data/merged_tll/0e299eb4/0e299eb4.ttl`
- **Default MCP config**:
  - `configs/grounding.json`
- **Default MCP server key**:
  - `"ontospecies"`
- **Source TTL label predicates**:
  - always reads `rdfs:label`
  - additionally reads `ONTOSYN.hasAlternativeNames` (OntoSyn-specific)

#### Inputs (Stage B)
- `--ttl <path>` or `--batch-dir <dir>`
- `--mcp-config <path>` (default: `configs/grounding.json`)
- `--server-key <key>` (default: `ontospecies`)
- `--enable-fuzzy` / `--no-fuzzy`
- `--write-grounded-ttl` + `--grounded-ttl-out` (optional)
- `--grounding-mode sameas|replace` (default: `replace`)

#### Outputs (Stage B)
- Prints a stable JSON payload to stdout containing:
  - `mapping`: `{source_iri: grounded_iri | null}`
  - `details`: per-entity evidence of attempted lookups
- Optionally writes a grounded TTL:
  - default: `<input_stem>_grounded.ttl` next to the input TTL

---

### The OntoSpecies MCP server (how Script C is used at runtime)

- **MCP server**: `src/mcp_servers/ontospecies/main.py`
- **Configured by**: `configs/grounding.json`
- **What it does**:
  - loads Script C from the file path provided via `--script-c`
  - optionally overrides Script C’s `LABELS_DIR` via `--labels-dir`
  - registers MCP tools (e.g., `execute_sparql`, `fuzzy_lookup_*`, `lookup_*`)
  - logs tool calls to `data/log/ontospecies_mcp.log` (via `models.locations.DATA_LOG_DIR`)

The grounding runtime calls this MCP server; it does not import Script C directly.

---

### Concrete example artifacts in this repo

The repo currently includes example generated artifacts under `sandbox/`:
- `sandbox/script_a_sampling.py` (Script A example)
- `sandbox/script_b_label_download.py` (Script B example)
- `sandbox/script_c_query_client.py` (Script C example)

These are helpful to understand the generated code shape, but they may be regenerated.

---

### Developer notes (how to extend safely)

- **Minimize runtime hardcoding**:
  - Today, candidate extraction includes an OntoSyn-specific predicate (`hasAlternativeNames`).
  - To generalize, make “candidate label predicates” configurable (CLI or config file).
- **Keep determinism**:
  - Grounding selection is deterministic by design (stable ordering + tie-break rules).
  - Preserve this property when adding new heuristics (always define stable tie-breaks).
- **Treat scripts A/B/C as build artifacts**:
  - Consider writing them under `src/agents/grounding/generated/<ontology>/...` and version them only for reproducibility snapshots.
- **If you want stable SPARQL**:
  - Today, SPARQL lives inside generated scripts; future work could move templates to checked-in `.sparql` files and have Script C load them.

---

### Paper methodology (suggested wording)

We ground extracted entities by mapping their labels to canonical entities in a reference knowledge graph (OntoSpecies). To support scalable grounding, we first generate a SPARQL sampling script (A), a label-harvesting script (B), and a query/fuzzy-lookup module (C) using an LLM conditioned only on the target ontology schema (T-Box) and endpoint evidence. Script B builds a local label index for selected classes by downloading label-like predicates from the target KG. Script C performs deterministic fuzzy matching over the local label index and provides SPARQL accessors. During grounding, we extract candidate labels from the source TTL (primary `rdfs:label` plus alternative name predicates where available), query the OntoSpecies MCP lookup server (wrapping Script C), and deterministically select the best-matching canonical IRI. We then either rewrite IRIs in the source TTL or add `owl:sameAs` links to preserve provenance.
