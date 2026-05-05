# Request for Clinical Review of Definitions Used in Thoracic Surgery Report Interpretation
 
## What we want you to assess
For each category or boundary below, please tell us whether our current interpretation is:

- **Correct**
- **Mostly correct, but too broad**
- **Mostly correct, but too narrow**
- **Incorrect or potentially misleading**

When relevant, please also tell us:
- what should be changed,
- 1-2 short examples that **should count**,
- 1-2 short examples that **should not count**,
- any important rule about priority, exclusion, co-occurrence, or overlap.

## Suggested answer format
For each topic, please answer using this structure:

1. **Status**  
   Correct / too broad / too narrow / incorrect

2. **Reason**  
   Why the current interpretation is clinically acceptable or problematic

3. **Recommended correction**  
   How the definition or boundary should be changed

4. **Examples**  
   - Examples that should count
   - Examples that should not count

## General principle we want to validate
Our main question is:

**Are these category definitions medically correct and usable for interpreting real thoracic surgery reports?**

---

# Topic-by-topic review

## 1. Procedure
### Our current rule
- This category should include **completed, clinically meaningful operative procedures**.
- Explicitly named procedures in summary sections such as **Operation**, **Procedure performed**, or equivalent summary lines are given strong weight.
- A specific named procedure should take priority over **other procedure** (`sonst. (Eingriff)`).
- Routine parts of an operation, setup steps, access steps, exposure steps, mobilization steps, and closure steps should usually **not** be coded as separate procedures.
- We currently do **not** use **other procedure** for actions that are merely part of performing a more specific main operation, such as routine preparation, exposure, adhesiolysis, mobilization, or access-related steps.
- We also currently do **not** use **other procedure** for accompanying steps such as lymphadenectomy or related adjunctive manoeuvres when these are described only as part of a larger canonical operation and there is no dedicated category for them.
- We use **other procedure** only when the report describes a **separate completed operative action with its own clinical weight** and no specific existing procedure category fits.

### Questions
1. Is our threshold between an **independent procedure** and a **routine operative step** clinically appropriate?
2. Should explicitly named procedures in summary lines be given more weight than scattered mentions in the narrative?
3. Is it right to use **other procedure** only as a strict fallback, rather than as a broad catch-all?
4. Are there common procedure types that we are likely to over-call or under-call with this approach?

---

## 2. Diagnosis
### Our current rule
- This category should capture the **case-level diagnosis relevant to the operation**, not every incidental finding and not every postoperative finding.
- Explicit diagnosis statements such as **Diagnosis**, **Indication**, or equivalent case-level headings are treated as strong evidence.
- A specific diagnosis category should take priority over a generic fallback such as **other diagnosis** (`sonst. (Diagnose)`).
- If a diagnosis is only weakly implied, we usually leave it empty rather than infer it.
- We generally do **not** infer diagnosis from the procedure alone.
- For some diagnoses, however, we currently allow an **explicitly stated suspicion diagnosis, working diagnosis, or operative indication** to count; for others, we are unsure whether final pathology should be required.

### Questions
1. Is this evidence threshold too strict, too loose, or about right?
2. Are we separating diagnosis appropriately from postoperative findings and complications?
3. For suspected diagnoses, working diagnoses, or operative indications, is our intended handling clinically reasonable?
4. For which diagnoses is preoperative wording sufficient, and for which should final pathology be required?

---

## 3. Complication
### Our current rule
- This category is intended to capture **postoperative complications of the case in the period from the day of surgery until discharge**.
- We do **not** want to count every ICU stay, treatment step, or unplanned management decision automatically as a complication.
- We currently prefer **explicit case-specific complication wording** over inference.
- Routine postoperative care should remain excluded.
- We currently do **not** count the following by themselves as postoperative complications unless the report clearly describes a true postoperative complication event:
  - intraoperative technical observations,
  - immediate reventilation or repositioning observations,
  - logistical ICU transfer,
  - prophylactic measures such as preventive drainage or irrigation,
  - blood-stopping measures without a clearly documented postoperative complication,
  - reoperation or postoperative management that is described only as part of the underlying disease process and not as an additional postoperative complication event.
- Our current structure is:
  - **Complication yes/no** (`Komplikation (j/n)`) = whether a postoperative complication occurred,
  - **Clavien-Dindo grade** = only if a complication is present,
  - **Short comment** (`Kommentar`) = only a brief case-specific explanatory note, and only when there is a concrete note relevant to the grading.

### Questions
1. Is our current time window for postoperative complications — **day of surgery to discharge** — clinically reasonable?
2. Are we missing true complications by requiring wording that is too explicit?
3. Are we incorrectly labeling complications based only on treatment actions or postoperative course descriptions?
4. When postoperative complications are described in free text, what is the correct relationship between:
   - **Complication yes/no** (`Komplikation (j/n)`)
   - **Clavien-Dindo grade**
   - **Short explanatory comment** (`Kommentar`)

---

