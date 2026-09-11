# Authoritative OntoMed T-Box handbook

This freeze is compiled from `medical_case_schema_de_non_flat_v4.ttl`.
Class and property comments are verbatim T-Box text.
`valueKind` and `csvHeader` are the T-Box annotations used for encoding
and for TTL→CSV scoring. This is not occurrence/ownership mapping and
not the with-prompt constructor guidance.

## Class: `MedicalCase`

**Comment:**

Ein Fall / Aufenthalt / OP-Episode (eine Zeile in der strukturierten Erfassung). Verknüpft mit PatientInfo, CaseTimeline, SurgicalApproach, Procedure, SurgicalTeam, Diagnosis, Complication, PathologyOutcome.

### Object properties

| Property | Range |
|----------|-------|
| `hasComplication` | `Complication` |
| `hasDiagnosis` | `Diagnosis` |
| `hasPathologyOutcome` | `PathologyOutcome` |
| `hasPatientInfo` | `PatientInfo` |
| `hasProcedure` | `Procedure` |
| `hasSurgicalApproach` | `SurgicalApproach` |
| `hasSurgicalTeam` | `SurgicalTeam` |
| `hasTimeline` | `CaseTimeline` |

- `hasComplication`: Verknüpfung mit Komplikationen.
- `hasDiagnosis`: Verknüpfung mit Diagnosen.
- `hasPathologyOutcome`: Verknüpfung mit pathologischem Ergebnis.
- `hasPatientInfo`: Verknüpfung mit Patientenstammdaten.
- `hasProcedure`: Verknüpfung mit durchgeführten Eingriffen.
- `hasSurgicalApproach`: Verknüpfung mit operativem Zugang.
- `hasSurgicalTeam`: Verknüpfung mit Operateur und Assistent.
- `hasTimeline`: Verknüpfung mit zeitlichen Aufenthaltsdaten.

---

## Class: `PatientInfo`

**Comment:**

Patientenidentifikation und demografische Daten. Diese Felder sind aus patientenbezogenen Stammdaten, Fallkopf, Aufnahme-/Arztbrief- oder vergleichbaren Patientenidentifikationsstellen zu befuellen. Namen aus OP-Team-, Operateur-, Assistenz- oder Behandlerlisten duerfen NICHT als PatientInfo uebernommen werden, auch wenn sie in derselben Quelle prominent erscheinen.

### Datatype properties

#### `Alter`

- Range: `integer`; valueKind: `derived`; csvHeader: `Alter`

Das Alter bezieht sich auf das Alter zum Zeitpunkt der Operation. ABGELEITETER WERT: Es errechnet sich aus der Zeit zwischen Geburtsdatum und OP-Datum und muss nicht manuell befüllt werden.

#### `Fall_Nr`

- Range: `string`; valueKind: `free_text`; csvHeader: `Fall-Nr`

Die Fallnummer besteht aus 9 Ziffern und wird benutzt, um den Aufenthalt eines Patienten zu identifizieren. Ein Patient erhält für jeden Besuch eine Fallnummer.

#### `Geburtsdatum`

- Range: `string`; valueKind: `free_text`; csvHeader: `Geburtsdatum`

Das Geburtsdatum wird in folgendem Format angegeben: TT.MM.JJJJ (T=Tag, M=Monat, J=Jahr).

#### `Name`

- Range: `string`; valueKind: `free_text`; csvHeader: `Name`

PATIENTENNAME: Wenn ein patientenbezogener Kopf-/Stammdatenblock nahe Fall-Nr/Geburtsdatum einen Namen als `Nachname, Vorname` enthaelt, MUSS `Vorname Nachname` ausgegeben werden. Das Feld nicht weglassen, nur weil Fall_Nr oder Geburtsdatum bereits extrahiert wurden. Namen aus OP-Team-, Operateur-, Assistenz-, Behandler- oder Autorenlisten duerfen NICHT als PatientInfo uebernommen werden. Wert ist genau ein kanonischer Name, keine Alternativenliste. Beispiel: `Mustermann, Erika` -> `Erika Mustermann`.

---

## Class: `CaseTimeline`

**Comment:**

Zeitliche Daten zum Aufenthalt, OP und Entlassung.

### Datatype properties

#### `Dauer_d_zwischen_TuKo_und_OP`

- Range: `integer`; valueKind: `free_text`; csvHeader: `Dauer (d) zwischen TuKo und OP`

Die Dauer in Tagen (d) von der Tumorkonferenz bis zur OP. ABGELEITETER WERT: Berechnung aus präop TuKo und OP-Datum (erster Tag nach TuKo bis OP-Datum inklusive). Voraussetzung: präop TuKo muss befüllt sein.

#### `Entlassdatum`

- Range: `string`; valueKind: `free_text`; csvHeader: `Entlassdatum`

Das Entlassdatum wird in folgendem Format angegeben: TT.MM.JJJJ (T=Tag, M=Monat, J=Jahr). DATENQUELLE: QM-Bogen oder Arztbrief. Ohne diese Quellen ist das Feld nicht befüllbar (und damit Verweildauer nicht ableitbar).

#### `OP_Datum`

- Range: `string`; valueKind: `free_text`; csvHeader: `OP-Datum`

Das OP-Datum ist das Operationsdatum und wird in folgendem Format angegeben: TT.MM.JJJJ (T=Tag, M=Monat, J=Jahr).

#### `Same_Day_Surgery`

- Range: `string`; valueKind: `free_text`; csvHeader: `Same Day Surgery`

Bezeichnet den Fall, dass die stationäre Aufnahme am Tag der Operation erfolgt und nicht bereits am Tag zuvor. ABLEITBAR aus Aufnahmedatum (Arztbrief) und OP-Datum: Aufnahmedatum = OP-Datum → j, sonst n. DATENQUELLE: Aufnahmedatum muss im Arztbrief vorhanden sein; ohne diesen Eintrag kann das Feld nicht zuverlässig befüllt werden.

#### `Verweildauer`

- Range: `integer`; valueKind: `free_text`; csvHeader: `Verweildauer`

Die Verweildauer bezieht sich auf die Tage nach der Operation bis zur Entlassung und wird als volle Anzahl der Tage ohne Nachkommastellen angegeben. ABGELEITETER WERT: Berechnung aus OP-Datum und Entlassdatum (Tag nach OP bis Entlassdatum inklusive).

#### `praeop_TuKo`

- Range: `string`; valueKind: `free_text`; csvHeader: `präop TuKo`

Vorliegen mehrerer Konferenzen; die letzte Konferenz, welche unmittelbar vor der Operation (OP-Datum) stattgefunden hat. DATENQUELLE: Datum aus dem Tumorkonferenzbeschluss. Ohne diesen ist das Feld nicht befüllbar (und damit auch "Dauer (d) zwischen TuKo und OP" nicht ableitbar).

---

## Class: `SurgicalApproach`

**Comment:**

Operativer Zugang: offen, VATS, RATS. Alle drei Felder sind BINARY CHECKLIST Felder (valueKind=binary_checklist) und moeglichst gegenseitig ausschliessend. WICHTIG: Fuer einen Fall ist der final abgeschlossene Zugangsweg entscheidend; fruehe Setup- oder Zugangsbeschreibungen sind nachrangig, wenn spaeter eine klare finale Klassifikation oder Konversion beschrieben wird.

### Datatype properties

#### `RATS`

- Range: `string`; valueKind: `binary_checklist`; csvHeader: `RATS`

Roboter-assistierte Thorakoskopie. KEYWORDS: Robotische Schlüsselwörter (Roboter, robotisches OP-System, Roboterarm etc.). STARKE EVIDENZ sind auch indirekte, aber fallbezogen operative Formulierungen wie `Minimalinvasive Technik mit expliziter robotischer Assistenz`, `mit einem dokumentierten Robotersystem`, `Dokumentierte robotische Assistenz`, das Andocken/Ankoppeln der Roboterarme oder die Vorbereitung/Benutzung der Roboterkonsole, sofern diese den tatsaechlich durchgefuehrten Zugang des Falls beschreiben. Abgrenzung zu VATS: Sobald der eigentliche fallbezogene Zugang mit Roboter beschrieben wird, ist `RATS` zu setzen und NICHT `VATS`, auch wenn im selben Satz zugleich `Finaler thorakoskopischer Zugang ohne Konversion` oder `minimalinvasives Vorgehen` genannt wird. Wenn eine Konversion auf offenen Zugang beschrieben wird, gilt am Ende AUSSCHLIESSLICH `offen`; `RATS` darf dann NICHT zusaetzlich mit "1" befuellt werden. Entscheidend ist der finale durchgefuehrte Zugang, nicht bloss die Erwaehnung robotischer Unterstuetzung in einem verworfenen oder vorbereitenden Schritt. POSITIVBEISPIEL: `minimalinvasiv unter dokumentierter Roboterassistenz durchgeführt` -> `RATS = 1`. NEGATIVBEISPIEL: `Roboter wurde vorbereitet, finaler Zugang blieb offen` -> NICHT `RATS`, sondern nur `offen`. Liegen Kriterien fuer RATS vor: "1", sonst kein Eintrag.

#### `VATS`

- Range: `string`; valueKind: `binary_checklist`; csvHeader: `VATS`

