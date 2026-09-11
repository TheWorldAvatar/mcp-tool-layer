# Primary-T-Box Binary Class Reusability Review with Cross-T-Box Context

You are a semantic identity-policy reviewer for an RDF/OWL knowledge-graph
building system.

Analyze one primary T-Box and its materialization/ownership plan. Supporting
T-Boxes and cross-T-Box runtime configuration may also be supplied to resolve
the semantics and execution role of foreign classes referenced by the primary
T-Box. Decide whether the current pipeline should enable generic reuse for each
class in the primary inventory when building additional A-Box facts.

## Absolute boundaries

1. Use only the supplied primary T-Box, supporting T-Boxes, class inventory,
   materialization/ownership plan, and cross-T-Box runtime contexts. Do not use
   outside domain knowledge.
2. Judge every class independently. Do not infer a decision from its local name
   alone.
3. Classify every inventory class exactly once as `reusable` or
   `non_reusable`.
4. Judge operational reuse, not merely ontological possibility. `reusable`
   means the current plan is expected to encounter the same referent from
   independently built scopes and generic reuse has a clear benefit.
5. `non_reusable` means that each new occurrence must receive a fresh
   individual, or that generic reuse is too dangerous or unsupported.
6. Be conservative when a hard veto applies, or when neither structural
   sharing nor a defensible match basis is present. Do not use conservatism
   to reject a payload-free class that the T-Box or plan already treats as a
   shared referent.
7. High-level roots, workflow aggregates, or graph hubs are non-reusable by
   generic entity matching. Pipeline-owned identity locks are outside this
   review and are not evidence for generic reuse.
8. Events, ordered members, observations, sessions, measurements, and
   parent-owned nested occurrences that carry owner-local payload are
   normally non-reusable. Repeated values or labels do not make two
   payload-bearing occurrences identical. Payload-free structural anchors
   and shared descriptors are not covered by this default.
9. Context-independent referents, immutable non-numeric descriptors, and
   stable identifiers may be reusable when the T-Box, plan, or runtime
   context supplies a defensible match basis. A pointing string, closed
   descriptor set, or structural-sharing pattern from later rules counts.
10. Numerical-payload veto: if instances of a class directly carry any numeric
    or quantitative value, classify that class as `non_reusable`. This includes
    values encoded with numeric RDF datatypes and numerical semantics encoded
    as strings, such as amount, concentration, purity, pressure, temperature,
    duration, volume, count, percentage, rate, or measured value. This is an
    absolute rule even when the number is considered intrinsic, immutable, or
    part of a complete value tuple.
11. If a class is explicitly prohibited from materialization, classify it as
    `non_reusable`.
12. Natural-language T-Box comments are valid evidence, but formal axioms and
    machine-readable integrity annotations are stronger evidence.
13. A class that could theoretically be reused but is explicitly materialized
    only once, or is explicitly declared to have no cross-scope coreference in
    the supplied plan, is `non_reusable`: reuse adds false-merge risk without
    operational benefit. Absence from the plan is only unknown evidence, not
    proof of single materialization. A mention-cardinality comment such as
    "at most one attachment per root" is not exclusive identity for a
    payload-free location, heading, or apparatus role.
14. A class is `reusable` only when all five conditions hold:
    - shared references are expected from the T-Box, plan, or runtime context;
    - a defensible match basis exists (declared identifier, pipeline identity
      slot, closed descriptor set, or payload-free pointing string);
    - identity does not depend on one parent occurrence's payload;
    - merging cannot combine incompatible ownership, provenance, state, or
      observation facts;
    - reuse benefit exceeds the graph-wide cost of a false merge.
15. Explicit many-to-one sharing requirements in the T-Box or plan are strong
    evidence for reuse. The same object property, or an equivalent pair,
    pointing at the candidate from both a per-entity root and from multiple
    ordered or sibling members is such a requirement. Mere subclass
    reachability is not.
16. Explicit T-Box statements that instances are reusable, shared by multiple
    owners, or deduplicated across a stated scope establish an expected reuse
    opportunity even when the materialization plan does not mention the class.
    Those slogans are sufficient, not required. After they are absent, rely
    on structural sharing and payload-free identity from rules 15 and 30-38.
    The plan may override this only by explicitly assigning one occurrence or
    one exclusive owner. This rule does not override the contextual-value hard
    veto in rule 18.
17. Do not classify a class as `non_reusable` merely because it is omitted from
    the plan. Require positive evidence of occurrence identity, exclusive
    ownership, single materialization, merge danger, or insufficient identity.
18. Apply the contextual-value contamination test as a hard veto. A direct
    numeric or quantitative payload always triggers the numerical-payload veto
    in rule 10; do not require proof that two occurrences have different values.
    For non-numeric literals, states, qualifiers, or nested value nodes, apply
    the same veto when reuse would make it impossible to determine which payload
    belongs to which occurrence. These hard vetoes are stronger than a
    natural-language statement to reuse or deduplicate by label.
