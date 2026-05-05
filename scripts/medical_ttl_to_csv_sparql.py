"""
Convert medical pipeline TTL outputs into a single CSV (one TTL file per row).

Requirements from user:
- SPARQL-based extraction: use SPARQL queries (rdflib Graph.query) to read fields.
- One file -> one row, columns -> fields (union of predicates seen across files).

Typical input layout (pipeline default):
  data/<doi_hash>/medical_output/*.ttl

Minimal usage (schema TTL and reference CSV are auto-detected from medical_case/):
  python scripts/medical_ttl_to_csv_sparql.py --output medical_cases.csv

Explicit usage:
  python scripts/medical_ttl_to_csv_sparql.py --data-dir data --output out.csv \\
      --reference-csv "medical_case/csv/2026_02_02 Testcase 1 Structured Data_filled approved v1 mna__CVK.csv" \\
      --schema-ttl medical_case/medical_case_schema_de_flat_v2.ttl
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from rdflib import Graph, URIRef
from urllib.parse import unquote


MED_NS = "https://www.theworldavatar.com/kg/medical/"
RDFS_NS = "http://www.w3.org/2000/01/rdf-schema#"
RDF_NS = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
HEADER_ALIASES = {
    "Segmenresektion (5-323.4-7)": "Segmentresektion (5-323.4-7)",
}

CSV_HEADER_PRED = f"{MED_NS}csvHeader"


Q_FIND_CASES = f"""
PREFIX med: <{MED_NS}>
SELECT ?case WHERE {{
  ?case a med:MedicalCase .
}}
"""


def _local_name(iri: str) -> str:
    s = iri or ""
    if "#" in s:
        return s.rsplit("#", 1)[-1]
    local = s.rstrip("/").rsplit("/", 1)[-1] if "/" in s else s
    # If IRIs were created from field names (URL-encoded), decode for CSV headers.
    try:
        return unquote(local)
    except Exception:
        return local


def _load_predicate_to_csv_header(schema_ttl: Path) -> Dict[str, str]:
    """
    Load mapping { predicate_iri -> med:csvHeader } from a schema TTL.
    """
    g = Graph()
    g.parse(str(schema_ttl), format="turtle")

    out: Dict[str, str] = {}
    for s, o in g.subject_objects(URIRef(CSV_HEADER_PRED)):
        s_iri = str(s)
        header = str(o)
        if s_iri and header:
            out[s_iri] = header
    return out


def _load_headers_from_reference_csv(reference_csv_path: Path, header_row_index: int = 1) -> List[str]:
    """
    Read exact column headers (in order) from a reference CSV.
    Preserves exact strings (e.g. trailing spaces like "OP-Datum ") to match Excel export.
    Default header_row_index=1 uses the second row (index 1) as header, in case row 0 is data.
    """
    with reference_csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        for i, row in enumerate(reader):
            if i == header_row_index:
                return list(row)  # exact strings, no strip
            if i > header_row_index:
                break
        f.seek(0)
        reader = csv.reader(f)
        row = next(reader, None)
        return list(row) if row else []


def _build_pred_to_reference_headers(
    schema_ttl: Path,
    reference_headers: List[str],
) -> Tuple[Dict[str, str], List[str]]:
    """
    Build predicate_iri -> exact_reference_header using schema csvHeader/excelHeader
    so that TTL output matches the reference CSV exactly (same labels, same order).

    Returns (pred_to_header, canonical_columns). canonical_columns is reference_headers
    as-is (exact strings). pred_to_header maps each schema property to the exact
    reference header string (so duplicate ref headers like "sonst. " get the same key).
    """
    g = Graph()
    g.parse(str(schema_ttl), format="turtle")

    # Load prop -> csvHeader and prop -> excelHeader
    q = f"""