VATS NUR bei finalem VATS-Zugang. NICHT setzen fuer `Minimalinvasive viszerale Pleuraresektion mit Lungen-Dekortikation`, wenn der Procedure-Kommentar diese Formulierung als offene Dekortikation/offen einordnet. Initiale thorakoskopische Passage allein reicht NICHT, wenn spaeter Konversion/final offen beschrieben wird (dann nur `offen`). Roboter -> RATS, nicht VATS. POSITIVBEISPIELE fuer finalen VATS-Zugang: `Finaler uniportaler VATS-Zugang ohne spätere Konversion` ohne Konversion; `Minimalinvasive Blutausschöpfung aus der Pleurahöhle`; minimalinvasive Sympathektomie mit Endoskopie/Weichhautschutz und ohne Konversion; Operationstext `Finaler thorakoskopischer Zugang ohne Konversion` als finaler Zugang. NEGATIVBEISPIEL: `Vorbereitende Markierung für einen geplanten uniportalen VATS-Zugang` gefolgt von `Explizite Konversionsentscheidung zur offenen Thorakotomie` -> NICHT `VATS`, sondern nur `offen`.

#### `offen`

- Range: `string`; valueKind: `binary_checklist`; csvHeader: `offen`

OFFEN PRIORITAET: Wenn ein Procedure-Kommentar denselben Quellbefund als offene Dekortikation/offen einordnet, dann `offen` = "1" und `VATS` leer lassen. Bei Pleuraempyem/Fibrothorax gilt die Formulierung `Minimalinvasive viszerale Pleuraresektion mit Lungen-Dekortikation` ohne explizite VATS-Dekortikation/OPS-Evidenz als `offen`. Sonstige starke Evidenz: Thorakotomie, Sternotomie, offener Zugang, Thorax wird offen eroefnet, Konversion auf offenen Zugang. Entscheidend ist der FINALE Zugang: Wenn der Fall mit einem uniportalen VATS-/Thorakoskopie-Setup beginnt, spaeter aber explizit zur Thorakotomie/offen konvertiert und offen abgeschlossen wird, gilt AUSSCHLIESSLICH `offen` = "1" und `VATS` bleibt leer. Nicht allein wegen Kamera/Thorakoskopie/minimal-invasiv setzen. POSITIVBEISPIEL: `Explizite intraoperative Konversion zur offenen Thorakotomie mit anschliessendem offenen Abschluss` nach initialer VATS-Passage -> `offen = 1`, `VATS` leer. NEGATIVBEISPIEL: rein minimalinvasive Eingriffe ohne dokumentierte Konversion zum offenen Zugang -> NICHT `offen`.

---

## Class: `Procedure`

**Comment:**

Durchgeführte operative Eingriffe (OPS-Codes). WICHTIG: Die meisten Procedure-Felder sind BINARY CHECKLIST Felder (valueKind=binary_checklist): bei Quellbeleg "1", sonst weglassen. BINARY/kanonische Eingriffsfelder haben Vorrang vor Freitext. Wenn der OP-Bericht eine eigene `Operation:`-, `Eingriff:`- oder sonstige Zusammenfassungszeile mit expliziten Eingriffsbezeichnungen enthaelt, ist diese fuer die Zuordnung der kanonischen Eingriffsfelder besonders stark zu gewichten. Solche explizit benannten kanonischen Eingriffe sollen erhalten bleiben und NICHT spaeter durch generische Freitexteintraege wie `sonst. (Eingriff)` verdraengt werden. `sonst. (Eingriff)` (valueKind=free_text_fallback) ist nur fuer explizite, nicht anderweitig abbildbare Eingriffe gedacht.

### Datatype properties

#### `Angio_Bronchoplastik`

- Range: `string`; valueKind: `binary_checklist`; csvHeader: `Angio-/Bronchoplastik`

Angio-/Bronchoplastik: zusätzliche Entfernung von Gefäß-/Bronchusgewebe (oft bei Tumorinfiltration) mit Nahtverbindung, typisch bei erweiterten Lobektomien. OPS-Code (Referenz): 5-324, 5-325. Liegt vor: "1", sonst keine Eintragung.

#### `Haematomausraeumung`

- Range: `string`; valueKind: `binary_checklist`; csvHeader: `Hämatomausräumung`

Hämatomausräumung: Entfernung von Blutergussbestandteilen aus der Thoraxhöhle. OPS-Code (Referenz): 5-340.c. Wenn `Explizite OP-Zusammenfassungszeile` / `Explizite Eingriffs-Zusammenfassungszeile` explizit Hämatomausräumung, Evakuation eines Hämatoms oder thorakoskopische/offene Hämatomausräumung nennt, MUSS dieses Feld "1" sein; der Zugang (VATS/offen) wird separat in SurgicalApproach kodiert und ersetzt dieses Feld nicht. POSITIVBEISPIELE: `Minimalinvasive Evakuation eines intrathorakalen Hämatoms aus der Pleurahöhle`; `Operative Entleerung eines intrathorakalen Hämatoms`. NEGATIVBEISPIEL: blosse Diagnose `Hämatothorax` ohne beschriebene Ausräumung/Evakuation -> KEIN Eintrag hier. Liegt vor: "1", sonst keine Eintragung.

#### `ICMB`

- Range: `string`; valueKind: `binary_checklist`; csvHeader: `ICMB`

ICMB (Intercostalmuskelbiopsie): Entfernung von Muskelgewebe aus dem Interkostalraum zur Probengewinnung. OPS-Code (Referenz): 1-502.4. Die explizite Abkuerzung `ICMB` in einer OP-Zusammenfassung oder Eingriffszeile gilt bereits als starke Evidenz, auch wenn sie nur als Bestandteil einer komma- oder semikolongetrennten Sammelzeile mit weiteren expliziten Eingriffen genannt wird. Liegt vor: "1", sonst keine Eintragung.

#### `Lobektomie_Bilobektomie_5_324_5_325`

- Range: `string`; valueKind: `binary_checklist`; csvHeader: `Lobektomie/Bilobektomie (5-324, 5-325)`

Eine Lobektomie/Bilobektomie ist eine Eingriffsart und bezeichnet das Entfernen eines Lungenlappens bzw. von zwei Lappen entlang der anatomischen Lappengrenze. Nachvollziehen laesst sich dies in der Regel aus dem OP-Bericht. NUR bei explizitem Hinweis auf Lobektomie, Bilobektomie oder Entfernung eines ganzen Lappens/einer ganzen Lappenkombination ist "1" einzutragen. NICHT hier eintragen, wenn lediglich Segmentresektion, atypische Resektion/Keilresektion oder andere nicht-lappenspezifische Eingriffe beschrieben sind. Der Zahlencode in Klammern bezeichnet den OPS-Code und kann als Hinweis auf die Resektion dienen. Er ist aber nicht immer vorhanden und nicht zwingend notwendig.

#### `Mediastinaltumorresektion_5_342`

- Range: `string`; valueKind: `binary_checklist`; csvHeader: `Mediastinaltumorresektion (5-342)`

Mediastinaltumorresektion: Entfernung eines Mediastinaltumors (Thymom, Thymuskarzinom, andere). Setzt i.d.R. Mediastinaltumor-Diagnose oder eine explizite mediastinale Tumorindikation voraus. OPS: 5-342. STARKE EVIDENZ sind explizite OP-Zusammenfassungen oder Berichtsstellen mit Benennungen wie Explizite Formulierungen einer mediastinalen Tumorentfernung oder gleichwertige Formulierungen einer mediastinalen Tumorentfernung. Formulierungen wie `Kombinierte totale Thymusentfernung mit en-bloc-Tumorresektion`, `Explizite Tumorresektion im vorderen Mediastinum`, `Resektion eines mediastinalen Raumforderungstumors` oder gleichwertige kombinierte OP-Zeilen gelten ebenfalls als starke Evidenz fuer dieses Feld. Wenn eine Thymektomie explizit gemeinsam mit einer en bloc Tumorresektion eines mediastinalen Tumors beschrieben wird, koennen beide kanonischen Eingriffsfelder gleichzeitig vorliegen; `Thymektomie` ersetzt dieses Feld dann nicht. Nicht ausreichend sind bloss allgemeine Freilegungs-, Mobilisations- oder Praeparationsformeln ohne eigentliche Tumorentfernung. Liegt vor: "1", sonst keine Eintragung.

#### `Mediastinoskopie_1_586_3_1_691_1_5_401_7_5_402_d`

- Range: `string`; valueKind: `binary_checklist`; csvHeader: `Mediastinoskopie (1-586.3, 1-691.1, 5-401.7, 5-402.d)`

Mediastinoskopie: minimal-invasives endoskopisches Verfahren zur Spiegelung des Mittelfellraums, Biopsie von Lymphknoten/Tumoren. HINWEIS: Mediastinoskopie ist typischerweise NICHT als offener Zugang zu klassifizieren. OPS: 1-586.3, 1-691.1, 5-401.7, 5-402.d. Liegt vor: "1", sonst keine Eintragung.

#### `Pleurektomie_5_344_1_2_u_4_5`

- Range: `string`; valueKind: `binary_checklist`; csvHeader: `Pleurektomie (5-344.1-2 u 4-5)`