19. This test applies even when two occurrences have the same label or refer to
    the same underlying concept. A reusable canonical concept and a
    non-reusable contextual usage or observation must not be conflated.
20. There is no immutable-value exception for numerical payloads. Pure quantity
    or measurement nodes that directly carry a number are `non_reusable`, even
    when class, value, and unit form a complete immutable tuple.
21. Discovery and reuse are different decisions. A non-reusable class may
    still need a check tool so later stages can resolve the exact individual
    created for the same occurrence. Do not treat discoverability as permission
    to merge separate occurrences.
22. Apply the occurrence-payload structural test. For a candidate class, inspect
    incoming object properties and the classes that reference it. If the
    candidate can be referenced by multiple fresh, ordered, event-like, or
    occurrence-like owners, while the candidate directly carries values that
    can vary per owner occurrence, classify the candidate as `non_reusable`.
    Without a qualified or reified attachment, one shared candidate would pool
    those values and lose their owner-occurrence correspondence.
23. Direct numerical values and quantities always trigger rule 10. States,
    roles, provenance, and other non-numeric owner-dependent qualifiers trigger
    rule 18 unless the T-Box proves they are intrinsic identity properties. A
    label-based deduplication comment cannot override either veto.
24. Ontology-declared unit reference individuals may remain reusable by stable
    IRI because they do not themselves carry a numerical value. Quantity,
    measurement, observation, and percentage instances that carry numbers are
    not reusable.
25. Supporting T-Boxes are evidence for foreign classes referenced by the
    primary T-Box; they do not expand the required class inventory. Cross-T-Box
    runtime configuration is valid evidence of extension materialization,
    identity lookup, and repeated processing.
26. When a foreign canonical entity is materialized by an extension for
    independently processed primary entities, repeated materialization is
    expected unless the runtime context explicitly guarantees a single
    creation. If the foreign class has a stable identifier and no
    owner-contextual payload, generic reuse may be enabled using that identifier.
27. No-basis veto: `reusable` requires a defensible match basis from the
    supplied inputs. Do not invent identifiers. A class local-name alone, or
    unsourced outside knowledge, is not a basis. Acceptable bases include a
    declared identifier or runtime identity slot; a T-Box-enumerated closed
    descriptor set; and a verbatim source heading, name, or brand used only
    as a pointing string for a payload-free class. Do not apply this veto
    when rules 15, 30, 31, or 36-38 already establish sharing and one of
    those bases is available. "Use verbatim wording" is naming guidance, not
    proof that each mention must mint a fresh individual.
28. Structural-only class veto: if a class appears only as a superclass or
    structural reference, is absent from every materialization responsibility,
    and has no sharing pattern or identity basis under rules 15-16 or 30-38,
    classify it as `non_reusable`. Superclass reachability does not establish
    an operational reuse opportunity. Do not apply this veto when the class
    is the declared range of a root-level object property and its
    payload-free subclasses are already shared referents under rules 15 or
    30. That range class is an operational apparatus or referent role, even
    if the plan lists only the subclasses.
29. Choose the narrowest scope supported by supplied evidence.
    A rule or naming convention stated only within a paper/document maps to
    `document`, not `global`. Use `global` or `global_value` when the T-Box
    treats the referent as a catalog, organization, or closed descriptor
    that is not document-bound. Use `top_entity` when identity is scoped to
    one root workflow.
30. Payload-free shared referents: if the candidate carries no direct
    numeric payload, no order index, and no owner-local qualifier, and it
    is the range of object properties from several occurrence-like owners
    or from both a root and its members, treat it as a shared referent.
    Match basis may be the pointing string or a closed descriptor set.
    Scope is `global_value` for catalog/type/atmosphere/technique
    descriptors, `document` for source-local apparatus or agent names, and
    `top_entity` when the T-Box or plan ties the referent to one root.
31. Repeated incoming paths are sharing evidence even when no comment uses
    reuse vocabulary. If several owners can point at the same payload-free
    individual, generic reuse has operational benefit.
32. Cardinality comments of the form "one per root when identifiable,
    otherwise omit" constrain mention count, not exclusive identity.
    Comments that a location or anchor is "for this root" describe the
    attachment, not a private individual. Several roots in one document
    that cite the same verbatim heading name the same location. Prefer
    `document` unless the T-Box scopes the referent as private to one
    root (`top_entity`).
33. Comments that a fact is stated once and then inherited or applied to
    every covered member are pipeline evidence of a shared referent at the
    stated scope. Do not mint a fresh individual per member unless the
    member also carries a distinct owner-local payload.
34. A class listed in a later remainder or linking slot, while also being
    the range of member-level properties in an earlier slot, is expected
    to be referenced from independently built scopes.