## 4. Pathology-based outcome
### Our current rule
- This category should capture **final pathology-based results**.
- We currently prioritize the **final pathology or final histology result** over provisional intraoperative wording.
- Resection status such as **R0 / R1 / R2** should **not** be assigned from frozen section or other provisional intraoperative wording alone unless it is clearly adopted as the final pathology result.
- **Tumor stage** should be assigned only when supported by explicit final tumor-stage wording.
- In this field, **“stage” means oncological tumor stage only**. We do **not** want to copy non-oncological staging systems such as empyema stage or fibrothorax stage into this category.
- Our current intended stage field is **UICC stage** in a tumor context.

### Questions
1. Is it clinically right to prioritize final pathology over intraoperative wording?
2. Is our threshold for assigning tumor stage appropriate?
3. Is it clinically correct to restrict this field to **oncological tumor stage only**?
4. Are there common report phrasings in this area that are easily misread or commonly misclassified?

---

## 5. Surgical team
Categories under review:
- **Primary surgeon** (`Operateur/in`)
- **Assistant** (`Assistent/in`)

### Our current rule
- These roles should remain separate.
- We normalize names to **surname only**, when the surname is clear.
- If multiple people are explicitly listed within the same role, we keep them in stable source order, separated consistently.
- Role assignment should follow explicit document structure whenever available.
- If names appear near each other without clear role labels, they should **not** automatically be reassigned unless the structure clearly supports that reading.
- However, in a narrow OCR/layout-repair situation, we currently allow the following reconstruction rule: if visible role labels for surgeon and assistant are present, but the inline values under those labels are broken or contain only titles/placeholders, and the same team header is immediately followed by a short ordered name list, we currently assign **first name = surgeon** and **second name = assistant**, unless explicit contradictory role labeling exists.
- We currently do **not** allow a second name in a surgeon line to be reinterpreted as assistant **unless the source structure clearly supports that role assignment**.

### Questions
1. Is the distinction between primary surgeon and assistant clinically and operationally appropriate?
2. Is surname-only normalization an acceptable output format?
3. In reports with broken layout or OCR errors, how cautious should we be when reconstructing team roles?
4. Is our current exception rule for short ordered name lists clinically reasonable, too permissive, or too conservative?

---

# Review of specific high-risk boundaries

## 6. Procedure-family boundaries
We want to know whether the following distinctions are medically correct as currently understood.

### Decortication by approach
#### Our current rule
- **Open decortication** and **VATS decortication** are meant to represent the same type of procedure separated mainly by the **final approach used**.
- We currently classify **open decortication** when the definitive decortication is performed through an open final approach.
- We currently classify **VATS decortication** when decortication is present and the final approach is **minimally invasive rather than open**.
- At present, this means we are effectively grouping **robotic final approach together with VATS decortication** rather than creating a separate robotic decortication label.

### Pleural procedures
#### Our current rule
- **Pleurodesis** and **pleurectomy** should remain distinct procedures.
- **Pleurectomy** is intended to mean resection of parietal pleura, including local, subtotal, or total pleurectomy.
- We do not want to treat pleurodesis and pleurectomy as interchangeable just because they occur in similar pleural disease settings.

### Thymic and mediastinal procedures
#### Our current rule
- **Thymectomy** means resection of the thymus gland.
- **Mediastinal tumor resection** means resection of a mediastinal tumor.
- We currently allow **both** to be marked present when a report explicitly describes combined wording such as **thymectomy with en bloc tumor resection**.
- We currently treat explicit summary phrases like **complete thymectomy with en bloc tumor resection** as strong evidence for **both categories**, not just one.
- For **mediastinal tumor resection**, our current rule still leans toward requiring either an explicit operative description of tumor resection or an explicit mediastinal tumor indication; we suspect this may be too dependent on diagnosis wording.
- **Mediastinoscopy** and **VAMLA** are currently treated as distinct procedures.
- Our current wording says mediastinoscopy is **not** an open approach category.
- Our current wording for **VAMLA** may be wrong: at present it is tied to **VATS/RATS rather than open**, which we suspect may not be clinically appropriate.

### Exploratory thoracotomy
#### Our current rule
- **Exploratory thoracotomy** should only be counted when the report explicitly describes an exploratory thoracotomy or equivalent diagnostic thoracic opening.
- We do **not** want to assume exploratory thoracotomy simply because another open thoracic procedure was performed.

### Questions
For each pair or family above, please comment on:
1. whether the separation is clinically correct,
2. which boundary is most likely to be wrong or unclear,
3. which combinations may legitimately co-occur,
4. which should usually be treated as mutually exclusive.

---

## 7. Diagnosis evidence thresholds for selected diagnoses
We usually prefer an explicit case-level diagnosis statement rather than inferring diagnosis only from the operation performed. However, our **current diagnosis thresholds are not identical across diagnoses**.

Please review whether our current threshold is appropriate for the following:

### NSCLC
#### Our current rule
- We count **NSCLC** only when NSCLC or an equivalent case-specific diagnosis is **explicitly stated**.
- We currently allow either **preoperative** or **postoperative** explicit case-level diagnosis wording to count.
- We do **not** infer NSCLC from lobectomy, staging language, or general tumor suspicion alone.