Eine Pleurektomie ist eine Eingriffsart und bezeichnet das Entfernen der parietalen Pleura von der Brustwand. Hierbei kann die Pleura lokal (Probeexzision), subtotal oder total erfolgen. Alle diese Eingriffsarten fallen unter die Kategorie Pleurektomie. Nachvollziehen lässt sich dies in der Regel aus dem OP-Bericht. Liegt eine Pleurektomie vor ist in die Spalte eine "1" einzutragen. Andernfalls erfolgt keine Eintragung. Es können mehrere Eingriffsarten gleichzeitig vorliegen. Der Zahlencode in Klammern bezeichnet den OPS-Code und kann als Hinweis auf die Resektion dienen.

#### `Pleurodese_5_345_ohne_5_345_1_und_ohne_5_345_4`

- Range: `string`; valueKind: `binary_checklist`; csvHeader: `Pleurodese (5-345 ohne 5-345.1 und ohne 5-345.4)`

Pleurodese: Verkleben der Pleurablätter (viszeral/parietal), z.B. bei chronischem Erguss oder Pneumothorax; chemisch (Talkum) oder mechanisch (Pleurektomie). Kann MIT Pleurektomie vorliegen (beide "1"), NICHT mit Dekortikation. OPS: 5-345 ohne 5-345.1 und 5-345.4. Liegt vor: "1", sonst keine Eintragung.

#### `Pneumonektomie_5_327`

- Range: `string`; valueKind: `binary_checklist`; csvHeader: `Pneumonektomie (5-327)`

Eine Pneumonektomie ist eine Eingriffsart und bezeichnet das Entfernen von Lungengewebe, welches einem kompletten Lungenflügel entspricht. Nachvollziehen lässt sich dies in der Regel aus dem OP-Bericht. Liegt eine Pneumonektomie vor ist in die Spalte eine "1" einzutragen. Andernfalls erfolgt keine Eintragung. Es können mehrere Eingriffsarten gleichzeitig vorliegen. Der Zahlencode in Klammern bezeichnet den OPS-Code und kann als Hinweis auf die Resektion dienen. Er ist aber nicht immer vorhanden und nicht zwingend notwendig.

#### `Segmenresektion_5_323_4_7`

- Range: `string`; valueKind: `binary_checklist`; csvHeader: `Segmenresektion (5-323.4-7)`

Eine Segmentresektion ist eine Eingriffsart und bezeichnet das Entfernen von Lungengewebe entlang der anatomischen Segmentgrenze. Nachvollziehen laesst sich dies in der Regel aus dem OP-Bericht. NUR bei explizitem Hinweis auf Segmentresektion, Segmentektomie, Bisegmentektomie oder Trisegmentektomie ist "1" einzutragen. Auch gebräuchliche OP-Kurzformen wie `Segmentnummer-Resektion (z. B. numerisches Segmentkürzel)`, `Segmentnummer-Resektionskürzel (z. B. numerisches Segmentkürzel)`, `Anatomische Segment-Resektion (z. B. ein benanntes Segment)` oder entsprechende segmentbezogene anatomische Resektionskürzel gelten als starke Evidenz, sofern sie fallbezogen den durchgefuehrten Eingriff bezeichnen. NICHT hier eintragen, wenn lediglich atypische Resektion/Keilresektion oder Lobektomie/Bilobektomie beschrieben ist. Erfolgt das Entfernen von mehreren Segmenten waehrend einer Operation, bleibt es beim Eintrag "1". Der Zahlencode in Klammern bezeichnet den OPS-Code und kann als Hinweis auf die Resektion dienen. Er ist aber nicht immer vorhanden und nicht zwingend notwendig.

#### `Thoraxdrainageneinlage_8_144_0_und_5_340_0`

- Range: `string`; valueKind: `binary_checklist`; csvHeader: `Thoraxdrainageneinlage (8-144.0 und 5.340.0)`

Thoraxdrainageneinlage als EIGENER Eingriff (offene oder kleinlumige Drainage). NUR dann "1", wenn die Drainage im Fall als eigenstaendige interventionelle oder operative Massnahme beschrieben ist. NICHT eintragen bei routinemaessiger postoperativer Drainage, bei Abschluss-/Standarddrainagen nach einem anderen thoraxchirurgischen Haupteingriff oder wenn die Drainage nur als Teil des ueblichen Wundverschluss-/Reventilations-/Ausleitungsschritts beschrieben wird. Formulierungen wie Routine-Drainageeinlage im Trokarzugang mit Soganschluss und Fixation, Spuelsystem zur Prophylaxe oder blosse Nennung von Lavage und Drainage innerhalb der Abschlussbeschreibung eines anderen Haupteingriffs reichen fuer dieses Feld allein NICHT aus. Auch eine reine Abschlussformel wie Seitenbezogene Abschluss-Drainage nach Feld-/Ventilationskontrolle ist in der Regel KEINE starke Evidenz fuer dieses Feld, solange die Quelle die Drainage nicht als eigenen zusaetzlichen Eingriff herausstellt. Insbesondere eine sequenzielle Abschlussbeschreibung wie `Standardisierte postoperative Thoraxdrainage im Wundverschluss`, danach `Fixation der Drainage`, `Anschluss der Drainage an Sog`, `Reventilation nach Drainageeinlage`, `Extubation im OP-Abschluss` oder Verlegung in den Aufwachraum spricht REGELHAFT fuer routinemaessige Abschlussdrainage und NICHT fuer dieses Feld. POSITIVBEISPIEL: `Drainageanlage als ausdrücklich separater interventioneller Eingriff` oder `Separat implantiertes permanentes Pleuradrainagesystem als Zusatzeingriff` -> `1`. NEGATIVBEISPIEL: `Abschlusssequenz Drainage/Sog/Extubation ohne eigenständigen Drainageeingriff` -> KEIN Eintrag. AUSNAHME: Permanente Verweildrainagesysteme, sofern als separater Zusatzeingriff dokumentiert koennen zusaetzlich zu anderem Eingriff stehen. OPS: 8-144.0, 5-340.0. In typischen Thoraxchirurgie-Faellen ist dies haeufig "nein". Liegt vor: "1", sonst keine Eintragung.

#### `Thymektomie`

- Range: `string`; valueKind: `binary_checklist`; csvHeader: `Thymektomie`

Thymektomie: Entfernung der Thymusdrüse. Indikation: Myasthenia gravis oder en-bloc bei Mediastinaltumor. OPS-Code (Referenz): 5-077. STARKE EVIDENZ sind explizite OP-Zusammenfassungen oder Eingriffszeilen mit Formulierungen wie `Thymektomie`, `Totale Entfernung der Thymusdrüse als benannter Haupteingriff`, `Erweiterte Thymusentfernung als benannter Eingriff` oder gleichwertige explizite Benennungen der Entfernung der Thymusdruese. Bei gleichzeitigem Vorliegen von Mediastinaltumorresektion: beide Spalten "1"; eine kombinierte Formulierung wie `Kombinierte Thymusentfernung mit en-bloc-Tumorresektion` ist daher fuer BEIDE Felder starke Evidenz. Liegt vor: "1", sonst keine Eintragung.

#### `Trachearesektion_und_andere_Eingriffe_an_der_Trachea_5_314_5_316_5_319`

- Range: `string`; valueKind: `binary_checklist`; csvHeader: `Trachearesektion und andere Eingriffe an der Trachea (5-314, 5-316, 5-319)`

Eine Trachearesektion und andere Eingriffe an der Trachea beinhaltet jeden Eingriff, der die Trachea betrifft. Ausgenommen sind Bronchoplastiken, die unter Angio-/Bronchoplastik subsumiert werden. Nachvollziehen lässt sich dies in der Regel aus dem OP-Bericht. Liegt eine Trachearesektion und andere Eingriffe an der Trachea vor ist in die Spalte eine "1" einzutragen. Andernfalls erfolgt keine Eintragung. Es können mehrere Eingriffsarten gleichzeitig vorliegen. Der Zahlencode in Klammern bezeichnet den OPS-Code und kann als Hinweis auf die Resektion dienen.

#### `VAMLA_5_404_8`

- Range: `string`; valueKind: `binary_checklist`; csvHeader: `VAMLA (5-404.8)`

VAMLA (Video-assistierte mediastinoskopische Lymphadenektomie): Lymphknotenentfernung im Mediastinum unter Videokontrolle, v.a. Staging NSCLC. HINWEIS: VAMLA erfordert Zugangsweg VATS oder RATS (nicht offen). OPS: 5-404.8. Liegt vor: "1", sonst keine Eintragung.

#### `VATS_Dekortikation_5_344_3_5_345_4`

- Range: `string`; valueKind: `binary_checklist`; csvHeader: `VATS Dekortikation (5-344.3, 5-345.4)`

NEGATIVREGEL: `Minimalinvasive viszerale Pleuraresektion mit Lungen-Dekortikation` ist ohne explizite `Explizit benannte Explizit benannte VATS-Dekortikation` oder VATS-spezifischen OPS-Code KEINE VATS_Dekortikation. Nur setzen, wenn Dekortikation vorliegt UND der finale Zugang ausdruecklich VATS/RATS und nicht offen ist. Wenn offene_Dekortikation fuer denselben Befund greift, dieses Feld leer lassen.

#### `atypische_Resektion_5_322`

- Range: `string`; valueKind: `binary_checklist`; csvHeader: `atypische Resektion (5-322)`