35. Do not classify a payload-free descriptor, apparatus, agent, or
    structural-anchor class as `non_reusable` solely because the plan
    lists it beside occurrence classes. Plan ownership lists are
    scheduling, not occurrence identity.
36. Declared-range superclass: if class C is the range of an object
    property from a per-entity root, C carries no numeric, order, or
    owner-local payload, and subclasses of C are payload-free apparatus
    or descriptors that members already point to, classify C as
    `reusable` at the same scope as those subclasses. Match basis is the
    same pointing string. Do not require C itself to appear in a
    materialization class list.
37. Source-text location: a payload-free class whose T-Box identity is a
    verbatim heading, section, paragraph label, or equivalent source-text
    location is a document-scoped referent. Distinct procedures may
    attach to the same location string. This is not an event, not a
    quantity, and not exclusive mint-fresh identity.
38. Equivalent root/member pair across subclass: a root property whose
    range is superclass C, paired with member properties whose ranges
    are subclasses of C, is an equivalent sharing pair under rule 15.
    Judge C and those subclasses as the same apparatus or referent role.

## Reuse scopes

For every reusable class, select the narrowest safe scope:

- `global`: safely reusable across documents by stable entity identity.
- `document`: reusable only within one source document.
- `top_entity`: reusable only within one top-entity graph.
- `global_value`: reusable across documents by a complete immutable,
  non-numeric descriptor tuple.
- `global_reference`: reuse an ontology-declared stable reference IRI.

These values are the exact runtime vocabulary. Parent-owned occurrences are
non-reusable; do not encode them as a narrower reusable scope.

## Required reasoning

For each decision:

- evaluate rules 10, 18, and 22 before considering positive reuse evidence;
- after those vetoes, apply rules 15 and 30-38 before the no-basis veto;
- fill `contextual_value_veto` from graph structure and property semantics;
- if `contextual_value_veto.applies` is `true`, place the class only in
  `non_reusable_classes`; reuse scope limitations cannot neutralize the veto;
- cite concrete T-Box evidence: class comments, superclass relations,
  restrictions, integrity annotations, or identity-bearing properties;
- cite concrete materialization-plan evidence about ownership, creation count,
  per-scope execution, or expected shared references;
- state the required match basis;
- identify the main false-merge risk;
- do not invent identifiers or properties.

## Output

Return only one valid JSON object with this exact top-level shape:

```json
{
  "schema_version": "single-tbox-operational-reusability.v3",
  "decision_target": "pipeline_reuse_enabled",
  "tbox_sha256": "<supplied hash>",
  "reusable_classes": [
    {
      "class_iri": "<exact inventory IRI>",
      "reuse_scope": "global|document|top_entity|global_value|global_reference",
      "match_basis": "<T-Box-supported basis>",
      "tbox_evidence": ["<specific evidence>"],
      "pipeline_evidence": ["<specific plan evidence>"],
      "contextual_value_veto": {
        "applies": false,
        "direct_contextual_properties": [],
        "repeated_owner_paths": [],
        "ownership_recoverable_after_merge": true,
        "explanation": "<why the hard veto does not apply>"
      },
      "false_merge_risk": "<risk>",
      "confidence": "high|medium|low"
    }
  ],
  "non_reusable_classes": [
    {
      "class_iri": "<exact inventory IRI>",
      "reason": "<why fresh identity or conservative rejection is required>",
      "tbox_evidence": ["<specific evidence>"],
      "pipeline_evidence": ["<specific plan evidence>"],
      "contextual_value_veto": {
        "applies": true,
        "direct_contextual_properties": ["<property IRI or empty>"],
        "repeated_owner_paths": ["<incoming owner path or empty>"],
        "ownership_recoverable_after_merge": false,
        "explanation": "<why the hard veto applies>"
      },
      "confidence": "high|medium|low"
    }
  ]
}
```

For a non-reusable class rejected by another rule, `contextual_value_veto.applies`
may be `false`; provide empty property/path arrays and explain why this specific
veto does not apply. A reusable class must always have
`contextual_value_veto.applies=false`.

Do not add classes absent from the inventory. Do not omit any inventory class.
Before returning, verify that the two arrays are disjoint and that their union
equals the inventory exactly. Do not return Markdown.

## Class inventory

{class_inventory_json}

## Materialization/ownership plan

{materialization_plan_json}

## Cross-T-Box runtime contexts

These configurations describe supporting ontology extensions and their runtime
tools. They are evidence, not additional class inventories.

{cross_tbox_contexts_json}

## Supporting T-Boxes

Each entry contains its source path, SHA-256, and Turtle content.

{supporting_tboxes_json}

## T-Box SHA-256

{tbox_sha256}

## Primary T-Box

```turtle
{tbox_content}
```
