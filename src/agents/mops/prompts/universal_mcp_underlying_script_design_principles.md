# Universal MCP underlying-script design principles

This file is intentionally **domain-agnostic**. It is used by the script generation agents to create ontology-specific MCP “underlying scripts” (`*_creation.py`) and their corresponding `main.py` MCP server wrappers.

## Goals

- Produce **reliable**, **idempotent** tools that are safe to call repeatedly.
- Prefer **simple, composable** tool functions over large, monolithic actions.
- Make functions **easy to use** by accepting primitive values and performing internal resolution (the “auto-create / find-or-create” pattern where appropriate).

## Core principles

### 1) No side effects before validation

- Validate required inputs and obvious invariants first.
- If a call cannot succeed, fail early with a clear exception/message.

### 2) Deterministic identifiers and deduplication

- Prefer stable, deterministic IRIs/IDs when you can (e.g., content-hash or canonical label hashing).
- If entities should not duplicate, implement `_find_or_create_*` helpers that:
  - search by identifying attributes
  - reuse existing nodes when a match exists
  - otherwise create exactly one new node

### 3) Separation of concerns

- Keep graph construction utilities (IRI minting, literals, validation helpers) separate from domain entity creation functions.
- Keep IO (reading/writing TTL/JSON) separate from mutation logic when possible.

### 4) Small, testable tools

- Prefer smaller functions with clear signatures:
  - `create_<Entity>(...) -> iri` (or path)
  - `link_<A>_<B>(a_iri, b_iri, ...)`
  - `load_graph(path)`, `save_graph(g, path)`
- Avoid hidden global state unless the runtime explicitly requires it.

### 5) Clear error messages

- Errors should include:
  - what input was invalid
  - what was expected
  - how to fix it (when obvious)

### 6) Environment-agnostic paths

- Use `Path` / `os.path.join` and avoid hardcoding OS-specific separators.
- Treat the repository root as the working directory unless explicitly configured.