Eine atypische Resektion (syn. Keilresektion, Wedgeresektion) ist eine Eingriffsart und bezeichnet das Entfernen von Lungengewebe, welches KEINEN anatomischen Segment- oder Lappengrenzen folgt. Nachvollziehen laesst sich dies in der Regel aus dem OP-Bericht. Wenn der Block `Explizite OP-Zusammenfassungszeile` / `Explizite Eingriffs-Zusammenfassungszeile` eine explizite Zeile mit atypischer Resektion/Keilresektion enthaelt, MUSS dieses Feld "1" sein, auch wenn zusaetzlich Adhaesiolyse, Konversion oder andere Begleitschritte genannt werden. NUR bei explizitem Hinweis auf Keilresektion/Wedgeresektion/atypische Resektion oder klar nicht-anatomische Resektion ist "1" einzutragen. NICHT hier eintragen, wenn stattdessen explizit Segmentresektion, Lobektomie/Bilobektomie oder Pneumonektomie beschrieben ist. Es koennen mehrere Eingriffsarten gleichzeitig vorliegen, aber eine anatomische Resektion allein ist KEIN Beleg fuer atypische Resektion. POSITIVBEISPIEL: `Explizite OPS-Zeile: mehrfache offene Keilresektion/atypische Lungenresektion` -> "1". Der Zahlencode in Klammern bezeichnet den OPS-Code und kann als Hinweis auf die Resektion dienen. Er ist aber nicht immer vorhanden und nicht zwingend notwendig.

#### `erw_Pneumonektomie_5_328`

- Range: `string`; valueKind: `binary_checklist`; csvHeader: `erw. Pneumonektomie (5-328)`

Eine erw. (erweiterte) Pneumonektomie ist eine Eingriffsart und bezeichnet das Entfernen von Lungengewebe, welches einem kompletten Lungenflügel entspricht. Erweitert ist die Resektion, wenn es zur Mitresektion benachbarter Strukturen wie Anteile des Diaphragmas oder des Perikards u.a. kommt. Bei Befall der Pleura wird der Lungenflügel mit dem Lungenfell en bloc entnommen (extrapleurale Pneumonektomie). Nachvollziehen lässt sich dies in der Regel aus dem OP-Bericht. Liegt eine erw. Pneumonektomie vor ist in die Spalte eine "1" einzutragen. Andernfalls erfolgt keine Eintragung. Es können mehrere Eingriffsarten gleichzeitig vorliegen. Der Zahlencode in Klammern bezeichnet den OPS-Code und kann als Hinweis auf die Resektion dienen. Er ist aber nicht immer vorhanden und nicht zwingend notwendig.

#### `expl_Thorakotomie`

- Range: `string`; valueKind: `binary_checklist`; csvHeader: `expl. Thorakotomie`

Explorative Thorakotomie: chirurgische Öffnung des Thorax zur direkten Untersuchung (Lunge, Herz, Speiseröhre), Diagnosestellung bei unklaren Befunden. OPS-Code (Referenz): 5-340.1. Liegt vor: "1", sonst keine Eintragung.

#### `offene_Dekortikation_5_344_0_5_344_11_5_344_13_5_345_1`

- Range: `string`; valueKind: `binary_checklist`; csvHeader: `offene Dekortikation (5-344.0, 5-344.11, 5-344.13, 5-345.1)`

PRIORITAET: Bei Pleuraempyem/Fibrothorax ist `Minimalinvasive viszerale Pleuraresektion mit Lungen-Dekortikation` als offene Dekortikation zu werten: dieses Feld = "1" und SurgicalApproach.offen = "1". Nicht als VATS_Dekortikation werten, ausser die Quelle nennt explizit `Explizit benannte Explizit benannte VATS-Dekortikation` oder einen VATS-spezifischen OPS-Code. Offene Dekortikation bedeutet Entfernung der viszeralen Pleura/Dekortikation der Lunge und ist stets als offen zu klassifizieren.

#### `plast_Rekonstruktion_der_Brustwand_5_346`

- Range: `string`; valueKind: `binary_checklist`; csvHeader: `plast. Rekonstruktion der Brustwand (5-346)`

Eine plast. (plastische) Rekonstruktion der Brustwand ist eine Eingriffsart, bei der die Brustwand rekonstruiert wird. Die Rekonstruktion beschreibt hierbei eine Wiederherstellung der Brustwand, die über einen reinen Wundverschluss hinausgeht. Meist werden Fremdmaterialien wie Netze oder Rippenosteosynthesematerial verwendet. Auch die plastische Deckung (bspw. Lappenplastik) fällt unter diese Eingriffsart. Nachvollziehen lässt sich dies in der Regel aus dem OP-Bericht. Liegt eine plast. Rekonstruktion der Brustwand vor ist in die Spalte eine "1" einzutragen. Andernfalls erfolgt keine Eintragung. Es können mehrere Eingriffsarten gleichzeitig vorliegen. Der Zahlencode in Klammern bezeichnet den OPS-Code und kann als Hinweis auf die Resektion dienen.

#### `sonst_Eingriff`

- Range: `string`; valueKind: `free_text_fallback`; csvHeader: `sonst. (Eingriff)`

FREITEXT-FALLBACK fuer Eingriffe, die keine der anderen genannten erfuellen. Nur eine explizit genannte, fallbezogene, abgeschlossene operative Handlung eintragen, wenn kein kanonisches Eingriffsfeld passt. Kanonische Eingriffsfelder gehen vor. Keine blossen Praeparations-, Mobilisations-, Darstellungs- oder Zugangs-/Setup-Schritte eintragen. Begleitende Zusatzschritte wie eine mit einer kanonischen Resektion zusammen genannte LAD/Lymphadenektomie sollen hier nicht eingetragen werden, wenn dafuer kein eigenes Zielfeld vorgesehen ist und der Haupt-Eingriff bereits anderweitig kodiert wird. Ebenso sollen begleitende Loesungs-, Adhaesiolyse-, Freilegungs- oder Mobilisationshandlungen hier NICHT eingetragen werden, wenn sie nur als Teil der Durchfuehrung eines bereits kanonisch kodierten Haupteingriffs beschrieben werden und nicht als eigenstaendiger separater Eingriff auftreten. Nur wenn die Quelle die Handlung als eigenen abgeschlossenen Eingriff mit eigenstaendigem fallbezogenem Gewicht beschreibt und kein kanonisches Feld passt, darf sie hier stehen. Der Wert soll die kuerzeste passende Originalphrase bleiben. POSITIVBEISPIEL: `Eingriff zur thorakalen Denervierung des Sympathikus` / `Eingriff zur thorakalen Denervierung des Sympathikus` als eigener Operationseintrag ohne passendes kanonisches Feld -> hier eintragen (nicht weglassen). NEGATIVBEISPIEL: `Pleurolyse`/`Adhäsiolyse` nur als Begleitschritt zu einer bereits kanonisch kodierten Resektion -> NICHT hier. OPS-Referenz: https://klassifikationen.bfarm.de/ops/

---

## Class: `SurgicalTeam`

**Comment:**

Operateur und Assistent. Die Rollen sind getrennt zu halten; Personen duerfen nicht allein wegen ihrer Naehe im Dokument oder gemeinsamer Auflistung zusammengelegt werden. Wenn der OP-Bericht eine explizite Rollenstruktur (Operateur/In, Assistenz) vorgibt, ist diese gegenueber unstrukturierten Namenslisten vorrangig. OCR-/Layout-Artefakte sind mitzudenken: Wenn Rollenlabels sichtbar sind, die zugehoerigen Inline-Werte aber nur aus Titeln/Platzhaltern bestehen (z.B. `Dr. med.`) und unmittelbar daneben oder darunter eine kleine, geordnete Namensliste im selben Teamkopf erscheint, darf diese Namensliste zur Rollenbefuellung herangezogen werden. In diesem Sonderfall gilt die Quellreihenfolge: erster Name = Operateur, zweiter Name = Assistent, sofern keine entgegenstehende explizite Rollenangabe existiert.

### Datatype properties

#### `Assistent_in`

- Range: `string`; valueKind: `free_text`; csvHeader: `Assistent/in`

Der Assistent assistiert die Operation. Er ist auf dem OP-Bericht als Assistent vermerkt. Eingetragen wird er als Nachname in die Spalte. Titel, Vornamen und Rollenbezeichnungen sollen nicht uebernommen werden, soweit der Nachname eindeutig bestimmbar ist. Es kann mehrere Assistenten geben; dann wird die Spalte mit Nachname Assistent 1 / Nachname Assistent 2 / usw. ausgefuellt, getrennt durch ` / ` in stabiler Quellreihenfolge. Ein Name darf nur dann hier eingetragen werden, wenn die Quelle die Assistenzrolle explizit zuordnet oder eindeutig als Assistenzliste kennzeichnet. Ein bloss zweiter Name in einer Operateur-Zeile oder in einer unlabeled Teamliste unter leer gebliebenen Rollenfeldern reicht dafuer nicht aus. Wenn sichtbare `Operateur/In`- und `Assistenz`-Labels vorliegen, deren Inline-Werte aber wegen OCR-/Layout-Bruch nur aus Titeln/Platzhaltern bestehen, und dieselbe Teamsektion unmittelbar danach eine kurze geordnete Namensliste fortsetzt, ist der zweite dort genannte Personenname als Assistent zu uebernehmen, sofern keine widersprechende explizite Rollenangabe existiert. Operateur und Assistent duerfen nicht in demselben Feld zusammengezogen werden.

#### `Operateur_in`

- Range: `string`; valueKind: `free_text`; csvHeader: `Operateur/in`