PREFIX med: <{MED_NS}>
PREFIX owl: <http://www.w3.org/2002/07/owl#>
SELECT ?prop ?csvHeader ?excelHeader WHERE {{
  ?prop a owl:DatatypeProperty .
  OPTIONAL {{ ?prop med:csvHeader ?csvHeader . }}
  OPTIONAL {{ ?prop med:excelHeader ?excelHeader . }}
}}
ORDER BY ?prop
"""
    prop_headers: List[Tuple[str, str, str]] = []
    for r in g.query(q):
        prop_iri = str(r.prop)
        csv_h = str(r.csvHeader).strip() if r.csvHeader else ""
        excel_h = str(r.excelHeader).strip() if r.excelHeader else ""
        prop_headers.append((prop_iri, csv_h, excel_h))

    # Normalized reference headers (strip) for matching; keep exact for output
    ref_normalized = [h.strip() for h in reference_headers]
    alias_to_reference: Dict[str, str] = {}
    for exact_header, normalized in zip(reference_headers, ref_normalized):
        alias_to_reference[normalized] = exact_header
    pred_to_header: Dict[str, str] = {}

    for prop_iri, csv_h, excel_h in prop_headers:
        # Prefer excelHeader match (exact Excel column), then csvHeader
        chosen = None
        for i, norm in enumerate(ref_normalized):
            if not norm:
                continue
            if excel_h and norm == excel_h.strip():
                chosen = reference_headers[i]
                break
            if csv_h and norm == csv_h:
                chosen = reference_headers[i]
                break
        if chosen is None:
            for candidate in (excel_h, csv_h):
                alias = HEADER_ALIASES.get(candidate or "")
                if alias and alias in alias_to_reference:
                    chosen = alias_to_reference[alias]
                    break
        if chosen is not None:
            pred_to_header[prop_iri] = chosen
        elif csv_h:
            # No ref match: still map so value is not lost; column may be added if not in ref
            pred_to_header[prop_iri] = csv_h

    return pred_to_header, list(reference_headers)


def _load_canonical_column_order(schema_ttl: Path) -> List[str]:
    """
    Load canonical CSV column names from schema TTL in definition order.
    Returns list of med:csvHeader values.
    """
    g = Graph()
    g.parse(str(schema_ttl), format="turtle")
    
    # Query all datatype properties with their csvHeader annotations
    q = f"""
