# tbox

Parse OWL/RDF Turtle and derive the contracts that used to be hand-copied
into domain config.

The T-Box is the semantic authority. Operational policy (reuse, MCP wiring,
slot shape) does not live in TTL annotation predicates.

## Files

| File | Role |
| --- | --- |
| `parser.py` | Classes, parents, properties, domains/ranges, per-class property maps |
| `property_contract.py` | Complete iteration-scoped property surface for one prompt |
| `runtime_contracts.py` | Ordered-member profile and required-link bindings from the T-Box |