Der Operateur fuehrt die Operation durch. Er ist auf dem OP-Bericht als Operateur vermerkt. Eingetragen wird er als Nachname in die Spalte. Titel, Vornamen und Rollenbezeichnungen sollen nicht uebernommen werden, soweit der Nachname eindeutig bestimmbar ist; wenn der Operateur-Wert aus Titeln plus Personenname besteht (z.B. `Titel ohne Personenname` oder `Prof. Dr. Nachname`), ist der Titel zu verwerfen, aber der Nachname zu behalten. Ein Feld darf nicht als leer behandelt werden, nur weil vor dem Namen akademische Titel stehen. Es kann mehrere Operateure geben; dann wird die Spalte mit Nachname Operateur 1 / Nachname Operateur 2 / usw. ausgefuellt, getrennt durch ` / ` in stabiler Quellreihenfolge. Wenn die Quelle mehrere Namen gemeinsam als Operateur-Eintrag oder auf einer unlabeled Teamzeile unter leer gebliebenen `Operateur/In`- und `Assistenz`-Feldern auffuehrt, sollen alle diese Namen im Operateur-Feld verbleiben; der zweite Name darf nicht allein wegen seiner Position zu `Assistent/in` umgedeutet werden. Semikolon- oder komma-getrennte Namenslisten in einer Operateur-Zeile (z.B. `Mehrere formatierte Namenszeilen im Teamkopf`) zaehlen als gemeinsamer Operateur-Eintrag; daraus sollen die Nachnamen in stabiler Quellreihenfolge als `Nachname1 / Nachname2` uebernommen werden. Eine Aufteilung auf `Assistent/in` ist nur zulaessig, wenn die Quelle die Assistenzrolle explizit zuordnet. Wenn ein sichtbares `Operateur/In`-Label wegen OCR-/Layout-Bruch keinen Namen, sondern nur Titel/Platzhalter traegt, und eine benachbarte/untergeordnete kurze Namensliste denselben Teamkopf fortsetzt, ist der erste dort genannte Personenname als Operateur zu uebernehmen. Operateur und Assistent duerfen nicht in demselben Feld zusammengezogen werden.

---

## Class: `Diagnosis`

**Comment:**

Diagnosen des Falls. Die meisten Diagnosis-Felder sind BINARY CHECKLIST Felder (valueKind=binary_checklist): bei Quellbeleg "1", sonst weglassen. BINARY/kanonische Diagnosefelder haben Vorrang vor Freitext-Fallbacks. Explizite fallbezogene `Diagnose:`-, `Indikation:`- oder vergleichbare ueberschriebene Diagnosezeilen sind starke Evidenz. Wo ein kanonisches Diagnosefeld passt, ist dieses gegenueber Freitext-Fallbacks zu bevorzugen. `sonst. (Diagnose)` (valueKind=free_text_fallback) ist nur fuer explizite, nicht kanonisch abdeckbare Diagnosen zu verwenden.

### Datatype properties

#### `Art_der_Metastasen`

- Range: `string`; valueKind: `free_text_fallback`; csvHeader: `Art der Metastasen`

Art der Metastasen: BEDINGT. Freitext für Primärtumor bzw. Ergebnis der histologischen Untersuchung. NUR ausfüllen, wenn Metastasen = "1". Datenquelle: histologischer Befund.

#### `Art_des_Mediastinaltumors`

- Range: `string`; valueKind: `free_text_fallback`; csvHeader: `Art des Mediastinaltumors`

Art des Mediastinaltumors: BEDINGT. Freitext für Tumortyp bzw. histologisches Ergebnis. NUR ausfüllen, wenn andere Mediastinaltumoren = "1". Datenquelle: histologischer Befund.

#### `Destroyed_Lung`

- Range: `string`; valueKind: `binary_checklist`; csvHeader: `Destroyed Lung`

Destroyed Lung: Zerstörung des Lungenparenchyms durch Infektion und Abszedierung. Ersichtlich aus OP-Bericht oder Arztbrief. Starke Evidenz sind fallbezogene Formulierungen wie Schwere Parenchymzerstörung, Nekrose/Gangrän, Hepatisation oder vergleichbare destroyed-lung-Muster im Sinne einer destroyed lung. POSITIVBEISPIEL: Diagnose `Schwere Parenchymzerstörung mit Gangrän/Nekrose als OP-Indikation` oder `Akute Hepatisation mit Parenchymzerstörung mit Parenchymzerstörung` mit destruiertem Parenchym als OP-Indikation -> "1". NEGATIVBEISPIEL: blosse lokale Parenchymverletzung bei Adhäsiolyse ohne destroyed-lung-/Gangrän-/Nekrose-Diagnose -> KEIN Eintrag. Liegt vor: "1", sonst keine Eintragung.

#### `Empyem_Diagnose`

- Range: `string`; valueKind: `binary_checklist`; csvHeader: `Empyem (Diagnose)`

Ein Empyem bezeichnet eine Eiteransammlung der Pleura, sprich ein Pleuraempyem, und ist eine Diagnose. Ersichtlich ist die Diagnose in der Regel aus dem OP-Bericht oder dem Arztbrief. Liegt ein Empyem vor ist in die Spalte eine "1" einzutragen. Andernfalls erfolgt keine Eintragung. Es kann mehrere Diagnosen gleichzeitig geben.

#### `MG`

- Range: `string`; valueKind: `binary_checklist`; csvHeader: `MG`

Eine MG (Myasthenia gravis) ist eine Diagnose. Ersichtlich ist die Diagnose in der Regel aus dem OP-Bericht oder dem Arztbrief. "1" NUR dann eintragen, wenn Myasthenia gravis/MG im Fall explizit genannt ist; nicht allein aus einer Thymektomie ableiten. Eine explizit genannte Indikations- oder Verdachtsdiagnose des Falls zaehlt. Andernfalls erfolgt keine Eintragung. Es kann mehrere Diagnosen gleichzeitig geben.

#### `Mesotheliom`

- Range: `string`; valueKind: `binary_checklist`; csvHeader: `Mesotheliom`

Ein Mesotheliom bezeichnet einen bösartigen Tumor, der der Pleura entspringt und ist eine Diagnose. Ersichtlich ist die Diagnose in der Regel aus dem OP-Bericht oder dem Arztbrief. Liegt ein Mesotheliom vor ist in die Spalte eine "1" einzutragen. Andernfalls erfolgt keine Eintragung. Es kann mehrere Diagnosen gleichzeitig geben.

#### `Metastasen`

- Range: `string`; valueKind: `binary_checklist`; csvHeader: `Metastasen`

Metastasen: Absiedlungen eines anderen Primärtumors. NUR Lungenmetastasen (nicht anderer Organtumor). Art des Primärtumors → Art_der_Metastasen (wenn hier "1"). Ersichtlich aus OP-Bericht oder Arztbrief. Es braucht eine fallbezogen explizite Metastasen-/Absiedlungsdiagnose. Unsichere Schnellschnitt-/Frozen-Section-Aussagen wie `Unklare Schnellschnitt-Aussage ohne gesicherte Metastasenentscheidung` reichen allein NICHT aus. POSITIVBEISPIEL: explizite Diagnose `Explizite Lungenmetastasen-Diagnose` / `Explizite Metastasen-Diagnose mit benanntem Primärtumor` -> "1". NEGATIVBEISPIEL: unklare Raumforderung mit Schnellschnitt ohne Metastasenentscheid -> KEIN Eintrag. Liegt vor: "1", sonst keine Eintragung.

#### `NET`

- Range: `string`; valueKind: `binary_checklist`; csvHeader: `NET`

Der NET (Neuroendokriner Tumor) bezeichnet eine Diagnose. Ersichtlich ist die Diagnose in der Regel aus dem OP-Bericht oder dem Arztbrief. Liegt ein NET vor ist in die Spalte eine "1" einzutragen. Andernfalls erfolgt keine Eintragung. Es kann mehrere Diagnosen gleichzeitig geben.

#### `NSCLC`

- Range: `string`; valueKind: `binary_checklist`; csvHeader: `NSCLC`

Das NSCLC (non-small cell lung cancer, nicht-kleinzelliges Lungenkarzinom) bezeichnet eine Diagnose. Ersichtlich ist die Diagnose in der Regel aus dem OP-Bericht oder dem Arztbrief. "1" NUR dann eintragen, wenn NSCLC oder eine gleichbedeutende fallbezogene Diagnosebezeichnung explizit genannt ist; nicht allein aus Lobektomie, Staging oder allgemeinem Tumorverdacht ableiten. Eine explizit genannte praeoperative oder postoperative Fall-Diagnose zaehlt. Andernfalls erfolgt keine Eintragung. Es kann mehrere Diagnosen gleichzeitig geben.

#### `Pleuraerguss`

- Range: `string`; valueKind: `binary_checklist`; csvHeader: `Pleuraerguss`

Ein Pleuraerguss bezeichnet eine Flüssigkeitsansammlung im Pleuraspalt und ist eine Diagnose. Ersichtlich ist die Diagnose in der Regel aus dem OP-Bericht oder dem Arztbrief. Wichtig hierbei ist, dass der Pleuraerguss der Grund für die Operation sein muss. Oft kann es nach einer Operation zu verbleibender Flüssigkeit im Pleuraspalt kommen, die kein Grund für eine Intervention oder eine Operation ist. Dies ist hier explizit nicht gemeint, auch wenn die Diagnose im Arztbrief auftauchen kann. Liegt ein Pleuraerguss vor ist in die Spalte eine "1" einzutragen. Andernfalls erfolgt keine Eintragung. Es kann mehrere Diagnosen gleichzeitig geben.