### SCLC
#### Our current rule
- We currently count **SCLC** when it is explicitly documented as the diagnosis of the case.
- We have **not yet clearly specified** in our internal definition whether suspicion/working diagnosis should count, or whether final pathology should be required.

### NET
#### Our current rule
- We currently count **NET** when it is explicitly documented as the diagnosis of the case.
- We have **not yet clearly specified** in our internal definition whether preoperative suspicion is sufficient or whether final pathology should be required.

### Myasthenia gravis (`MG`)
#### Our current rule
- We count **MG** only when **myasthenia gravis / MG** is explicitly stated.
- We currently allow an explicit **indication diagnosis** or **suspicion diagnosis** to count.
- We do **not** infer MG from thymectomy alone.

### Other mediastinal tumors (`andere Mediastinaltumoren`)
#### Our current rule
- This category is intended for explicit mediastinal tumors that are **not** thymoma and **not** thymic carcinoma.
- Current examples include entities such as **teratoma** or **Castleman disease** when treated as mediastinal tumor diagnoses.
- We have **not yet clearly specified** whether suspicion wording is sufficient or whether final pathology should be required.

### Mesothelioma (`Mesotheliom`)
#### Our current rule
- We currently count mesothelioma when it is explicitly documented as the diagnosis of the case.
- We have **not yet clearly specified** whether preoperative suspicion is sufficient or whether final pathology should be required.

### Questions
For each diagnosis above, please comment on:
1. whether our threshold is too broad, too narrow, or acceptable,
2. whether suspicion / working diagnosis / operative indication should count,
3. whether final pathology should be required.

---

## 8. Broad fallback categories
We want to know whether these fallback categories are defined narrowly enough.

### Other procedure (`sonst. (Eingriff)`)
#### Current rule
- Use only when there is an **explicitly performed, case-specific, completed operative action** that does not fit an existing specific procedure category.
- Do **not** use it for exposure, access, preparation, mobilization, adhesiolysis, or other steps that are merely part of a larger canonical operation.
- Do **not** use it for accompanying steps such as lymphadenectomy when these are described only as part of a canonical main procedure and there is no dedicated target category.
- Use the **shortest original phrase** that still correctly names the procedure.

### Short comment (`Kommentar`)
#### Current rule
- Use only as a **brief explanatory note** in the context of complications.
- Use it only when a concrete case-specific note is present and relevant to the **Clavien-Dindo assessment**.
- Do **not** use it as a long free-text summary.
- Do **not** use it for isolated leak, drainage, or reintubation sentences unless the case actually contains an explicit complication classification or a concrete explanatory note relevant to grading.

### Questions
1. Are these fallback categories still too broad?
2. Should any of them be narrowed further?
3. Are there situations in which these fallback definitions would be clinically misleading?

---

## 9. Definitions that may currently be wrong or misleading
Please specifically confirm or correct the following.

### a) Interstitial lung disease (`interstitielle Lungenerkrankung`)
#### Current rule
- This label currently covers **interstitial lung disease**, but our current wording also gives **COPD** and **pulmonary fibrosis** as examples.

#### Question
- Should this label be restricted to true interstitial lung disease, rather than a broader chronic lung disease group?

### b) VAMLA
#### Current rule
- VAMLA is treated as a distinct mediastinal procedure label.
- However, our current wording also states that VAMLA requires a **VATS or RATS approach rather than open surgery**.

#### Question
- Is that clinically incorrect? Should VAMLA be treated independently from thoracoscopic access categories?

### c) Mediastinoscopy (`Mediastinoskopie`)
#### Current rule
- Mediastinoscopy is treated as a distinct procedure.
- Our current wording also states that it should **not** be interpreted as an open access category.

#### Question
- Is mediastinoscopy best treated only as a procedure, with surgical access left unspecified unless separately documented?

### d) Mediastinal tumor resection (`Mediastinaltumorresektion`)
#### Current rule
- We currently tend to require either explicit operative wording of tumor resection or explicit mediastinal tumor indication/diagnosis.
- We suspect this may be too dependent on diagnosis wording.

#### Question
- If the operative report clearly describes resection of a mediastinal tumor, should that procedure be coded even when the diagnosis wording is incomplete or absent?

---

# Highest-priority items if time is limited
If only a subset can be reviewed, the most useful items are:

1. **Procedure boundary**: independent procedure vs routine operative step
2. **Other procedure** as a fallback category
3. **Diagnosis thresholds** for NSCLC, MG, SCLC, NET, mesothelioma, and other mediastinal tumors
4. **Complication evidence threshold**, especially whether **day of surgery to discharge** is the right complication window
5. **Surgical team** role separation and name normalization
6. **VAMLA / mediastinoscopy / mediastinal tumor resection**, because we suspect these may still be mis-specified
7. **Pathology-based outcome**, especially stage and R-status thresholds
