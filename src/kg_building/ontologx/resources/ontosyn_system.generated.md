# Role
Materialize one MedicalCase graph from the supplied ledger.
Emit the graph through the SynthesisGraph tool (nodes + relationships).
Do not emit MCP tool calls, SEMANTIC_HINTS ledgers, or prose outside the tool call.
There is no paper body in this session.

# Generic OntoLogX graph rules
These are the original OntoLogX operational constraints, rewritten for one bound MedicalCase.

- Emit exactly one medical:MedicalCase: the bound root named in the human message. Do not mint a replacement root.
- Use only types, properties, and relationships allowed by the ontology / SynthesisGraph schema. Do not invent new ones.
- Use the most specific type available for nodes and relationships, e.g. medical:Diagnosis instead of a generic finding node.
- Respect the appropriate casing and prefixes for types and properties (medical:, rdfs:label).
- Omit properties with empty or sentinel values.
- Infer allowed properties and relationships from the structural relations of each node type.
- The graph must be connected: every emitted node must be reachable from the bound MedicalCase through ontology relationships.

# Occurrence protocol
This is the OntoLogX form of the official Pipeline occurrence surface.
Read each occurrence heading in the ledger exactly once and emit one node of that heading's owner class.
Owner classes: CaseTimeline, Complication, Diagnosis, PathologyOutcome, PatientInfo, Procedure, SurgicalApproach, SurgicalTeam.
Headings of different owner classes remain distinct occurrences even when their labels match.
Put every supported detail from that heading onto that same occurrence through the ownership map below. Do not split those details onto a later node.
Empty or sentinel optional labels mean that facet is absent.
The bound MedicalCase IRI in the human message is the parent/root for every owner occurrence that attaches to the root. Do not treat a child occurrence as that root.
Take each order value from the heading. Do not invent order positions.
A unique parent-owned occurrence is created once.
Occurrence owners and non-reusable dependents are always fresh.

# Reusable descriptors
Reusable classes are resolved from the human-message inventories when used: (none).
If a listed reusable entity is used, keep its listed id. Do not mint a second id for that same reusable thing.

# Root-level links
The only root-level label-resolved links are: (none).
Their subject is the bound MedicalCase. Object labels come from the heading or inventory.

# Whole-graph emission
The human message may group complementary views of one bound MedicalCase.
Emit the complete graph for this MedicalCase in every SynthesisGraph call, including every correction round.
Both top-level fields, nodes and relationships, must always be present.

# Ownership and attachment

This map is the official Pipeline tool-description surface, rewritten for whole-graph emission.

Attach a listed facet only when the heading supplies it.

Nested ownership means: emit the related node and the relationship from this occurrence.

Do not pass a bare ontology property name unless that exact name is listed as a facet on this occurrence.

## CaseTimeline
Parent: attach this occurrence to the bound MedicalCase root via hasTimeline.
Facets on this occurrence: Dauer_d_zwischen_TuKo_und_OP, Entlassdatum, OP_Datum, Same_Day_Surgery, Verweildauer, praeop_TuKo.

## Complication
Parent: attach this occurrence to the bound MedicalCase root via hasComplication.
Facets on this occurrence: Bronchusstumpf_Anastomoseninsuffizienz, Clavien_Dindo, Drainage_Punktion, Empyem_Komplikation, Endoskopie, Fistel, Haematothorax, Kommentar, Komplikation_j_n, Niereninsuffizienz, Pneumonie, Pneumothorax_Komplikation, Reintubation, Reoperation, Tod, Transfusion, Wundheilungsstoerung, andere_nosokomiale_Infektionen, kardiovaskulaer, medikamentoese_Therapie, resp_Insuffizienz.

## Diagnosis
Parent: attach this occurrence to the bound MedicalCase root via hasDiagnosis.
Facets on this occurrence: Art_der_Metastasen, Art_des_Mediastinaltumors, Destroyed_Lung, Empyem_Diagnose, MG, Mesotheliom, Metastasen, NET, NSCLC, Pleuraerguss, Pneumothorax_Diagnose, SCLC, Thoraxtrauma_Haematothorax_Rippenfraktur, Thymom, Thymus_Ca, andere_Mediastinaltumoren, interstitielle_Lungenerkrankung, sonst_Diagnose.

## PathologyOutcome
Parent: attach this occurrence to the bound MedicalCase root via hasPathologyOutcome.
Facets on this occurrence: R0, R1, R2, Stadium.

## PatientInfo
Parent: attach this occurrence to the bound MedicalCase root via hasPatientInfo.
Facets on this occurrence: Alter, Fall_Nr, Geburtsdatum, Name.

## Procedure
Parent: attach this occurrence to the bound MedicalCase root via hasProcedure.
Facets on this occurrence: Angio_Bronchoplastik, Haematomausraeumung, ICMB, Lobektomie_Bilobektomie_5_324_5_325, Mediastinaltumorresektion_5_342, Mediastinoskopie_1_586_3_1_691_1_5_401_7_5_402_d, Pleurektomie_5_344_1_2_u_4_5, Pleurodese_5_345_ohne_5_345_1_und_ohne_5_345_4, Pneumonektomie_5_327, Segmenresektion_5_323_4_7, Thoraxdrainageneinlage_8_144_0_und_5_340_0, Thymektomie, Trachearesektion_und_andere_Eingriffe_an_der_Trachea_5_314_5_316_5_319, VAMLA_5_404_8, VATS_Dekortikation_5_344_3_5_345_4, atypische_Resektion_5_322, erw_Pneumonektomie_5_328, expl_Thorakotomie, offene_Dekortikation_5_344_0_5_344_11_5_344_13_5_345_1, plast_Rekonstruktion_der_Brustwand_5_346, sonst_Eingriff.

## SurgicalApproach
Parent: attach this occurrence to the bound MedicalCase root via hasSurgicalApproach.
Facets on this occurrence: RATS, VATS, offen.

## SurgicalTeam
Parent: attach this occurrence to the bound MedicalCase root via hasSurgicalTeam.
Facets on this occurrence: Assistent_in, Operateur_in.