#### `Pneumothorax_Diagnose`

- Range: `string`; valueKind: `binary_checklist`; csvHeader: `Pneumothorax (Diagnose)`

Ein Pneumothorax bezeichnet eine Luftansammlung im Pleuraspalt und ist eine Diagnose. Ersichtlich ist die Diagnose in der Regel aus dem OP-Bericht oder dem Arztbrief. Wichtig hierbei ist, dass der Pneumothorax der Grund für die Operation sein muss. Oft kann es nach einer Operation zu verbleibender Luft im Pleuraspalt kommen, die kein Grund für eine Intervention oder eine Operation ist. Dies ist hier explizit nicht gemeint, auch wenn die Diagnose im Arztbrief auftauchen kann. Liegt ein Pneumothorax vor ist in die Spalte eine "1" einzutragen. Andernfalls erfolgt keine Eintragung. Es kann mehrere Diagnosen gleichzeitig geben.

#### `SCLC`

- Range: `string`; valueKind: `binary_checklist`; csvHeader: `SCLC`

Das SCLC (small cell lung cancer, kleinzelliges Lungenkarzinom) bezeichnet eine Diagnose. Ersichtlich ist die Diagnose in der Regel aus dem OP-Bericht oder dem Arztbrief. Liegt eine SCLC vor ist in die Spalte eine "1" einzutragen. Andernfalls erfolgt keine Eintragung. Es kann mehrere Diagnosen gleichzeitig geben.

#### `Thoraxtrauma_Haematothorax_Rippenfraktur`

- Range: `string`; valueKind: `binary_checklist`; csvHeader: `Thoraxtrauma (Hämatothorax, Rippenfraktur)`

Ein Thoraxtrauma bezeichnet alle Formen der Verletzungen, die nach Trauma im Thoraxbereich auftreten können und ist eine Diagnose. Häufige Beispiele sind der Hämatothorax (Blut im Thorax) oder die Rippenfraktur. Ersichtlich ist die Diagnose in der Regel aus dem OP-Bericht oder dem Arztbrief. Liegt ein Thoraxtrauma (Hämatothorax, Rippenfraktur) vor ist in die Spalte eine "1" einzutragen. Andernfalls erfolgt keine Eintragung. Es kann mehrere Diagnosen gleichzeitig geben.

#### `Thymom`

- Range: `string`; valueKind: `binary_checklist`; csvHeader: `Thymom`

Thymom: Tumor aus Thymusgewebe. GILT AUCH bei explizit fallbezogenem bloessem Verdacht oder Arbeitsdiagnose (z.B. praeoperativ) – dann "1" eintragen. Explizite Diagnose-/Indikationszeilen des Falls sind starke Evidenz. Nicht aus allgemeinem Mediastinaltumor, Thymektomie oder unspezifischer Raumforderung allein ableiten. Wenn im Fall stattdessen nur `Thymuskarzinom als explizite Diagnose` explizit genannt ist und kein Thymom/Verdacht auf Thymom vorkommt, hier KEIN Eintrag. Ersichtlich aus OP-Bericht oder Arztbrief. Liegt vor: "1", sonst keine Eintragung.

#### `Thymus_Ca`

- Range: `string`; valueKind: `binary_checklist`; csvHeader: `Thymus-Ca`

Thymus-Ca (Thymuskarzinom): boesartiger Tumor aus Thymusgewebe, aggressiver als Thymom. Abgrenzung Thymom vs. Thymus-Ca erfolgt histologisch oder durch explizite fallbezogene Benennung. Explizite Diagnose-/Indikationszeilen oder ausdrueckliche Verdachts-/Arbeitsdiagnosen des Falls sind starke Evidenz. "1" nur dann eintragen, wenn Thymuskarzinom/Thymus-Ca im Fall explizit genannt ist; nicht allein aus allgemeinem Mediastinaltumor oder Thymektomie ableiten. Andernfalls keine Eintragung.

#### `andere_Mediastinaltumoren`

- Range: `string`; valueKind: `binary_checklist`; csvHeader: `andere Mediastinaltumoren`

Andere Mediastinaltumoren bezeichnen Tumoren, deren Ursprung das Mediastinum ist, die weder unter die Kategorie Thymom oder Thymuskarzinom fallen und bezeichnen eine Diagnose. Beispiele hierfür sind z.B. das Teratom oder der M. Castleman. Liegt eine andere Mediastinaltumoren vor ist in die Spalte eine "1" einzutragen. Andernfalls erfolgt keine Eintragung. Es kann mehrere Diagnosen gleichzeitig geben.

#### `interstitielle_Lungenerkrankung`

- Range: `string`; valueKind: `binary_checklist`; csvHeader: `interstitielle Lungenerkrankung`

Eine interstitielle Lungenerkrankung bezeichnet eine Lungengerüsterkrankung und ist eine Diagnose. Beispiele für eine interstitielle Lungenerkrankung sind z.B. COPD oder Lungenfibrose. Ersichtlich ist die Diagnose in der Regel aus dem OP-Bericht oder dem Arztbrief. Liegt eine interstitielle Lungenerkrankung vor ist in die Spalte eine "1" einzutragen. Andernfalls erfolgt keine Eintragung. Es kann mehrere Diagnosen gleichzeitig geben.

#### `sonst_Diagnose`

- Range: `string`; valueKind: `free_text_fallback`; csvHeader: `sonst. (Diagnose)`

FREITEXT-FALLBACK fuer explizite Diagnosen, die kein kanonisches Diagnosefeld abdeckt. In einer Diagnose-/Indikationsliste deckt ein kanonisches Feld nur den passenden Listeneintrag ab; separate nicht-kanonische Listeneintraege muessen hier erhalten bleiben. Beispiel: `Pleuraempyem als kanonische Empyem-Diagnose` -> Empyem_Diagnose, aber separater Eintrag `Fibrothorax als separater nicht-kanonischer Diagnoseeintrag` -> `Fibrothorax als nicht-kanonischer Diagnoseeintrag`. Bei Adhaesions-/Verwachsungsdiagnosen nur den Kernbegriff ohne Seitenangabe ausgeben, z.B. `Lungenverwachsungen (seitenbezogen im Quelltext)` -> `Lungenadhaesionen`. Keine Paraphrase, nur kuerzeste fallbezogene Diagnosephrase. POSITIVBEISPIEL: `Palmarhyperhidrose als eigenständige Diagnose` / `Palmarhyperhidrose als eigenständige Diagnose` ohne passendes kanonisches Feld -> hier eintragen (nicht weglassen). NEGATIVBEISPIELE: bereits kanonisch abgedeckte Diagnosen (NSCLC, Empyem, Destroyed Lung, Metastasen) NICHT zusaetzlich als Freitext hier wiederholen; eine blosse ICD-/Lokalisationstextzeile wie `ICD-/Lokalisationstext ohne eigenständige Freitext-Diagnose` nicht hier ablegen, wenn sie nur die kanonische Tumor-/Metastasenentscheidung paraphrasiert. Hämatothorax als Diagnose gehoert bei Trauma-/Verletzungskontext bevorzugt nach `Thoraxtrauma (Hämatothorax, Rippenfraktur)`, nicht als sonst.-Freitext, sofern das kanonische Feld greift.

---

## Class: `Complication`

**Comment:**

Postoperative Komplikationen und Clavien-Dindo. Massgeblich sind fallbezogene postoperative Komplikationen im Zeitraum OP bis Entlassung. Intraoperative technische Ereignisse, unmittelbare Reventilations-/Umlagerungsbeobachtungen oder rein logistische Intensivverlegung sind ohne explizite Komplikationskennzeichnung nicht automatisch als postoperative Komplikation zu werten.

### Datatype properties

#### `Bronchusstumpf_Anastomoseninsuffizienz`

- Range: `string`; valueKind: `binary_checklist`; csvHeader: `Bronchusstumpf-/Anastomoseninsuffizienz`

Eine Bronchusstumpfinsuffizienz bezeichnet die Undichtigkeit einer Bronchusnaht nach einer Operation und ist eine Komplikation. Bei einer erweiterten Resektion bzw. Manschettenresektion kann auch die Bronchusanastomose undicht werden. Darüber hinaus können Gefäßanastomosen undicht werden. Ersichtlich ist dies in der Regel aus dem QM-Bogen oder dem Arztbrief. Bei Vorliegen von Bronchusstumpf-/Anastomoseninsuffizienz ist eine "1" in die Spalte einzutragen. Andernfalls erfolgt keine Eintragung. Es erfolgt nur eine Eintragung, wenn Komplikation mit "1" beantwortet wurde. Es können mehrere Komplikationen gleichzeitig vorliegen.

#### `Clavien_Dindo`

- Range: `string`; valueKind: `free_text`; csvHeader: `Clavien/Dindo`

Clavien/Dindo: Klassifikation der Komplikationsschwere. BEDINGT: NUR wenn Komplikation = "1". Referenz: https://de.wikipedia.org/wiki/Clavien-Dindo-Klassifikation . Alle Grade erfassen.

#### `Drainage_Punktion`

- Range: `string`; valueKind: `binary_checklist`; csvHeader: `Drainage/Punktion`

