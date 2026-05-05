# Medical Extraction Error Analysis

## Scope
This note summarizes the latest error analysis for the medical case extraction pipeline after the most recent targeted rerun.

Current snapshot:
- Overall accuracy: `92.16%` (`341 / 370`)
- Cases: `5`
- Comparable columns: `74`

The goal of this document is:
- identify the fields that are still underperforming,
- explain likely causes in plain English,
- separate pipeline/prompt issues from ontology/T-Box structure issues.

## Worst Performing Fields
### 1. `Age`
- Accuracy: `20%`
- Observed pattern:
  Age is usually missing even when `Geburtsdatum` and `OP-Datum` are present.
- Best guess:
  This does not look like an LLM understanding problem. It looks more like a deterministic post-processing or CSV-conversion gap. The information needed to derive age is often already available, but the derivation does not seem to be applied consistently in the current output path.

### 2. `Other procedure`
- Accuracy: `20%`
- Observed pattern:
  The system still uses this field for borderline operative phrases such as chest drain insertion or other residual free-text actions.
- Best guess:
  This field behaves like a catch-all sink. Whenever the model is unsure whether a phrase should map to a canonical procedure class, it falls back to `Other procedure`. That makes the field easy to over-predict.
- Real-case examples:
  - `Claudia_Meyer_Fall-Nr_234567890.ttl`: the latest iter3 hints still contained `sonst_Eingriff: "Einlage von 3 20 Charrière Thoraxdrainagen"`, even though the ground truth does not want this counted as `Other procedure`.
  - `MedicalCase-1_Gustav_Gans_Fall-Nr._456789012.ttl`: the system converged to `sonst_Eingriff: "Einlage einer linksseitigen Thoraxdrainage"`, while the ground truth expects a canonical thymic/mediastinal procedure signal instead of a drain-related fallback.
  - `MedicalCase-1_Hans_Mueller_Fall-Nr_123456789.ttl`: the latest iter3 hints still contained `sonst_Eingriff: "Einlage einer 20 Charrière Thoraxdrainage"`, which is likely routine postoperative management rather than the intended main procedure label.

### 3. `Same Day Surgery`
- Accuracy: `40%`
- Observed pattern:
  It is still over-predicted in several cases.
- Best guess:
  The current semantics are probably too weakly anchored. The system seems to infer this from timeline context or from the presence of surgery/date information, instead of requiring a strict case-level signal that the case truly belongs in the same-day category.

### 4. `Open approach`
- Accuracy: `40%`
- Observed pattern:
  Some cases still flip between `Open` and minimally invasive access markers.
- Best guess:
  The pipeline still struggles with access-route disambiguation when reports contain mixed language such as incision wording, thoracoscopic wording, robotic wording, or partial conversion-like descriptions. This is not pure hallucination anymore; it is mostly a final interpretation problem.
- Real-case examples:
  - `Claudia_Meyer_Fall-Nr_234567890.ttl`: ground truth expects `offen = 1`, but the latest system output still prefers `VATS = 1` based on the hint quote `Uniportale Inzision im 5. ICR der vorderen Axillarlinie`.
  - `MedicalCase-1_Peter_Lustig_Fall-Nr._345678901.ttl`: the report is still scored as `offen = 1` predicted vs `RATS = 1` in ground truth, showing that robotic access can still be collapsed into the wrong final access label.

### 5. `RATS`
- Accuracy: `60%`
- Observed pattern:
  Robot-assisted cases are sometimes still under-called.
- Best guess:
  The model is sensitive to phrasing. When the report uses indirect wording like "thoracoscopic technique with robot assistance" instead of a short canonical phrase, the normalization to the `RATS` flag is still somewhat brittle.

### 6. `MG`
- Accuracy: `60%`
- Observed pattern:
  `MG` is still missed in some cases where the diagnosis appears semantically present.
- Best guess:
  This remains a diagnosis-marker normalization issue. The extractor is more conservative than before and now avoids some false positives, but it still occasionally fails to promote narrative diagnosis wording into the binary marker field.

### 7. `Thymoma`
- Accuracy: `60%`
- Observed pattern:
  Similar to `MG`, some thymoma cases are still missed.
- Best guess:
  Same family of problem: explicit diagnosis wording is required, but the model is not always confident enough to normalize narrative/pathology-style wording into the structured diagnosis marker.

## Moderately Weak Fields
### `VATS`
- Accuracy: `80%`
- Best guess:
  Mostly good, but still vulnerable in mixed-access cases.