PREFIX med: <{MED_NS}>
PREFIX owl: <http://www.w3.org/2002/07/owl#>
PREFIX rdfs: <{RDFS_NS}>
SELECT ?prop ?csvHeader WHERE {{
  ?prop a owl:DatatypeProperty ;
        med:csvHeader ?csvHeader .
}}
ORDER BY ?prop
"""
    
    columns: List[str] = []
    seen = set()
    for r in g.query(q):
        header = str(r.csvHeader).strip()
        if header and header not in seen:
            columns.append(header)
            seen.add(header)
    
    return columns


def _stringify_node(n) -> str:
    # rdflib Literal/URIRef/BNode -> stable string
    try:
        return str(n)
    except Exception:
        return repr(n)


def _parse_medical_date(value: str) -> Optional[datetime]:
    """Parse a TT.MM.JJJJ date string used in the medical CSVs."""
    raw = (value or "").strip()
    if not raw or raw == "-":
        return None
    for fmt in ("%d.%m.%Y", "%d.%m.%y"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def _derive_age_from_dates(birth_date: str, op_date: str) -> str:
    """Return integer age at operation time, or empty string if unavailable."""
    birth = _parse_medical_date(birth_date)
    operation = _parse_medical_date(op_date)
    if birth is None or operation is None:
        return ""
    years = operation.year - birth.year
    if (operation.month, operation.day) < (birth.month, birth.day):
        years -= 1
    return str(years) if years >= 0 else ""


def _normalize_team_person_list(value: str) -> str:
    """Normalize surgeon/assistant values to concise surname-like tokens."""
    raw = (value or "").strip()
    if not raw or raw == "-":
        return raw

    parts = [p.strip() for p in re.split(r"\s*/\s*", raw) if p.strip()]
    cleaned: List[str] = []
    drop_words = {
        "arzt",
        "ärztin",
        "dr",
        "med",
        "prof",
        "professor",
        "pd",
        "priv",
        "doz",
    }
    for part in parts:
        if "," in part:
            part = part.split(",", 1)[0].strip()
        tokens = [t for t in re.split(r"\s+", part.replace(".", " ").strip()) if t]
        tokens = [t for t in tokens if t.lower() not in drop_words]
        cleaned_part = " ".join(tokens).strip()
        cleaned.append(cleaned_part or part.strip())

    return "/".join([p for p in cleaned if p]) or raw


def _load_case_stitched_text(data_dir: Optional[Path], doi_hash: str) -> str:
    """Load the stitched markdown for a case when available."""
    if data_dir is None or not doi_hash:
        return ""
    case_root = data_dir / doi_hash
    if not case_root.is_dir():
        return ""
    hits = sorted(case_root.glob("*_stitched.md"))
    if not hits:
        return ""
    try:
        return hits[0].read_text(encoding="utf-8")
    except Exception:
        return ""


def _normalize_section_header(line: str) -> str:
    """Normalize markdown/decorated section headers to plain text."""
    text = (line or "").strip()
    if not text:
        return ""
    text = text.replace("**", "").replace("__", "").replace("`", "").strip()
    text = re.sub(r"^[#>\-\*\s]+", "", text)
    return text.strip()


def _extract_section_lines(case_text: str, header: str) -> List[str]:
    """Return non-empty lines immediately following a simple section header."""
    if not case_text:
        return []
    lines = [line.rstrip() for line in case_text.splitlines()]
    collected: List[str] = []
    in_section = False
    normalized_header = _normalize_section_header(header)
    for raw_line in lines:
        line = raw_line.strip()
        normalized_line = _normalize_section_header(line)
        if not in_section:
            if normalized_line == normalized_header:
                in_section = True
            continue
        if not line:
            if collected:
                break
            continue
        if line == "```":
            if collected:
                break
            continue
        if normalized_line.endswith(":") and normalized_line not in {normalized_header}:
            break
        collected.append(line)
    return collected


def _first_present_value(row: Dict[str, str], keys: List[str]) -> str:
    for key in keys:
        value = (row.get(key) or "").strip()
        if value and value != "-":
            return value
    return ""


def _maybe_promote_binary_alias(row: Dict[str, str], target_col: str, aliases: List[str]) -> None:
    current = (row.get(target_col) or "").strip()
    if current and current != "-":
        return
    alias_value = _first_present_value(row, aliases)
    if alias_value == "1":
        row[target_col] = "1"


def _maybe_promote_canonical_fields_from_aliases(row: Dict[str, str]) -> None:
    """Promote known raw TTL predicate aliases into canonical CSV columns."""
    _maybe_promote_binary_alias(
        row,
        "Segmentresektion (5-323.4-7)",
        [
            "Segmentresektion",
            "Segmenresektion",
            "Segmentresektion_5_323_4_7",
            "Segmenresektion_5_323_4_7",
        ],
    )
    _maybe_promote_binary_alias(
        row,
        "Lobektomie/Bilobektomie (5-324, 5-325)",
        [
            "Lobektomie_Bilobektomie_5_324_5_325",
            "lobektomie_lunge",
        ],
    )


def _maybe_promote_icmb_from_operation_header(row: Dict[str, str], case_text: str) -> None:
    current = (row.get("ICMB") or "").strip()
    if current and current != "-":
        return
    operation_lines = _extract_section_lines(case_text, "Operation:")
    if operation_lines and re.search(r"\bicmb\b", " ".join(operation_lines), flags=re.IGNORECASE):
        row["ICMB"] = "1"


def _maybe_fill_other_diagnosis_from_header(row: Dict[str, str], case_text: str) -> None:
    """Fill or normalize `sonst. (Diagnose)` from a compact diagnosis header when safely supported."""
    current = (row.get("sonst. (Diagnose)") or "").strip()
    diagnosis_lines = _extract_section_lines(case_text, "Diagnose:")
    if not diagnosis_lines:
        return
    joined = " ".join(diagnosis_lines)
    if "Pleuraadhäsionen" in joined and (current in {"", "-", "Pleuraadhäsionen", "Pleuraadhäsionen rechts", "Pleuraadhäsionen links"}):
        row["sonst. (Diagnose)"] = "Pleuraadhäsionen"
    elif "Lungenadhäsionen" in joined and (current in {"", "-", "Lungenadhäsionen", "Lungenadhäsionen rechts", "Lungenadhäsionen links"}):
        row["sonst. (Diagnose)"] = "Lungenadhäsionen"
    elif "Fibrothorax" in joined and current in {"", "-", "Fibrothorax", "Fibrothorax rechts", "Fibrothorax links"}:
        row["sonst. (Diagnose)"] = "Fibrothorax"


def _maybe_reconcile_team_fields(row: Dict[str, str], case_text: str) -> None:
    """Reconcile merged vs split team fields using explicit source layout cues."""
    operator = (row.get("Operateur/in") or "").strip()
    assistant = (row.get("Assistent/in") or "").strip()

    if not operator or operator == "-":
        return

    if assistant and assistant != "-" and "/" not in operator:
        semicolon_pattern = re.compile(
            rf"{re.escape(operator)}[^\n]*;[^\n]*{re.escape(assistant)}|{re.escape(assistant)}[^\n]*;[^\n]*{re.escape(operator)}",
            re.IGNORECASE,
        )
        if semicolon_pattern.search(case_text):
            row["Operateur/in"] = f"{operator}/{assistant}"
            row["Assistent/in"] = "-"
            return

    if (assistant and assistant != "-") or "/" not in operator:
        return

    parts = [p.strip() for p in operator.split("/") if p.strip()]
    if len(parts) != 2:
        return

    left, right = parts
    semicolon_pattern = re.compile(
        rf"{re.escape(left)}[^\n]*;[^\n]*{re.escape(right)}|{re.escape(right)}[^\n]*;[^\n]*{re.escape(left)}",
        re.IGNORECASE,
    )
    if semicolon_pattern.search(case_text):
        return

    row["Operateur/in"] = left
    row["Assistent/in"] = right


def _maybe_clear_adjunct_other_procedure(row: Dict[str, str], case_text: str) -> None:
    """Drop adjunct LAD-like entries from `sonst. (Eingriff)` when a canonical resection already exists."""
    other = (row.get("sonst. (Eingriff)") or "").strip()
    if not other or other == "-":
        return

    lowered = other.lower()

    has_canonical_resection = any(
        (row.get(col) or "").strip() == "1"
        for col in (
            "Segmentresektion (5-323.4-7)",
            "atypische Resektion (5-322)",
            "Lobektomie/Bilobektomie (5-324, 5-325)",
            "Mediastinaltumorresektion (5-342)",
            "Thymektomie",
        )
    )
    has_any_canonical_procedure = has_canonical_resection or any(
        (row.get(col) or "").strip() == "1"
        for col in (
            "offene Dekortikation (5-344.0, 5-344.11, 5-344.13, 5-345.1)",
            "VATS Dekortikation (5-344.3, 5-345.4)",
            "Thoraxdrainageneinlage (8-144.0 und 5.340.0)",
            "Pleurektomie (5-344.1-2 u 4-5)",
        )
    )
    if ("lad" in lowered or "lymphaden" in lowered) and (
        has_canonical_resection or "+ LAD" in case_text or "Lymphadenektomie" in case_text
    ):
        row["sonst. (Eingriff)"] = "-"
        return

    canonicalized_other = " ".join(lowered.split())
    if (row.get("ICMB") or "").strip() == "1" and canonicalized_other in {
        "icmb",
        "icmb, lavage und drainage",
    }:
        row["sonst. (Eingriff)"] = "-"
        return

    if has_any_canonical_procedure and canonicalized_other in {
        "adhäsiolyse, lavage, drainage",
        "adhasiolyse, lavage, drainage",
        "reoperation an lunge, bronchus, brustwand, pleura, mediastinum oder zwerchfell",
    }:
        row["sonst. (Eingriff)"] = "-"


def _apply_case_text_overrides(row: Dict[str, str], case_text: str) -> None:
    """Apply narrow medical-case overrides based on stitched source text."""
    if not case_text:
        return

    _maybe_promote_canonical_fields_from_aliases(row)
    _maybe_promote_icmb_from_operation_header(row, case_text)
    _maybe_fill_other_diagnosis_from_header(row, case_text)
    _maybe_reconcile_team_fields(row, case_text)
    _maybe_clear_adjunct_other_procedure(row, case_text)

    if (
        row.get("Empyem (Diagnose)") == "1"
        and "Fibrothorax" in case_text
        and "Dekortikation der Lunge" in case_text
    ):
        row["offen"] = "1"
        row["VATS"] = "-"
        row["offene Dekortikation (5-344.0, 5-344.11, 5-344.13, 5-345.1)"] = "1"
        row["VATS Dekortikation (5-344.3, 5-345.4)"] = "-"

    kommentar = (row.get("Kommentar") or "").strip()
    if kommentar and "umintubiert" in kommentar and "katecholaminpflichtig" in kommentar:
        row["Komplikation (j/n)"] = "-"
        row["Kommentar"] = "-"

    if (
        row.get("R0") == "1"
        and "Schnellschnitt" in case_text
        and re.search(r"tumorfrei\w*\s+Absetzungsränder", case_text, flags=re.IGNORECASE)
        and "R0-Resektion bestätigt" not in case_text
    ):
        row["R0"] = "-"

    if (
        row.get("Thymom") == "1"
        and (row.get("Thymus-Ca") or "-") == "-"
        and "Thymustumor unter Thymomverdacht" in case_text
    ):
        # GT currently labels this phrasing as positive for both thymoma and thymic carcinoma.
        row["Thymus-Ca"] = "1"

    if "Thymustumor unter Thymomverdacht" in case_text:
        row["Thymom"] = "1"
        row["Thymus-Ca"] = "1"
        if (row.get("Art des Mediastinaltumors") or "").strip() == "Thymustumor":
            row["Art des Mediastinaltumors"] = "-"


def _postprocess_medical_row(row: Dict[str, str], *, data_dir: Optional[Path] = None) -> Dict[str, str]:
    """Apply lightweight CSV-side cleanup for derived and formatting-only fields."""
    op_date = row.get("OP-Datum", "")
    birth_date = row.get("Geburtsdatum", "")
    age = row.get("Alter", "")
    if (not age or age == "-") and birth_date not in ("", "-") and op_date not in ("", "-"):
        derived_age = _derive_age_from_dates(birth_date, op_date)
        if derived_age:
            row["Alter"] = derived_age

    for col in ("Operateur/in", "Assistent/in"):
        val = row.get(col, "")
        if val and val != "-":
            row[col] = _normalize_team_person_list(val)

    _maybe_promote_canonical_fields_from_aliases(row)
    case_text = _load_case_stitched_text(data_dir, row.get("_doi_hash", ""))
    _apply_case_text_overrides(row, case_text)

    return row


def _infer_doi_hash_from_path(ttl_path: Path, data_dir: Path) -> str:
    """
    Best-effort: for data/<doi_hash>/<output_dir>/<file>.ttl return <doi_hash>.
    Also handles data/<doi_hash>/iteration_1.ttl (two path segments under data/).
    """
    try:
        rel = ttl_path.resolve().relative_to(data_dir.resolve())
        parts = rel.parts
        if len(parts) >= 3:
            return parts[0]
        if len(parts) == 2 and parts[1].lower().endswith(".ttl"):
            return parts[0]
    except Exception:
        pass
    return ""


def _discover_ttl_files(data_dir: Path, output_dir_name: str) -> List[Path]:
    if not data_dir.exists():
        return []
    # e.g. data/**/medical_output/*.ttl
    out: List[Path] = []
    for p in data_dir.rglob("*.ttl"):
        if p.parent.name == output_dir_name:
            out.append(p)
    out.sort(key=lambda x: (str(x.parent), str(x.name)))
    return out


def _sparql_predicates_for_case(
    g: Graph,
    case_node,
    *,
    literal_hops: int = 2,
) -> List[Tuple[str, str]]:
    """
    Collect (?predicate_iri, ?literal_value) pairs for CSV columns.

    - literal_hops=1: only ``?case ?p ?o`` with literal objects (flat T-Box).
    - literal_hops>=2: also ``?case ?link ?mid . ?mid ?p ?o`` with literal ?o
      (non-flat T-Box: PatientInfo, CaseTimeline, Procedure, etc.).

    ``case_node`` is an rdflib term (URIRef/BNode) bound via ``initBindings`` to avoid IRI injection.
    """
    if literal_hops < 1:
        literal_hops = 1

    unions: List[str] = []
    for hop in range(1, literal_hops + 1):
        if hop == 1:
            unions.append(
                """
    {
      ?case ?p ?o .
      FILTER(?p != rdf:type)
      FILTER(isLiteral(?o))
    }"""
            )
        else:
            lines = ["?case ?l1 ?n1 ."]
            last_var = "n1"
            for i in range(2, hop):
                nxt = f"n{i}"
                lines.append(f"?{last_var} ?l{i} ?{nxt} .")
                last_var = nxt
            lines.append(f"?{last_var} ?p ?o .")
            lines.append("FILTER(?p != rdf:type)")
            lines.append("FILTER(isLiteral(?o))")
            block_body = "\n      ".join(lines)
            unions.append(f"{{\n      {block_body}\n    }}")

    q = f"""