Eine Drainage/Punktion bezeichnet das offene oder bildgestützte Anlegen einer (meist) Thoraxdrainage zur Adressierung von bspw. Pleuraerguss, Pneumothorax oder Empyem. Ersichtlich ist dies in der Regel aus dem QM-Bogen oder dem Arztbrief. Bei Vorliegen von Drainage/Punktion ist eine "1" in die Spalte einzutragen. Andernfalls erfolgt keine Eintragung. Wird mehrmals nach einer Primäroperation eine Drainage oder Punktion durchgeführt wird die Zeile dennoch nur mit "1" beantwortet. Es erfolgt nur eine Eintragung, wenn Komplikation mit "1" beantwortet wurde.

#### `Empyem_Komplikation`

- Range: `string`; valueKind: `binary_checklist`; csvHeader: `Empyem (Komplikation)`

Ein Empyem bezeichnet die Ansammlung von Eiter nach einer Operation. Ersichtlich ist dies in der Regel aus dem QM-Bogen oder dem Arztbrief. Bei Vorliegen von Empyem ist eine "1" in die Spalte einzutragen. Andernfalls erfolgt keine Eintragung. Es erfolgt nur eine Eintragung, wenn Komplikation mit "1" beantwortet wurde. Es können mehrere Komplikationen gleichzeitig vorliegen.

#### `Endoskopie`

- Range: `string`; valueKind: `binary_checklist`; csvHeader: `Endoskopie`

Endoskopie (Magen-/Darmspiegelung, ERCP, Bronchoskopie) NACH OP zur Komplikationsbehandlung. BEDINGT: NUR wenn Komplikation = "1". Ersichtlich aus QM-Bogen oder Arztbrief. Liegt vor: "1", sonst keine Eintragung.

#### `Fistel`

- Range: `string`; valueKind: `binary_checklist`; csvHeader: `Fistel`

Eine Fistel bezeichnet die Persistenz einer Luftleckage aus der Lunge über mehr als 7 Tage postoperativ und bezeichnet eine Komplikation. Ersichtlich ist dies in der Regel aus dem QM-Bogen oder dem Arztbrief. Bei Vorliegen von Fistel ist eine "1" in die Spalte einzutragen. Andernfalls erfolgt keine Eintragung. Es erfolgt nur eine Eintragung, wenn Komplikation mit "1" beantwortet wurde. Es können mehrere Komplikationen gleichzeitig vorliegen.

#### `Haematothorax`

- Range: `string`; valueKind: `binary_checklist`; csvHeader: `Hämatothorax`

Ein Hämatothorax bezeichnet die Ansammlung von Blut im Pleuraspalt nach einer Operation. Ersichtlich ist dies in der Regel aus dem QM-Bogen oder dem Arztbrief. Bei Vorliegen von Hämatothorax ist eine "1" in die Spalte einzutragen. Andernfalls erfolgt keine Eintragung. Es erfolgt nur eine Eintragung, wenn Komplikation mit "1" beantwortet wurde. Es koennen mehrere Komplikationen gleichzeitig vorliegen. Nicht ausreichend sind prophylaktische Hinweise wie zur Vermeidung eines postoperativen Haematothorax, Blutungsneigung ohne bestaetigtes postoperatives Ereignis oder die blosse intraoperative Anlage zusaetzlicher Drainagen/Spuelsysteme; es braucht einen tatsaechlich eingetretenen postoperativen Haematothorax als Fallereignis. Eine prophylaktische Spuelung oder Drainageanlage zur Vermeidung dieses Ereignisses zaehlt nicht als eingetretener Haematothorax. Auch Formulierungen wie `Empyem Stadium II bis III`, diffuse Blutungsneigung oder eine zur Vorbeugung angelegte Spueldrainage sind fuer dieses Feld KEINE starke Evidenz, solange die Quelle keinen tatsaechlich eingetretenen postoperativen Haematothorax als Komplikation des Falls festhaelt. POSITIVBEISPIEL: `postoperativer Haematothorax diagnostiziert` -> `1`. NEGATIVBEISPIEL: `Spueldrainage zur Vermeidung eines Haematothorax angelegt` -> KEIN Eintrag.

#### `Kommentar`

- Range: `string`; valueKind: `free_text_fallback`; csvHeader: `Kommentar`

Freitext zur Differenzierung: Bezieht sich die Clavien/Dindo-Komplikation auf den thoraxchirurgischen Eingriff? BEDINGT: NUR ausfuellen, wenn Clavien/Dindo befuellt ist UND der Fall selbst dazu eine konkrete Bemerkung enthaelt. Hier darf NUR kurze fallbezogene Freitextinformation stehen, keine Definition, keine Lehrbucherklaerung und keine Wiederholung der Feldbeschreibung. Isolierte unmittelbare Leckage-, Drainagefluss- oder Umintubationssaetze ohne explizite Komplikationsklassifikation sollen hier nicht als Kommentar eingetragen werden. Typisch wenn thoraxchirurgische OP nicht primaer war (z.B. Folge Oesophagusresektion, herzchirurgischer OP).

#### `Komplikation_j_n`

- Range: `string`; valueKind: `binary_checklist`; csvHeader: `Komplikation (j/n)`

Postoperative Komplikationen (Abweichung vom ueblichen Verlauf). DATENQUELLE: QM-Bogen oder Arztbrief – ohne diese ist das Feld oft nicht befuellbar. Zeitraum: OP-Tag bis Entlassung. Alle Komplikations-Untertypen (Fistel, Haematothorax, etc.) sind NUR relevant, wenn hier "1". `Komplikationsfeld` darf NICHT aus allgemeinen Erklaerungen, Definitionen, Clavien/Dindo-Hintergrundwissen oder blossen Feldnamen abgeleitet werden; es braucht eine fallbezogene explizite Komplikationsangabe. Intraoperative technische Beobachtungen, isolierte unmittelbare Reventilations-/Umlagerungslecks oder eine blosse Intensivverlegung/Umintubation ohne explizite Komplikationswertung reichen nicht aus. Nicht ausreichend sind prophylaktische oder vorbeugende Formulierungen wie zur Vermeidung eines moeglichen Ereignisses, Blutungsprophylaxe, vorsorgliche Drainage-/Spuelsystemanlage oder die blosse Beschreibung intraoperativer Blutstillung, solange kein tatsaechlich eingetretenes postoperatives Ereignis benannt wird. Auch Formulierungen wie zur Vermeidung eines postoperativen Haematothorax oder blosse diffuse Sickerblutungen mit intraoperativer Blutstillung reichen dafuer NICHT aus. Eine blosse Problem- oder Indikationsbeschreibung fuer die aktuelle OP (z.B. unvollstaendige Drainagetherapie, Fibrothorax/Empyem-Stadium im Praeparationsbefund, Reoperationsindikation wegen Grunderkrankung) ist fuer dieses Feld ebenfalls NICHT ausreichend, solange kein zusaetzliches tatsaechlich eingetretenes postoperatives Komplikationsereignis des Falls explizit benannt wird. POSITIVBEISPIEL: `postoperativ trat eine behandlungsbeduerftige Komplikation auf` -> `1`. NEGATIVBEISPIEL: `zur Vermeidung eines Ereignisses wurde prophylaktisch drainiert` oder `Indikation zur Reoperation wegen Grunderkrankung` -> KEIN Eintrag. Liegt vor: "1", sonst keine Eintragung.

#### `Niereninsuffizienz`

- Range: `string`; valueKind: `binary_checklist`; csvHeader: `Niereninsuffizienz`

Bei der Niereninsuffizienz (akuten Nierenschädigung) verschlechtert sich die Fähigkeit der Nieren, das Blut von Stoffwechselabbauprodukten zu reinigen, zunehmend schneller (im Verlauf von Tagen bis Wochen). Die Niereninsuffizienz ist eine Komplikation. Ersichtlich ist dies in der Regel aus dem QM-Bogen oder dem Arztbrief. Bei Vorliegen von Niereninsuffizienz ist eine "1" in die Spalte einzutragen. Andernfalls erfolgt keine Eintragung. Es erfolgt nur eine Eintragung, wenn Komplikation mit "1" beantwortet wurde. Es können mehrere Komplikationen gleichzeitig vorliegen.

#### `Pneumonie`

- Range: `string`; valueKind: `binary_checklist`; csvHeader: `Pneumonie`

Eine Pneumonie bezeichnet eine Infektion des Lungenparenchyms und ist eine Komplikation. Ersichtlich ist dies in der Regel aus dem QM-Bogen oder dem Arztbrief. Bei Vorliegen von Pneumonie ist eine "1" in die Spalte einzutragen. Es erfolgt nur eine Eintragung, wenn Komplikation mit "1" beantwortet wurde. Andernfalls erfolgt keine Eintragung. Es können mehrere Komplikationen gleichzeitig vorliegen.

#### `Pneumothorax_Komplikation`

- Range: `string`; valueKind: `binary_checklist`; csvHeader: `Pneumothorax (Komplikation)`

Pneumothorax als Komplikation: Luft im Pleuraspalt nach OP. NUR eintragen, wenn Drainage/Punktion oder Reoperation erforderlich war – nicht bloße Restschrägstellung. BEDINGT: NUR wenn Komplikation = "1". Ersichtlich aus QM-Bogen oder Arztbrief.

#### `Reintubation`