### `Lobectomy/Bilobectomy`
- Accuracy: `80%`
- Best guess:
  Specific resection type is sometimes expressed narratively instead of with an exact canonical term, so normalization still occasionally fails.

### `Open decortication`
- Accuracy: `80%`
- Best guess:
  This appears to be a compound interpretation problem: both the operative act (`decortication`) and the final access route (`open`) must be correct at the same time.

### `Thymectomy`
- Accuracy: `80%`
- Best guess:
  The model still sometimes prefers a literal unmatched free-text act under `Other procedure` instead of normalizing to the canonical `Thymectomy` field.

### `Chest tube insertion`
- Accuracy: `80%`
- Best guess:
  This remains a classic ambiguity: many reports mention drain placement, but the ground truth only wants it when interpreted as an independent procedure rather than routine postoperative management.

### `ICMB`
- Accuracy: `80%`
- Best guess:
  Likely under-detected because the underlying wording is less standardized and may not map cleanly to the exact structured field.

### `Primary surgeon`
- Accuracy: `80%`
- Best guess:
  Mostly a normalization issue. Multi-person strings such as `Kadlec/Klose` can collapse into a partial name if the system prefers one person or if output normalization is too aggressive.
- Real-case example:
  - `MedicalCase-1_Gustav_Gans_Fall-Nr._456789012.ttl`: the scoring report shows GT `Kadlec/Klose` vs Pred `Kadlec`, which is a typical "partial normalization" failure rather than a complete miss.

### `NSCLC`
- Accuracy: `80%`
- Best guess:
  The diagnosis meaning is often captured, but not always promoted into the binary marker field.
- Real-case example:
  - `MedicalCase-1_Hans_Mueller_Fall-Nr_123456789.ttl`: the latest TTL contains `sonst_Diagnose "Nichtkleinzelliges Bronchialkarzinom des Unterlappens links"` while the scoring report still shows `NSCLC` as missed. That suggests the semantic diagnosis is partially captured, but not normalized to the exact binary field consistently.

### `Thymic carcinoma`
- Accuracy: `80%`
- Best guess:
  Same family as `MG` and `Thymoma`: the system is now relatively cautious, but sometimes too cautious.

## High-Level Interpretation
At this stage, the remaining errors are not dominated by random hallucinations. They are concentrated in a few recurring categories:

### 1. Derived field failures
The clearest example is `Age`. This is probably outside the LLM core and should be fixable deterministically.

### 2. Borderline normalization into `Other procedure`
The pipeline is now better at avoiding broad checklist hallucinations, but it is still slightly too literal with unmatched procedure phrases.

### 3. Access-route disambiguation
The system still has trouble deciding between `Open`, `VATS`, and `RATS` when operative notes use mixed or layered access descriptions.

### 4. Diagnosis marker promotion
The semantic content may be present, but the binary diagnosis marker is not always emitted.

## Definitions and Real-Case Examples
### What does `mixed access` mean?
In this document, `mixed access` means that the same operative report contains signals for more than one surgical access type, so the system must decide which one represents the final structured label.

Typical patterns include:
- a minimally invasive access description plus wording that suggests an open procedure,
- robot-assisted phrasing plus generic thoracoscopic phrasing,
- early access/setup wording that does not match the final structured access category,
- layered descriptions where the report mentions both the planned access and the effectively completed access.

Why this matters:
- the source text is clinically understandable to a human reader,
- but it is not always a one-to-one mapping to a single binary field such as `offen`, `VATS`, or `RATS`.

Real-case examples:
- `Claudia_Meyer_Fall-Nr_234567890.ttl`:
  the system still uses the access quote `Uniportale Inzision im 5. ICR der vorderen Axillarlinie` as support for `VATS = 1`, while ground truth expects `offen = 1` together with `offene Dekortikation`. This is a mixed-access interpretation problem: the report contains minimally invasive-style access wording, but the target structure wants the final access/procedure interpretation on the open side.
- `MedicalCase-1_Gustav_Gans_Fall-Nr._456789012.ttl`:
  the operative text includes `minimal-invasiver thorakoskopischer Technik mit der Modifikation einer Roboterassistenz`. This mixes thoracoscopic and robotic wording in one phrase. The system now usually lands on `RATS`, but historically this type of phrasing caused `RATS`/`VATS` instability.
- `MedicalCase-1_Peter_Lustig_Fall-Nr._345678901.ttl`:
  the score still shows `offen` predicted where ground truth wants `RATS`. This is another example of access signals being interpreted at the wrong final level.

### What does `catch-all sink` mean?
Here, `catch-all sink` means a field that is too easy to use as a fallback whenever the system is unsure.