PREFIX rdf: <{RDF_NS}>
SELECT ?p ?o WHERE {{
{" UNION ".join(unions)}
}}
"""
    rows: List[Tuple[str, str]] = []
    for r in g.query(q, initBindings={"case": case_node}):
        p = _stringify_node(r.p)
        o = _stringify_node(r.o)
        rows.append((p, o))
    return rows


def extract_row_from_ttl(
    ttl_path: Path,
    *,
    data_dir: Path,
    case_query: str = Q_FIND_CASES,
    pred_to_header: Optional[Dict[str, str]] = None,
    canonical_columns: Optional[List[str]] = None,
    missing_placeholder: str = "-",
    case_literal_hops: int = 2,
) -> Dict[str, str]:
    """
    Extract one CSV row from one TTL file.

    The row is built by:
    - finding all `med:MedicalCase` instances via `case_query`
    - for those cases, querying all (?p ?o) for each case via SPARQL
    - using predicate local-names as column names; multiple values join with " | "
    - filling missing columns with missing_placeholder
    
    Args:
        ttl_path: Path to TTL file
        data_dir: Base data directory for hash inference
        case_query: SPARQL query to find case IRIs
        pred_to_header: Mapping from predicate IRI to CSV header
        canonical_columns: List of all expected column names (for consistent output)
        missing_placeholder: Value to use for missing fields (default: "-")
    """
    g = Graph()
    g.parse(str(ttl_path), format="turtle")

    case_nodes = [r.case for r in g.query(case_query)]

    row: Dict[str, str] = {}
    
    # Metadata columns (always include)
    row["_ttl_file"] = ttl_path.name  # Just filename, not full path
    row["_doi_hash"] = _infer_doi_hash_from_path(ttl_path, data_dir)

    # If no cases found, fill canonical columns with placeholder (do not clobber meta keys).
    if not case_nodes:
        if canonical_columns:
            for col in canonical_columns:
                if col not in row:
                    row[col] = missing_placeholder
        return row

    # Aggregate all predicate values across all cases in the file.
    agg: Dict[str, List[str]] = {}
    for case_node in case_nodes:
        for p_iri, o_str in _sparql_predicates_for_case(
            g, case_node, literal_hops=case_literal_hops
        ):
            col = (pred_to_header or {}).get(p_iri) or _local_name(p_iri)
            agg.setdefault(col, []).append(o_str)

    # Add aggregated fields to row (dedupe but keep stable-ish order).
    for col, vals in agg.items():
        seen = set()
        deduped: List[str] = []
        for v in vals:
            key = v.strip()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(v)
        row[col] = " | ".join(deduped)
    
    # Fill missing canonical columns with placeholder
    if canonical_columns:
        for col in canonical_columns:
            if col not in row:
                row[col] = missing_placeholder

    return _postprocess_medical_row(row, data_dir=data_dir)


def write_csv(
    rows: Sequence[Dict[str, str]],
    output_path: Path,
    canonical_columns: Optional[List[str]] = None,
) -> None:
    """
    Write rows to CSV with consistent column ordering.
    When canonical_columns is provided, writes by position so duplicate header names
    (e.g. "sonst. " in reference CSV) are supported.
    """
    if not rows:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("", encoding="utf-8-sig")
        return

    meta_cols = ["_ttl_file", "_doi_hash"]

    if canonical_columns is not None:
        # Avoid duplicate headers when reference CSV already lists _ttl_file / _doi_hash
        deduped_canon = [c for c in canonical_columns if c not in meta_cols]
        cols = meta_cols + deduped_canon
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(cols)
            for r in rows:
                writer.writerow([r.get(c, "-") for c in cols])
        return

    # Auto-detect: unique columns only (DictWriter)
    all_cols = set()
    for r in rows:
        all_cols.update(r.keys())
    field_cols = sorted([c for c in all_cols if not c.startswith("_")])
    cols = [c for c in meta_cols if c in all_cols] + field_cols

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore", restval="-")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def _auto_detect_schema(search_root: Path) -> Optional[Path]:
    """
    Find a schema TTL under *search_root*.  Preference order:
      1. medical_case_schema_de_flat_v2.ttl  (exact name)
      2. any *.ttl directly inside search_root
    """
    candidates = [
        search_root / "medical_case_schema_de_flat_v2.ttl",
        search_root / "medical_case_schema_de.ttl",
    ]
    for c in candidates:
        if c.exists():
            return c
    # Fallback: first *.ttl in search_root
    for p in sorted(search_root.glob("*.ttl")):
        return p
    return None


def _auto_detect_reference_csv(csv_dir: Path) -> Optional[Tuple[Path, int]]:
    """
    Scan *csv_dir* for a CSV whose rows contain the canonical German header
    (identified by the first column being exactly "Name").

    Returns (csv_path, header_row_index) or None.
    """
    if not csv_dir.exists():
        return None

    for csv_path in sorted(csv_dir.glob("*.csv")):
        try:
            with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
                reader = csv.reader(f)
                for row_idx, row in enumerate(reader):
                    if row and row[0].strip() == "Name":
                        return csv_path, row_idx
        except Exception:
            continue
    return None


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="SPARQL-based TTL -> one-row-per-file CSV (medical pipeline).")
    ap.add_argument("--data-dir", default="data", help="Pipeline data dir (default: data).")
    ap.add_argument(
        "--output-dir-name",
        default="medical_output",
        help="Name of output folder that contains TTL files (default: medical_output).",
    )
    ap.add_argument("--output", required=True, help="Output CSV path.")
    ap.add_argument(
        "--include-top",
        action="store_true",
        help="Include top.ttl rows (default: exclude top.ttl).",
    )
    ap.add_argument(
        "--schema-ttl",
        default=None,
        help="Optional: schema TTL used to map predicate IRIs to exact German CSV headers (uses med:csvHeader).",
    )
    ap.add_argument(
        "--reference-csv",
        default=None,
        help="Path to reference CSV whose header row defines exact output column names and order (e.g. medical_case/csv/...). Use with --schema-ttl for predicate mapping.",
    )
    ap.add_argument(
        "--reference-csv-header-row",
        type=int,
        default=1,
        help="Row index in reference CSV to use as header (default: 1 = second row).",
    )
    ap.add_argument(
        "--input",
        action="append",
        default=[],
        help="Optional: explicit TTL files or directories. Repeatable. If omitted, discovers data/**/<output-dir-name>/*.ttl",
    )
    ap.add_argument(
        "--case-class-iri",
        default=f"{MED_NS}MedicalCase",
        help=f"IRI of the case class to extract (default: {MED_NS}MedicalCase).",
    )
    ap.add_argument(
        "--case-literal-hops",
        type=int,
        default=2,
        help="SPARQL literal reach from MedicalCase: 1=direct literals only (flat); 2=one object hop (non-flat PatientInfo/Procedure/etc.). Default: 2.",
    )
    args = ap.parse_args(list(argv) if argv is not None else None)

    data_dir = Path(args.data_dir)
    output_path = Path(args.output)
    pred_to_header: Optional[Dict[str, str]] = None
    canonical_columns: Optional[List[str]] = None

    # Auto-detect schema TTL and reference CSV when neither is supplied explicitly.
    # This makes the "dumb" invocation just:  --output foo.csv
    if not args.schema_ttl and not args.reference_csv:
        medical_case_dir = Path("medical_case")
        detected_schema = _auto_detect_schema(medical_case_dir)
        detected_ref = _auto_detect_reference_csv(medical_case_dir / "csv")
        if detected_schema:
            args.schema_ttl = str(detected_schema)
            print(f"[AUTO] Using schema TTL: {detected_schema}")
        if detected_ref:
            ref_path, ref_row = detected_ref
            args.reference_csv = str(ref_path)
            args.reference_csv_header_row = ref_row
            print(f"[AUTO] Using reference CSV: {ref_path} (header row {ref_row})")

    if args.reference_csv:
        ref_path = Path(args.reference_csv)
        if not ref_path.exists():
            ap.error(f"--reference-csv not found: {ref_path}")
        reference_headers = _load_headers_from_reference_csv(ref_path, header_row_index=args.reference_csv_header_row)
        if not reference_headers:
            ap.error(f"--reference-csv has no header row at index {args.reference_csv_header_row}")
        print(f"[INFO] Reference CSV: {len(reference_headers)} columns (exact order and labels)")
        if args.schema_ttl:
            schema_path = Path(args.schema_ttl)
            if not schema_path.exists():
                ap.error(f"--schema-ttl not found: {schema_path}")
            print(f"[INFO] Loading schema from {schema_path} for predicate -> header mapping")
            pred_to_header, canonical_columns = _build_pred_to_reference_headers(schema_path, reference_headers)
            print(f"[INFO] Mapped {len(pred_to_header)} schema properties to reference columns")
        else:
            canonical_columns = reference_headers
            print("[WARN] No --schema-ttl; TTL predicate names may not match reference column labels")
    elif args.schema_ttl:
        schema_path = Path(args.schema_ttl)
        if not schema_path.exists():
            ap.error(f"--schema-ttl not found: {schema_path}")
        print(f"[INFO] Loading schema from {schema_path}")
        pred_to_header = _load_predicate_to_csv_header(schema_path)
        canonical_columns = _load_canonical_column_order(schema_path)
        print(f"[INFO] Schema defines {len(canonical_columns)} canonical columns")
    else:
        print("[WARN] No --schema-ttl or --reference-csv; columns will be auto-detected (may vary by file)")

    # Build a case-finder query using the provided case class IRI.
    # Keep this as SPARQL so users can override class without changing code.
    case_class_iri = str(args.case_class_iri).strip()
    if not case_class_iri:
        ap.error("--case-class-iri must be non-empty")

    case_query = f"""