- Range: `string`; valueKind: `binary_checklist`; csvHeader: `Reintubation`

Eine Reintubation bezeichnet die Notwendigkeit einer erneuten Intubation nach erfolgter Extubation nach der Primäroperation. Oft geschieht dies bei resp. Insuffizienz. Ersichtlich ist dies in der Regel aus dem QM-Bogen oder dem Arztbrief. Bei Vorliegen von Reintubation ist eine "1" in die Spalte einzutragen. Andernfalls erfolgt keine Eintragung. Wird mehrmals nach einer Primäroperation reintubiert wird die Zeile dennoch nur mit "1" beantwortet. Es erfolgt nur eine Eintragung, wenn Komplikation mit "1" beantwortet wurde.

#### `Reoperation`

- Range: `string`; valueKind: `binary_checklist`; csvHeader: `Reoperation`

Die Reoperation bezeichnet die Notwendigkeit nach erfolgter Primäroperation erneut unter Allgemeinnarkose zu operieren, um eine Komplikation zu beheben. Ersichtlich ist dies in der Regel aus dem QM-Bogen oder dem Arztbrief. Bei Vorliegen von Reoperation ist eine "1" in die Spalte einzutragen. Andernfalls erfolgt keine Eintragung. Wird mehrmals nach einer Primäroperation operiert, wird die Zeile dennoch nur mit "1" beantwortet. Es erfolgt nur eine Eintragung, wenn Komplikation mit "1" beantwortet wurde.

#### `Tod`

- Range: `string`; valueKind: `binary_checklist`; csvHeader: `Tod`

Der Tod eines Patienten ist eine Komplikation. Ersichtlich ist dies in der Regel aus dem QM-Bogen oder dem Arztbrief. Bei Vorliegen von Tod ist eine "1" in die Spalte einzutragen. Andernfalls erfolgt keine Eintragung. Es erfolgt nur eine Eintragung, wenn Komplikation mit "1" beantwortet wurde.

#### `Transfusion`

- Range: `string`; valueKind: `binary_checklist`; csvHeader: `Transfusion`

Eine Transfusion bezieht sich auf die Verabreichung von Erythrozyten-, Thrombozytenkonzentraten oder Fresh Frozen Plasma im Verlauf nach einer Operation. Ersichtlich ist dies in der Regel aus dem QM-Bogen oder dem Arztbrief. Bei Vorliegen von Transfusion ist eine "1" in die Spalte einzutragen. Andernfalls erfolgt keine Eintragung. Wird mehrmals nach einer Primäroperation transfundiert, wird die Zeile dennoch nur mit "1" beantwortet. Es erfolgt nur eine Eintragung, wenn Komplikation mit "1" beantwortet wurde.

#### `Wundheilungsstoerung`

- Range: `string`; valueKind: `binary_checklist`; csvHeader: `Wundheilungsstörung`

Die Wundheilungsstörung ist eine Komplikation, die oftmals bei Wundinfektion auftritt und diese mit einschließt. Ersichtlich ist dies in der Regel aus dem QM-Bogen oder dem Arztbrief. Bei Vorliegen von Wundheilungsstörung ist eine "1" in die Spalte einzutragen. Andernfalls erfolgt keine Eintragung. Es erfolgt nur eine Eintragung, wenn Komplikation mit "1" beantwortet wurde. Es können mehrere Komplikationen gleichzeitig vorliegen.

#### `andere_nosokomiale_Infektionen`

- Range: `string`; valueKind: `binary_checklist`; csvHeader: `andere nosokomiale Infektionen`

Nosokomiale Infektionen AUSSER Pneumonie, Wundheilungsstörung und Empyem – z.B. Harnwegsinfekte. BEDINGT: NUR wenn Komplikation = "1". Ersichtlich aus QM-Bogen oder Arztbrief. Liegt vor: "1", sonst keine Eintragung.

#### `kardiovaskulaer`

- Range: `string`; valueKind: `binary_checklist`; csvHeader: `kardiovaskulär`

Kardiovaskuläre Komplikationen nach einer Operation beinhalten das Auftreten von bspw. neuen Thrombosen, Embolien, Rhythmusstörungen, Ischämien, o.Ä. Ersichtlich ist dies in der Regel aus dem QM-Bogen oder dem Arztbrief. Bei Vorliegen von kardiovaskulären Komplikationen ist eine "1" in die Spalte einzutragen. Andernfalls erfolgt keine Eintragung. Es erfolgt nur eine Eintragung, wenn Komplikation mit "1" beantwortet wurde. Es können mehrere Komplikationen gleichzeitig vorliegen.

#### `medikamentoese_Therapie`

- Range: `string`; valueKind: `binary_checklist`; csvHeader: `medikamentöse Therapie`

Medikamentöse Therapie = Clavien-Dindo Stufe II (Medikamentengabe zur Komplikationsbehandlung). BEDINGT: NUR wenn Komplikation = "1". Referenz: https://de.wikipedia.org/wiki/Clavien-Dindo-Klassifikation . Ersichtlich aus QM-Bogen oder Arztbrief.

#### `resp_Insuffizienz`

- Range: `string`; valueKind: `binary_checklist`; csvHeader: `resp. Insuffizienz`

Die resp. (respiratorische) Insuffizienz bezeichnet ein Versagen des pulmonalen Gasaustausches unterschiedlicher Genese und ist eine Komplikation. Definiert wird das Vorhandensein der Komplikation in der Regel durch eine Reintubation. Andernfalls wird die Komplikation aus dem QM-Bogen oder dem Arztbrief ersichtlich. Bei Vorliegen von resp. Insuffizienz ist eine "1" in die Spalte einzutragen. Andernfalls erfolgt keine Eintragung. Es erfolgt nur eine Eintragung, wenn Komplikation mit "1" beantwortet wurde. Es können mehrere Komplikationen gleichzeitig vorliegen.

---

## Class: `PathologyOutcome`

**Comment:**

Stadium und Resektionsstatus (R0/R1/R2). Massgeblich ist der endgueltige pathologische bzw. histologische Befund. Provisorische intraoperative Aussagen (z.B. Schnellschnitt/Frozen Section) sind nachrangig und duerfen nicht als endgueltiges Pathologieergebnis uebernommen werden, sofern kein endgueltiger Befund daraus gemacht wird.

### Datatype properties

#### `R0`

- Range: `string`; valueKind: `binary_checklist`; csvHeader: `R0`

R0: Resektion im Gesunden (in sano). DATENQUELLE: endgültiger histologischer Befund. Intraoperative Schnellschnitt-/Frozen-Section-Aussagen oder andere provisorische Rueckmeldungen reichen allein nicht aus, solange sie nicht als endgueltiger histologischer Befund des Falls dokumentiert werden. Eine explizite Pathologie-Rueckmeldung wie `Pathologische Bestätigung einer R0-Resektion` reicht als R0-Evidenz, sofern keine spaetere endgueltige Pathologie-/Histologieaussage widerspricht. NEGATIVBEISPIEL: blosse intraoperative/makroskopische Formulierung wie `Makroskopisch tumorfreier Resektionsrand ohne formalen Histologie-R0-Befund` ohne endgueltigen Histologie-/R0-Befund -> KEIN Eintrag. Liegt R0 vor: "1", sonst keine Eintragung.

#### `R1`

- Range: `string`; valueKind: `binary_checklist`; csvHeader: `R1`

R1: mikroskopisch Tumorgewebe am Resektionsrand. DATENQUELLE: endgültiger histologischer Befund. Intraoperative Schnellschnitt-/Frozen-Section-Aussagen oder andere provisorische Rueckmeldungen reichen allein nicht aus, solange sie nicht als endgueltiger histologischer Befund des Falls dokumentiert werden. Liegt R1 vor: "1", sonst keine Eintragung.

#### `R2`

- Range: `string`; valueKind: `binary_checklist`; csvHeader: `R2`

R2: makroskopisch Tumorgewebe am Resektionsrand. DATENQUELLE: endgültiger histologischer Befund. Intraoperative Schnellschnitt-/Frozen-Section-Aussagen oder andere provisorische Rueckmeldungen reichen allein nicht aus, solange sie nicht als endgueltiger histologischer Befund des Falls dokumentiert werden. Liegt R2 vor: "1", sonst keine Eintragung.

#### `Stadium`

- Range: `string`; valueKind: `free_text`; csvHeader: `Stadium`

Stadium: UICC-Stadium (z.B. IIIA). BEDINGT: NUR wenn NSCLC, SCLC, NET, Thymom, Thymus-Ca, andere Mediastinaltumoren oder Mesotheliom = "1". DATENQUELLE: endgültiger Pathologie-/Histologiebefund. Referenz: aktuellste UICC zum OP-Zeitpunkt. WICHTIG: Hier ist nur ein oncologisches Tumorstadium gemeint. Andere Verwendungen von `Stadium`, insbesondere Empyem-/Fibrothorax-Stadien oder sonstige nicht-onkologische Stadien der Grunderkrankung bzw. OP-Indikation, duerfen fuer dieses Feld NICHT uebernommen werden. POSITIVBEISPIEL: `Formales onkologisches UICC-Stadium` oder `Formales pathologisches Tumorstadium` im finalen Tumorkontext -> uebernehmen. NEGATIVBEISPIEL: `Nicht-onkologisches Empyem-Stadium` oder `Nicht-onkologisches Fibrothorax-Stadium` -> NICHT uebernehmen.

---
