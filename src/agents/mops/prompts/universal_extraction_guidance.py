UNIVERSAL_EXTRACTION_GUIDANCE = """
UNIVERSAL_EXTRACTION_GUIDANCE

Purpose
- Extract only what the task requests. Do not add extras.

Scope control
- Include an item only if it clearly matches the target entity type for this document. When uncertain, exclude.
- One output per record. If a line names multiple outputs, create multiple records.

Evidence proximity
- Require clear supporting detail in the immediate context or an explicit “same as / following <label>” within this document.
- Use only facts stated in this document unless the task permits lookup.

Identity and matching
- Each included item must be a specific instance with a stable identifier: code, name, concrete string, or accession.
- Validate identifiers exactly. If the given entity_label and the chosen identifier do not match, correct before proceeding.

Composite label filter
- Exclude bundled items. Reject labels that indicate combinations, e.g., “/”, “+”, “·”, “with …”, “including …”, or similar combiners.
- Treat distinct entities separately. Never merge multiple outputs into one record.

Splitting and deduplication
- Count each distinct route or procedure as a separate record even if the named output is the same.
- Do not split by attributes that do not reflect distinct procedures.
- Deduplicate by stable identifiers. If text blocks are identical and there is no separate label, treat as one.

Inheritance (within this document; depth=1)
- If “same as / following / according to / similar to <label>” is stated, inherit the baseline content verbatim and apply only stated changes. Mark with “inherited from <label>”.

Numeric data (only when requested)
- Record only explicit numeric values and qualifiers as written. Do not compute values from other fields. If none, write “not stated”.

Source anchors
- Capture exact in-document anchors such as section or subsection titles, figure, table, or paragraph labels. If none, write “not stated”.

Normalization
- Preserve wording and units. Normalize whitespace. Remove control characters. Do not add markup that is not present.

External lookup and provenance (only if the task allows)
- Annotate provenance as one of:
  (as stated)
  (looked up: <source or ID>)
  (derived: <brief basis>)
- Do not invent values.

Formatting
- Output plain text exactly as the task specifies. No JSON unless requested.
- Keep field labels and order exactly as specified.
- Use “N/A” where the task requires a placeholder and data is missing.
- One line or one block per record as the task specifies. Use a single blank line between records if requested.

Final checks
- Re-verify entity_label and identifiers match.
- Confirm evidence proximity is satisfied.
- Confirm composites are filtered.
- Confirm correct splitting and deduplication.
"""