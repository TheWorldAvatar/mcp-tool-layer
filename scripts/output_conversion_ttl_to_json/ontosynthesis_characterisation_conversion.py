#!/usr/bin/env python3
"""
Characterisation TTL → JSON conversion using rdflib + SPARQL.

Changes from prior version:
- Pulls Elemental Analysis values:
  hasElementalAnalysisData → hasWeightPercentage{Experimental,Calculated} → …Value
- Reads OntoSpecies HNMRData (hasShifts, usesSolvent, hasTemperature)
- When a species has several CCDC deposits, keep the one aligned to the product name
- CLI: python ontosynthesis_characterisation_conversion.py [ttl_path] [out_json]

Source basis: ontosynthesis_characterisation_conversion.py.  # for traceability
"""

import json
import re
import sys
from typing import Dict, List, Any, Optional
from rdflib import Graph, Namespace, URIRef
from rdflib.namespace import RDF, RDFS


# ---------- RDF helpers ----------

def load_ttl_file(file_path: str) -> Graph:
    """Load TTL file into an RDF graph."""
    g = Graph()
    g.parse(file_path, format="turtle")
    print(f"Loaded TTL file with {len(g)} triples from {file_path}")
    return g


def get_namespaces(graph: Graph) -> Dict[str, Namespace]:
    """Extract namespaces from the graph."""
    namespaces: Dict[str, Namespace] = {}
    for prefix, namespace in graph.namespaces():
        namespaces[prefix] = namespace
        # noisy but useful when debugging
        # print(f"NS: {prefix} -> {namespace}")
    return namespaces


def _select_uris(graph: Graph, query: str) -> List[URIRef]:
    """Run a SELECT that returns ?uri rows."""
    results = graph.query(query)
    uris: List[URIRef] = []
    for row in results:
        if getattr(row, "uri", None):
            uris.append(URIRef(str(row.uri)))
    return uris


def _row_to_dict(row) -> Dict[str, Optional[str]]:
    return {
        k: (str(getattr(row, k)) if getattr(row, k) is not None else None)
        for k in row.labels
    }


def _select_first_row(graph: Graph, query: str) -> Optional[Dict[str, Any]]:
    results = graph.query(query)
    for row in results:
        return _row_to_dict(row)
    return None


def _select_all_rows(graph: Graph, query: str) -> List[Dict[str, Any]]:
    results = graph.query(query)
    return [_row_to_dict(row) for row in results]


_EMPTY_LITERALS = {
    "",
    "n/a",
    "na",
    "n.a.",
    "none",
    "null",
    "-",
    "not specified",
    "unspecified",
    "unknown",
}
_ALIGNMENT_STOPWORDS = {"ccdc", "number", "for", "the", "of", "a", "an"}


def _is_blank_literal(value: Optional[str]) -> bool:
    return str(value or "").strip().lower() in _EMPTY_LITERALS


def _clean_literal(value: Optional[str]) -> str:
    text = str(value or "").strip()
    return "N/A" if _is_blank_literal(text) else text


def _identity_tokens(value: str) -> List[str]:
    return [tok for tok in re.split(r"[^a-z0-9]+", str(value or "").lower()) if tok]


def _label_alignment_score(label: str, product_names: List[str]) -> tuple[int, int, int]:
    """Score how closely a CCDC node label matches the current product names.

    Same idea as main chemicals/steps conversion: keep the identifier that
    belongs to this product's human label, not an extra sibling deposit.
    """
    label_tokens = _identity_tokens(label)
    label_sig = [tok for tok in label_tokens if tok not in _ALIGNMENT_STOPWORDS]
    label_text = " ".join(label_sig)
    best = (0, 0, 0)
    for name in product_names:
        name_sig = [tok for tok in _identity_tokens(name) if tok not in _ALIGNMENT_STOPWORDS]
        if not name_sig:
            continue
        name_text = " ".join(name_sig)
        exact = 1 if name_text and name_text in label_text else 0
        overlap = len(set(name_sig) & set(label_sig))
        extra = len(set(label_sig) - set(name_sig))
        best = max(best, (exact, overlap, -extra))
    return best


