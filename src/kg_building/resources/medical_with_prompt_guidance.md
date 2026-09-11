# Frozen OntoMed KG-building guidance (with-prompt)

This text is the only additive protocol on top of each constructor's strict
no-prompt surface (Pipeline: no-contract envelope + MCP; OntoLogX: occurrence
and ownership map). It is not a T-Box dump and not official ONEPASS. Keep it
verbatim. Apply every rule through the constructor you are using.

Constructor encoding is different; the graph must be the same.

- Pipeline: one `create_*` per ledger occurrence. Datatype facets are arguments
  on that same call (`NSCLC="1"`, `OP_Datum="16.05.2024"`, `Operateur_in="Wagner"`,
  ...). The parent link is implicit in `create_Diagnosis(..., parent_iri=<MedicalCase>)`.
- OntoLogX: one `SynthesisGraph` with `nodes` and `relationships`. Each owner is
  a child node plus a relationship whose predicate is exactly the parent hop
  (`hasDiagnosis`, `hasProcedure`, `hasPatientInfo`, ...). Emitting the owner
  node without that relationship is incomplete.

ITER1 (MedicalCase identity) and ITER2 (`ref-entity-relations.v1` owners) are
complementary views of one bound `medical:MedicalCase`. Materialize the union.
Every call, including a SHACL correction, must emit the complete graph for that
bound root. Do not treat ITER2 as a patch that drops the root or earlier owners.

## Bound root — MedicalCase

- Exactly one `medical:MedicalCase` (the bound root). Give it `rdfs:label`.
- The bound root IRI in the human message is that case. Do not mint a second
  MedicalCase for the same ledger.
- Do not copy patient names, surgeon names, or procedure titles onto the root
  as a substitute for owner nodes.

## Eight owner classes (do not skip the parent edge)

This is the most common constructor failure. Each owner heading is one node
attached to the bound MedicalCase by exactly one object property:

| Owner | Parent predicate |
|---|---|
| `PatientInfo` | `hasPatientInfo` |
| `CaseTimeline` | `hasTimeline` |
| `SurgicalApproach` | `hasSurgicalApproach` |
| `Procedure` | `hasProcedure` |
| `SurgicalTeam` | `hasSurgicalTeam` |
| `Diagnosis` | `hasDiagnosis` |
| `Complication` | `hasComplication` |
| `PathologyOutcome` | `hasPathologyOutcome` |

- Emit an owner when the ledger has that heading or any grounded facet for it.
- Do not emit an empty owner with no datatype properties.
- Facets stay on that owner. These owners have **no nested hops**. Amount-like
  or name-like values are datatype properties on the owner, not child nodes.
- Every node needs `rdfs:label`.

Pipeline: `create_Diagnosis(parent_iri=<MedicalCase IRI>, NSCLC="1", ...)`.
Leaving the parent unset, or putting NSCLC on MedicalCase, is invalid.

OntoLogX: emit (1) the `medical:Diagnosis` node with its datatype properties,
(2) relationship `MedicalCase --hasDiagnosis--> Diagnosis`. A Diagnosis that
has type and label but no `hasDiagnosis` relationship is invalid. Do not hang
the same node under `hasProcedure` or `hasComplication` because labels overlap.

## PatientInfo vs SurgicalTeam (do not swap people)

- `PatientInfo.Name` is the patient. If a header block near Fall-Nr / Geburtsdatum
  gives `Nachname, Vorname`, emit `Vorname Nachname`.
- Names from Operateur / Assistent / Behandler / authors lists must **not**
  become PatientInfo.
- `SurgicalTeam.Operateur_in` and `Assistent_in` stay on SurgicalTeam. Keep the
  two roles distinct. When the source has an explicit role block, that block
  wins over an unstructured name list.

## SurgicalApproach — final access only

`offen`, `VATS`, and `RATS` are `binary_checklist`. At most one of them is `"1"`.

- Use the **final completed** approach.
- Documented conversion to thoracotomy / open → only `offen="1"`. Do not also
  set VATS or RATS because the case started thoracoscopically.
- Robot-assisted completed access → `RATS="1"`, not VATS, even if the sentence
  also says minimally invasive / thoracoscopic.
- Do not set `offen` merely because a camera, thoracoscopy, or "minimalinvasiv"
  is mentioned without conversion.

## Binary checklist vs omit

For every property whose T-Box `valueKind` is `binary_checklist`:

- Supported by the ledger → write the literal `"1"`.
- Not supported → **omit** the property. Do not write `"0"`, `"n"`, `"false"`,
  `"nein"`, or `"-"`.

Canonical binary diagnosis / procedure fields beat `sonst_*` free-text
fallbacks. Use `sonst_Diagnose` / `sonst_Eingriff` only for an explicit leftover
that no canonical field covers. Do not let a generic `sonst_*` string displace
a canonical `"1"` that the ledger already supports.

`Same_Day_Surgery` is **not** binary_checklist. When admission date and OP date
are both grounded: same calendar day → `"j"`, otherwise `"n"`. If admission date
is missing, omit it.

`Komplikation_j_n` is `"1"` only for postoperative complications in the window
OP to discharge. Intraoperative technical events, immediate re-ventilation, or
purely logistical ICU transfer without an explicit complication mark are not
automatically complications.

## Dates and derived integers

- `Geburtsdatum`, `OP_Datum`, `Entlassdatum`, `praeop_TuKo`: `TT.MM.JJJJ`.
- `Alter`: derived from Geburtsdatum and OP_Datum. Do not invent an age when
  either date is missing.
- `Verweildauer`: derived from OP_Datum and Entlassdatum (day after OP through
  discharge inclusive). Omit when either date is missing.
- `Dauer_d_zwischen_TuKo_und_OP`: derived from praeop_TuKo and OP_Datum (first
  day after TuKo through OP inclusive). Omit when praeop_TuKo is missing.

## Completion check

Before returning, verify:

1. The bound MedicalCase is present with `rdfs:label`.
2. Every headed owner from the ledger exists as its owner class.
3. Every owner is linked to the bound case with the table above (OX:
   relationship; Pipeline: `create_*` parent).
4. Binary checklist values are `"1"` or absent.
5. Patient names are not surgeon names; approach is the final exclusive access.
6. The graph is a complete replacement, not a delta.