In this pipeline, `sonst. (Eingriff)` / `Other procedure` behaves this way:
- if the model cannot confidently map a phrase to a canonical coded procedure,
- it often dumps the phrase into `Other procedure`.

Real-case examples:
- `Claudia`: chest drain insertion ends up in `Other procedure`.
- `Gustav`: `Einlage einer linksseitigen Thoraxdrainage` ends up in `Other procedure`.
- `Hans`: `Einlage einer 20 Charrière Thoraxdrainage` ends up in `Other procedure`.

### What does `diagnosis marker promotion` mean?
This means the source text already contains diagnosis information semantically, but the pipeline still fails to convert that information into the intended binary diagnosis field.

Real-case examples:
- `Hans`: the TTL contains `sonst_Diagnose "Nichtkleinzelliges Bronchialkarzinom des Unterlappens links"`, but the `NSCLC` marker is still missed in scoring.
- `Gustav`: the latest iter4 hints correctly contain `MG: "1"` and `Thymom: "1"` as diagnosis evidence, but the score still shows those fields underperforming overall, meaning this normalization is not yet robust across all cases.

## Possible Problems Caused by the Structure of the T-Box
The T-Box is not the only source of error, but some remaining weak spots are plausibly made harder by its structure.

### 1. Access route is modeled as multiple binary flags instead of a normalized final-state representation
Current examples include:
- `offen`
- `VATS`
- `RATS`

Potential issue:
- These fields behave like mutually exclusive final interpretations in many cases, but the ontology surface looks like three independent booleans.
- That makes mixed-access wording harder to resolve cleanly, especially in cases involving thoracoscopy, robotic assistance, or conversion semantics.

Why this matters:
- The model must infer exclusivity and final-state logic from comments and prompt rules, instead of from the core structure itself.
- Real-case grounding:
  - `Claudia` still oscillates between `offen` and `VATS`.
  - `Peter Lustig` still shows `offen` vs `RATS` disagreement.
  These are exactly the kinds of errors that become harder when the ontology presents access route as parallel binary flags instead of a stronger final-state structure.

### 2. Canonical procedures and `Other procedure` live side-by-side without a stronger structural preference
Potential issue:
- `Other procedure` is easy to use as a fallback because structurally it sits beside the canonical coded procedures.
- The T-Box comment helps, but the schema shape still makes the free-text escape hatch very available.

Why this matters:
- Whenever the model is slightly uncertain, it can choose the free-text field instead of a normalized coded procedure.
- Real-case grounding:
  - `Claudia`, `Gustav`, and `Hans` all still leak drain-related phrases into `Other procedure`.
  This strongly suggests that the free-text escape hatch is structurally too available.

### 3. Independent procedure versus routine operative step is encoded mainly in comments
Example:
- Thorax drain insertion

Potential issue:
- The distinction between a routine postoperative drain and an independently countable procedure is clinically meaningful but structurally weak.
- The ontology relies heavily on comment-level semantics rather than on a more explicit structural separation.

Why this matters:
- This creates recurring ambiguity for both `Chest tube insertion` and `Other procedure`.
- Real-case grounding:
  - In multiple cases, the system still treats chest drain insertion as if it were a countable independent procedure, even when the ground truth does not.

### 4. Diagnosis markers are represented as separate booleans instead of a more evidence-linked diagnosis model
Examples:
- `MG`
- `Thymoma`
- `NSCLC`
- `Thymic carcinoma`

Potential issue:
- These are not just text spans; they are normalized diagnostic conclusions.
- The current representation encourages a hard binary decision, but the source text often expresses diagnosis in narrative, pathological, or indication-style language.

Why this matters:
- The model must decide whether the wording is strong enough for the marker, which makes these fields systematically fragile.
- Real-case grounding:
  - `Hans` can carry NSCLC-like content in free text without reliably scoring as `NSCLC = 1`.
  - `Gustav` can carry explicit `MG` / `Thymom` evidence in hints, but those fields are still not perfect in the final score.

### 5. Derived fields appear in the same flat evaluation space as directly extractable fields
Example:
- `Age`

Potential issue:
- `Age` is structurally a derived value from dates, but it is still treated like an ordinary output field in downstream evaluation.

Why this matters:
- That makes the pipeline sensitive to whether the derivation step is applied consistently, even though the source evidence is already available.

### 6. Some semantics are comment-heavy rather than shape-heavy
Potential issue:
- Several important distinctions are currently encoded in `rdfs:comment` rather than in stronger structural constraints.
- This is workable for prompt engineering, but it means the ontology itself does not strongly guide the model unless the generated prompts successfully restate those comments.