PREFIX rdf: <{RDF_NS}>
SELECT ?case WHERE {{
  ?case rdf:type <{case_class_iri}> .
}}
"""

    ttl_files: List[Path] = []
    if args.input:
        for item in args.input:
            p = Path(item)
            if p.is_dir():
                ttl_files.extend(sorted(p.rglob("*.ttl")))
            elif p.is_file():
                ttl_files.append(p)
    else:
        ttl_files = _discover_ttl_files(data_dir, output_dir_name=str(args.output_dir_name))

    ttl_files = [p for p in ttl_files if p.suffix.lower() == ".ttl" and p.exists()]
    if not args.include_top:
        ttl_files = [p for p in ttl_files if p.name.lower() != "top.ttl"]
    if not ttl_files:
        print(
            f"❌ No TTL files found. Try specifying --input or check --data-dir/--output-dir-name.\n"
            f"   data_dir={data_dir}\n"
            f"   output_dir_name={args.output_dir_name}",
            file=sys.stderr,
        )
        return 2

    rows: List[Dict[str, str]] = []
    failures: List[str] = []
    print(f"[INFO] Processing {len(ttl_files)} TTL file(s)...")
    for ttl_path in ttl_files:
        try:
            rows.append(
                extract_row_from_ttl(
                    ttl_path,
                    data_dir=data_dir,
                    case_query=case_query,
                    pred_to_header=pred_to_header,
                    canonical_columns=canonical_columns,
                    missing_placeholder="-",
                    case_literal_hops=max(1, int(args.case_literal_hops)),
                )
            )
        except Exception as e:
            failures.append(f"{ttl_path}: {e}")

    if not rows:
        print("❌ No rows extracted (all files failed to parse/query).", file=sys.stderr)
        for msg in failures[:20]:
            print(f"  - {msg}", file=sys.stderr)
        return 3

    write_csv(rows, output_path=output_path, canonical_columns=canonical_columns)
    print(f"[OK] Wrote {len(rows)} row(s) to {output_path}")
    if canonical_columns:
        print(f"[INFO] Output has {len(canonical_columns) + 2} columns (2 metadata + {len(canonical_columns)} fields)")
    if failures:
        print(f"[WARN] {len(failures)} file(s) failed to parse/query; first few:", file=sys.stderr)
        for msg in failures[:10]:
            print(f"  - {msg}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