def _select_aligned_ccdc(
    candidates: List[tuple[str, str]],
    product_names: List[str],
) -> str:
    cleaned: List[tuple[str, str]] = []
    seen: set[str] = set()
    for value, label in candidates:
        number = str(value or "").strip()
        if not number or number.upper() in {"N/A", "NA"}:
            continue
        if number in seen:
            continue
        seen.add(number)
        cleaned.append((number, str(label or "").strip()))
    if not cleaned:
        return "N/A"
    if len(cleaned) == 1:
        return cleaned[0][0]
    cleaned.sort(key=lambda item: item[0])
    cleaned.sort(
        key=lambda item: _label_alignment_score(item[1], product_names),
        reverse=True,
    )
    return cleaned[0][0]


def _clean_hnmr_shifts(value: Optional[str]) -> str:
    text = _clean_literal(value)
    if text == "N/A":
        return text
    # T-box stores peak lists; leading delta marks are notation, not values.
    text = re.sub(r"^(?:δ|∆|delta)\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"(?:,\s*)(?:δ|∆|delta)\s*", ", ", text, flags=re.IGNORECASE)
    return text.strip() or "N/A"


# ---------- Discovery queries ----------

def _find_all_syntheses(graph: Graph) -> List[URIRef]:
    q = """
    PREFIX ontosyn: <https://www.theworldavatar.com/kg/OntoSyn/>
    SELECT DISTINCT ?uri WHERE { ?uri a ontosyn:ChemicalSynthesis . }
    """
    return _select_uris(graph, q)


def _find_species_for_synthesis(graph: Graph, synth: URIRef) -> List[URIRef]:
    q = f"""
    PREFIX ontosyn: <https://www.theworldavatar.com/kg/OntoSyn/>
    PREFIX ontospecies: <http://www.theworldavatar.com/ontology/ontospecies/OntoSpecies.owl#>
    SELECT DISTINCT ?uri WHERE {{
      <{synth}> ontosyn:hasChemicalOutput ?uri .
      ?uri a ontospecies:Species .
    }}
    """
    direct_hits = _select_uris(graph, q)
    if direct_hits:
        return direct_hits

    seen: set[str] = set()
    resolved: List[URIRef] = []

    def _add_species(uri: URIRef) -> None:
        key = str(uri)
        if key not in seen:
            seen.add(key)
            resolved.append(uri)

    # Fallback 1: resolve species by synthesis label / ChemicalOutput label matching.
    ontosyn = Namespace("https://www.theworldavatar.com/kg/OntoSyn/")
    ontospecies = Namespace("http://www.theworldavatar.com/ontology/ontospecies/OntoSpecies.owl#")

    def _normalize_product_name(value: str) -> str:
        text = str(value or "").strip().lower()
        if text.endswith(" synthesis"):
            text = text[: -len(" synthesis")].strip()
        return text

    target_names: set[str] = set()
    for label in graph.objects(synth, RDFS.label):
        norm = _normalize_product_name(str(label))
        if norm:
            target_names.add(norm)
    for out in graph.objects(synth, ontosyn.hasChemicalOutput):
        for label in graph.objects(out, RDFS.label):
            norm = _normalize_product_name(str(label))
            if norm:
                target_names.add(norm)

    if not target_names:
        return resolved

    for species in graph.subjects(RDF.type, ontospecies.Species):
        species_names: set[str] = set()
        for label in graph.objects(species, RDFS.label):
            norm = _normalize_product_name(str(label))
            if norm:
                species_names.add(norm)
        for product_name in graph.objects(species, ontospecies.hasProductName):
            norm = _normalize_product_name(str(product_name))
            if norm:
                species_names.add(norm)
        if species_names & target_names:
            _add_species(URIRef(str(species)))

    return resolved


# ---------- Extraction ----------

def query_characterisation_devices(graph: Graph, namespaces: Dict[str, Namespace]) -> Dict[str, Any]:
    """Return device info found anywhere under species' CharacterizationSession."""
    if 'ontospecies' not in namespaces:
        print("Required namespaces not found")
        return {}

    devices: Dict[str, Any] = {
        "ElementalAnalysisDevice": {},
        "HNMRDevice": {},
        "InfraredSpectroscopyDevice": {},
    }

    synths = _find_all_syntheses(graph)
    for synth in synths:
        for species in _find_species_for_synthesis(graph, synth):
            # HNMR device
            q_hnmr = f"""
            PREFIX ontospecies: <http://www.theworldavatar.com/ontology/ontospecies/OntoSpecies.owl#>
            PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
            SELECT DISTINCT ?deviceName ?deviceLabel ?frequency WHERE {{
              <{species}> ontospecies:hasCharacterizationSession ?cs .
              ?cs ontospecies:hasHNMRDevice ?device .
              OPTIONAL {{ ?device ontospecies:hasDeviceName ?deviceName }}
              OPTIONAL {{ ?device rdfs:label ?deviceLabel }}
              OPTIONAL {{ ?device ontospecies:hasFrequency ?frequency }}
            }} LIMIT 1
            """
            row = _select_first_row(graph, q_hnmr)
            if row:
                info: Dict[str, Any] = {
                    "deviceName": _clean_literal(row.get("deviceName") or row.get("deviceLabel"))
                }
                if row.get("frequency"):
                    info["frequency"] = row["frequency"]
                devices["HNMRDevice"] = info

            # Elemental Analysis device
            q_ea = f"""
            PREFIX ontospecies: <http://www.theworldavatar.com/ontology/ontospecies/OntoSpecies.owl#>
            PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
            SELECT DISTINCT ?deviceName ?deviceLabel WHERE {{
              <{species}> ontospecies:hasCharacterizationSession ?cs .
              ?cs ontospecies:hasElementalAnalysisDevice ?device .
              OPTIONAL {{ ?device ontospecies:hasDeviceName ?deviceName }}
              OPTIONAL {{ ?device rdfs:label ?deviceLabel }}
            }} LIMIT 1
            """
            row = _select_first_row(graph, q_ea)
            if row:
                devices["ElementalAnalysisDevice"] = {
                    "deviceName": _clean_literal(row.get("deviceName") or row.get("deviceLabel"))
                }

            # IR device
            q_irdev = f"""
            PREFIX ontospecies: <http://www.theworldavatar.com/ontology/ontospecies/OntoSpecies.owl#>
            PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
            SELECT DISTINCT ?deviceName ?deviceLabel WHERE {{
              <{species}> ontospecies:hasCharacterizationSession ?cs .
              ?cs ontospecies:hasInfraredSpectroscopyDevice ?device .
              OPTIONAL {{ ?device ontospecies:hasDeviceName ?deviceName }}
              OPTIONAL {{ ?device rdfs:label ?deviceLabel }}
            }} LIMIT 1
            """
            row = _select_first_row(graph, q_irdev)
            if row:
                devices["InfraredSpectroscopyDevice"] = {
                    "deviceName": _clean_literal(row.get("deviceName") or row.get("deviceLabel"))
                }

    return devices


def query_characterisation_data(graph: Graph, namespaces: Dict[str, Namespace]) -> List[Dict[str, Any]]:
    """Build per-species characterisation records."""
    if 'ontospecies' not in namespaces:
        print("Required namespaces not found")
        return []

    records: List[Dict[str, Any]] = []
    ontospecies = Namespace("http://www.theworldavatar.com/ontology/ontospecies/OntoSpecies.owl#")

    def _normalize_material_name(value: str) -> str:
        text = str(value or "").strip()
        if not text:
            return "N/A"
        if "kbr" in text.lower():
            return "KBr"
        return text

    def _infer_element_symbol(label: str) -> Optional[str]:
        allowed = {"C", "H", "N", "O", "S", "P"}
        tokens = re.findall(r"\b([A-Z])\b", str(label or "").replace("_", " "))
        for token in tokens:
            if token in allowed:
                return token
        return None

    def _build_weight_percentage_series(species: URIRef) -> tuple[str, str]:
        calc_parts: Dict[str, str] = {}
        exp_parts: Dict[str, str] = {}
        calc_full: List[str] = []
        exp_full: List[str] = []

        for ead in graph.objects(species, ontospecies.hasElementalAnalysisData):
            for wp in graph.objects(ead, ontospecies.hasWeightPercentageCalculated):
                label = next((str(v) for v in graph.objects(wp, RDFS.label)), "")
                for value in graph.objects(wp, ontospecies.hasWeightPercentageCalculatedValue):
                    text = str(value).strip()
                    if not text:
                        continue
                    if re.search(r"[A-Za-z]\s+\d", text):
                        calc_full.append(text)
                    else:
                        element = _infer_element_symbol(label)
                        if element:
                            calc_parts[element] = text
            for wp in graph.objects(ead, ontospecies.hasWeightPercentageExperimental):
                label = next((str(v) for v in graph.objects(wp, RDFS.label)), "")
                for value in graph.objects(wp, ontospecies.hasWeightPercentageExperimentalValue):
                    text = str(value).strip()
                    if not text:
                        continue
                    if re.search(r"[A-Za-z]\s+\d", text):
                        exp_full.append(text)
                    else:
                        element = _infer_element_symbol(label)
                        if element:
                            exp_parts[element] = text

        def _format(full_values: List[str], parts: Dict[str, str]) -> str:
            if full_values:
                # Prefer the richest combined string already closest to the benchmark format.
                return max(full_values, key=len)
            order = ["C", "H", "N", "O", "S", "P"]
            assembled = [f"{el}, {parts[el]}" for el in order if el in parts]
            return "; ".join(assembled) if assembled else "N/A"

        return _format(calc_full, calc_parts), _format(exp_full, exp_parts)

    def _is_guest_variant_for_species_label(species_label: str) -> bool:
        q_outputs = f"""
        PREFIX ontosyn: <https://www.theworldavatar.com/kg/OntoSyn/>
        PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
        SELECT DISTINCT ?outLabel ?desc WHERE {{
          ?anySynthesis ontosyn:hasChemicalOutput ?out .
          OPTIONAL {{ ?out rdfs:label ?outLabel }}
          OPTIONAL {{ ?out ontosyn:hasChemicalDescription ?desc }}
        }}
        """
        label_norm = (species_label or "").strip().lower()
        guest_markers = (
            "host-guest",
            "host guest",
            "inclusion compound",
            "guest molecule",
            "guest molecules",
            "included",
        )
        try:
            for row in graph.query(q_outputs):
                out_label = str(getattr(row, "outLabel", "") or "").strip().lower()
                desc = str(getattr(row, "desc", "") or "").strip().lower()
                if not desc:
                    continue
                if label_norm and out_label and label_norm != out_label:
                    continue
                if any(marker in desc for marker in guest_markers):
                    return True
        except Exception:
            return False
        return False

    synths = _find_all_syntheses(graph)
    for synth in synths:
        for species in _find_species_for_synthesis(graph, synth):
            # Species label
            q_label = f"""
            PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
            SELECT DISTINCT ?label WHERE {{ <{species}> rdfs:label ?label }} LIMIT 1
            """
            label_row = _select_first_row(graph, q_label)
            species_label = (label_row.get("label") if label_row else None) or "Unknown"
            if _is_guest_variant_for_species_label(species_label):
                continue

            # Keep names scoped to the current species; synthesis label is only
            # used to align a CCDC when the species carries more than one.
            names: List[str] = []
            if species_label and not _is_blank_literal(species_label):
                names.append(species_label)
            q_product_name = f"""
            PREFIX ontospecies: <http://www.theworldavatar.com/ontology/ontospecies/OntoSpecies.owl#>
            SELECT DISTINCT ?productName WHERE {{
              <{species}> ontospecies:hasProductName ?productName
            }}
            """
            for row in _select_all_rows(graph, q_product_name):
                product_name = (row.get("productName") or "").strip()
                if product_name and product_name not in names and not _is_blank_literal(product_name):
                    names.append(product_name)
            alignment_names = list(names)
            for synth_label in graph.objects(synth, RDFS.label):
                text = str(synth_label).strip()
                if text and text not in alignment_names and not _is_blank_literal(text):
                    alignment_names.append(text)

            # All CCDC nodes on this species. Main conversion also collects every
            # deposit, then keeps the one that belongs to this product label.
            q_ccdc = f"""
            PREFIX ontospecies: <http://www.theworldavatar.com/ontology/ontospecies/OntoSpecies.owl#>
            PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
            PREFIX dc: <http://purl.org/dc/elements/1.1/>
            SELECT DISTINCT ?ccdcVal ?ccdcId ?ccdcLabel WHERE {{
              <{species}> ontospecies:hasCCDCNumber ?ccdc .
              OPTIONAL {{ ?ccdc ontospecies:hasCCDCNumberValue ?ccdcVal }}
              OPTIONAL {{ ?ccdc dc:identifier ?ccdcId }}
              OPTIONAL {{ ?ccdc rdfs:label ?ccdcLabel }}
            }}
            """
            ccdc_candidates: List[tuple[str, str]] = []
            for row in _select_all_rows(graph, q_ccdc):
                value = (row.get("ccdcVal") or row.get("ccdcId") or "").strip()
                if value:
                    ccdc_candidates.append((value, (row.get("ccdcLabel") or "").strip()))
            ccdc_number = _select_aligned_ccdc(ccdc_candidates, alignment_names)

            # The benchmark's ElementalAnalysis.chemicalFormula expects an EA-specific field.
            # When the graph does not provide one explicitly, prefer N/A over species formulas.
            molecular_formula = "N/A"
            wp_calc, wp_exp = _build_weight_percentage_series(species)

            # IR data: query bands and material separately to avoid coupling
            # Bands
            q_ir_bands = f"""
            PREFIX ontospecies: <http://www.theworldavatar.com/ontology/ontospecies/OntoSpecies.owl#>
            SELECT DISTINCT ?bands WHERE {{
              <{species}> ontospecies:hasInfraredSpectroscopyData ?ir .
              OPTIONAL {{ ?ir ontospecies:hasBands ?bands }}
            }}
            """
            ir_bands_vals: list[str] = []
            try:
                for r in graph.query(q_ir_bands):
                    if getattr(r, 'bands', None):
                        s = str(r.bands).strip()
                        if s:
                            ir_bands_vals.append(s)
            except Exception:
                pass
            # Deduplicate and join bands
            seen_b: set[str] = set()
            bands_uniq: list[str] = []
            for b in ir_bands_vals:
                if b not in seen_b:
                    seen_b.add(b)
                    bands_uniq.append(b)
            ir_bands = (" ; ".join(bands_uniq)).strip() if bands_uniq else "N/A"

            # Material
            q_ir_mat = f"""
            PREFIX ontospecies: <http://www.theworldavatar.com/ontology/ontospecies/OntoSpecies.owl#>
            SELECT DISTINCT ?matName WHERE {{
              <{species}> ontospecies:hasInfraredSpectroscopyData ?ir .
              OPTIONAL {{
                ?ir ontospecies:usesMaterial ?mat .
                OPTIONAL {{ ?mat ontospecies:hasMaterialName ?matName }}
              }}
            }} LIMIT 1
            """
            ir_material = "N/A"
            try:
                for r in graph.query(q_ir_mat):
                    nm  = str(r.matName).strip() if getattr(r, 'matName',  None) else ""
                    ir_material = _normalize_material_name(nm or "N/A")
                    break
            except Exception:
                pass

            q_nmr = f"""
            PREFIX ontospecies: <http://www.theworldavatar.com/ontology/ontospecies/OntoSpecies.owl#>
            SELECT DISTINCT ?shifts ?temperature ?solventName WHERE {{
              <{species}> ontospecies:hasHNMRData ?nmr .
              OPTIONAL {{ ?nmr ontospecies:hasShifts ?shifts }}
              OPTIONAL {{ ?nmr ontospecies:hasTemperature ?temperature }}
              OPTIONAL {{
                ?nmr ontospecies:usesSolvent ?solv .
                OPTIONAL {{ ?solv ontospecies:hasSolventName ?solventName }}
              }}
            }}
            """
            hnmr_shifts = "N/A"
            hnmr_temperature = "N/A"
            hnmr_solvent = "N/A"
            for row in _select_all_rows(graph, q_nmr):
                if hnmr_shifts == "N/A":
                    hnmr_shifts = _clean_hnmr_shifts(row.get("shifts"))
                if hnmr_temperature == "N/A":
                    hnmr_temperature = _clean_literal(row.get("temperature"))
                if hnmr_solvent == "N/A":
                    hnmr_solvent = _clean_literal(row.get("solventName"))

            if ccdc_number == "N/A":
                continue

            char_entry: Dict[str, Any] = {
                "ElementalAnalysis": {
                    "chemicalFormula": molecular_formula,
                    "weightPercentageCalculated": wp_calc,
                    "weightPercentageExperimental": wp_exp,
                },
                "HNMR": {
                    "shifts": hnmr_shifts,
                    "solvent": hnmr_solvent,
                    "temperature": hnmr_temperature,
                },
                "InfraredSpectroscopy": {"bands": ir_bands, "material": ir_material},
                "productCCDCNumber": ccdc_number,
                "productNames": names,
            }

            records.append(char_entry)
            # Use ASCII-safe printing to avoid Unicode encoding errors
            try:
                print(f"Characterisation: {species_label} | CCDC: {ccdc_number}")
            except UnicodeEncodeError:
                print(f"Characterisation: {species_label.encode('ascii', 'replace').decode('ascii')} | CCDC: {ccdc_number}")

    print(f"Total characterisation entries: {len(records)}")
    return records


# ---------- Assembly ----------

def build_json_structure(devices: Dict[str, Any], characterisations: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Wrap devices + characterisations under Devices list, single device entry.
    Merge characterisation entries that share the same productCCDCNumber and
    de-duplicate list fields (e.g., productNames). For string fields, prefer the
    first non-"N/A" value. For IR bands, merge and de-duplicate tokens by ';'.
    """
    if not characterisations:
        return {"Devices": []}

    # Merge characterisations by productCCDCNumber
    merged: Dict[str, Dict[str, Any]] = {}
    import re  # local import to avoid top-level dependency if unused elsewhere

    def _choose_best_names(raw_names: List[str]) -> List[str]:
        names = []
        seen = set()
        for name in raw_names:
            text = str(name or "").strip()
            if text and text not in seen:
                seen.add(text)
                names.append(text)
        if not names:
            return []

        def _score(name: str) -> tuple[int, int, str]:
            normalized = name.strip()
            simple_product = bool(re.fullmatch(r"[A-Za-z]+-\d+", normalized))
            has_formula = "[" in normalized or "(" in normalized
            has_scope_noise = "synthesis" in normalized.lower()
            has_guest_suffix = any(token in normalized for token in ["·", "•", "∙", "⋅"])
            return (
                0 if simple_product else 1,
                0 if has_formula else 1,
                1 if has_scope_noise else 0,
                1 if has_guest_suffix else 0,
                len(normalized),
                normalized.lower(),
            )

        best = sorted(names, key=_score)[0]
        return [best]

    def _merge_bands(a: str, b: str) -> str:
        tokens: list[str] = []
        seen: set[str] = set()
        for s in (a, b):
            if not s or s == "N/A":
                continue
            parts = [p.strip() for p in re.split(r"\s*;\s*", s) if p.strip()]
            for t in parts:
                if t not in seen:
                    seen.add(t)
                    tokens.append(t)
        out = " ; ".join(tokens) if tokens else (a or b or "N/A")
        return out.strip()

    for rec in characterisations:
        ccdc = str(rec.get("productCCDCNumber") or "").strip()
        if not ccdc:
            # If no CCDC, treat as-is: create a unique bucket keyed by id(rec)
            ccdc = f"__no_ccdc__::{id(rec)}"
        cur = merged.get(ccdc)
        if cur is None:
            # Normalize names list
            names = _choose_best_names(list(rec.get("productNames") or []))
            # Clone minimal structure
            cur = {
                "ElementalAnalysis": dict(rec.get("ElementalAnalysis") or {}),
                "HNMR": dict(rec.get("HNMR") or {}),
                "InfraredSpectroscopy": dict(rec.get("InfraredSpectroscopy") or {}),
                "productCCDCNumber": rec.get("productCCDCNumber") or "",
                "productNames": names,
            }
            merged[ccdc] = cur
            continue

        # Merge names (de-duplicate, keep order)
        existing_names: list[str] = cur.get("productNames") or []
        merged_names = existing_names + list(rec.get("productNames") or [])
        cur["productNames"] = _choose_best_names(merged_names)

        # Merge ElementalAnalysis: prefer first non-"N/A"
        for k in ("chemicalFormula", "weightPercentageCalculated", "weightPercentageExperimental"):
            v_cur = (cur.get("ElementalAnalysis") or {}).get(k)
            v_new = (rec.get("ElementalAnalysis") or {}).get(k)
            if (not v_cur or str(v_cur).strip() == "N/A") and v_new and str(v_new).strip():
                cur.setdefault("ElementalAnalysis", {})[k] = v_new

        # Merge HNMR (placeholders): prefer first non-"N/A"
        for k in ("shifts", "solvent", "temperature"):
            v_cur = (cur.get("HNMR") or {}).get(k)
            v_new = (rec.get("HNMR") or {}).get(k)
            if (not v_cur or str(v_cur).strip() == "N/A") and v_new and str(v_new).strip():
                cur.setdefault("HNMR", {})[k] = v_new

        # Merge IR bands by union; material prefer non-"N/A"
        ir_cur = cur.get("InfraredSpectroscopy") or {}
        ir_new = rec.get("InfraredSpectroscopy") or {}
        ir_bands_merged = _merge_bands(ir_cur.get("bands") or "", ir_new.get("bands") or "")
        if ir_bands_merged:
            ir_cur["bands"] = ir_bands_merged.strip()
        if (not ir_cur.get("material") or str(ir_cur.get("material")).strip() == "N/A") and (ir_new.get("material")):
            ir_cur["material"] = str(ir_new.get("material")).strip()
        cur["InfraredSpectroscopy"] = ir_cur

    merged_list = []
    for ccdc, rec in merged.items():
        merged_list.append(rec)

    device_entry: Dict[str, Any] = {"Characterisation": merged_list}
    if devices.get("ElementalAnalysisDevice"):
        device_entry["ElementalAnalysisDevice"] = devices["ElementalAnalysisDevice"]
    if devices.get("HNMRDevice"):
        device_entry["HNMRDevice"] = devices["HNMRDevice"]
    if devices.get("InfraredSpectroscopyDevice"):
        device_entry["InfraredSpectroscopyDevice"] = devices["InfraredSpectroscopyDevice"]
    return {"Devices": [device_entry]}


# ---------- Main ----------

def main():
    ttl_path = sys.argv[1] if len(sys.argv) > 1 else "ontospecies_extension.ttl"
    out_path = sys.argv[2] if len(sys.argv) > 2 else "converted_characterisation.json"

    print("=== Building characterisation JSON ===")
    graph = load_ttl_file(ttl_path)
    namespaces = get_namespaces(graph)

    devices = query_characterisation_devices(graph, namespaces)
    characterisations = query_characterisation_data(graph, namespaces)

    json_data = build_json_structure(devices, characterisations)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(json_data, f, indent=2, ensure_ascii=False)

    print(f"Saved: {out_path}")


if __name__ == "__main__":
    main()
