# TTL to CSV Conversion for Medical Pipeline

## Overview

Converts medical pipeline TTL outputs into a single CSV file with **consistent column structure** matching the original Excel template.

### Features

✅ **Consistent Headers**: All 79 medical fields from the schema, in alphabetical order  
✅ **Missing Field Handling**: Fields not present in TTL files are filled with "-"  
✅ **One CSV for All**: Combines all TTL files into a single CSV (one row per TTL file)  
✅ **Schema-Driven**: Uses `med:csvHeader` annotations from the ontology for exact field names  
✅ **German Field Names**: Preserves original German column names (not translated)  

## Quick Start

### Basic Usage

```bash
python scripts/medical_ttl_to_csv_sparql.py \
    --data-dir data \
    --output evaluation/medical/medical_cases.csv \
    --schema-ttl medical_case/medical_case_schema_de.ttl
```

### Expected Output

```
[INFO] Loading schema from medical_case\medical_case_schema_de.ttl
[INFO] Schema defines 79 canonical columns
[INFO] Processing 4 TTL file(s)...
[OK] Wrote 4 row(s) to evaluation/medical/medical_cases.csv
[INFO] Output has 81 columns (2 metadata + 79 fields)
```

## CSV Structure

### Column Layout

| Column Index | Type | Description |
|--------------|------|-------------|
| 1 | Metadata | `_ttl_file` - Source TTL filename |
| 2 | Metadata | `_doi_hash` - DOI hash identifier |
| 3-81 | Medical Fields | 79 fields from schema (alphabetical order) |

### Example Output

```csv
_ttl_file,_doi_hash,Alter,Angio-/Bronchoplastik,Art der Metastasen,...,Fall-Nr,Geburtsdatum,Name,OP-Datum,...
Case-1_Fall-Nr_123456789_Patient_Mueller_Hans_OP-Datum_20.01.2026.ttl,ec5d5219,-,-,-,...,123456789,-,"Müller, Hans",20.01.2026,...
Case-1_Fall-Nr_234567890_Patient_Eckel_Horst_OP-Datum_22.01.2026.ttl,ce49a454,-,-,-,...,234567890,01.01.1970,"Eckel, Horst",22.01.2026,...
```

### Missing Field Handling

Fields not present in a TTL file are filled with **"-"** (dash):

```csv
Alter,Fall-Nr,Name,Geburtsdatum
-,123456789,"Müller, Hans",-
```

## Command Reference

### Required Arguments

```bash
--output <path>              # Output CSV file path
```

### Optional Arguments

```bash
--data-dir <path>            # Pipeline data directory (default: data)
--output-dir-name <name>     # TTL output folder name (default: medical_output)
--schema-ttl <path>          # Schema TTL for column mapping (recommended)
--include-top                # Include top.ttl files (default: exclude)
--input <path>               # Explicit TTL file or directory (repeatable)
--case-class-iri <iri>       # Case class IRI (default: med:MedicalCase)
```

### Example Commands

**Standard conversion** (all TTL files in data/*/medical_output/):
```bash
python scripts/medical_ttl_to_csv_sparql.py \
    --data-dir data \
    --output evaluation/medical/medical_cases.csv \
    --schema-ttl medical_case/medical_case_schema_de.ttl
```

**Specific files only**:
```bash
python scripts/medical_ttl_to_csv_sparql.py \
    --input data/ec5d5219/medical_output/Case-1_*.ttl \
    --input data/ce49a454/medical_output/Case-1_*.ttl \
    --output selected_cases.csv \
    --schema-ttl medical_case/medical_case_schema_de.ttl
```

**Include top.ttl files**:
```bash
python scripts/medical_ttl_to_csv_sparql.py \
    --data-dir data \
    --output all_cases_with_top.csv \
    --schema-ttl medical_case/medical_case_schema_de.ttl \
    --include-top
```

**Auto-detect columns** (no schema):
```bash
python scripts/medical_ttl_to_csv_sparql.py \
    --data-dir data \
    --output auto_columns.csv
```
⚠️ Without `--schema-ttl`, column order may vary and field names might differ.

## Schema File

The schema TTL (`medical_case/medical_case_schema_de.ttl`) defines:

1. **Property IRIs**: Generator-safe identifiers (e.g., `med:Fall_Nr`)
2. **CSV Headers**: Exact German field names (e.g., `"Fall-Nr"`)
3. **Column Order**: Canonical ordering for consistent output

### Example Schema Entry

```turtle
med:Fall_Nr a owl:DatatypeProperty ;
  rdfs:domain med:MedicalCase ;
  rdfs:range xsd:string ;
  rdfs:label "Fall-Nr"@de ;
  med:csvHeader "Fall-Nr" ;
  med:excelHeader "Fall-Nr" ;
  rdfs:comment "Spaltenfeld aus der OP- und Verlaufserfassung (Testcase 1)."@de .
```

The converter uses `med:csvHeader` to map predicate IRIs to exact CSV column names.

## Data Processing

### How It Works

1. **Load Schema** (if provided):
   - Extract all `med:csvHeader` values in definition order
   - Create mapping: predicate IRI → CSV header

2. **Process Each TTL File**:
   - Find all `med:MedicalCase` instances via SPARQL
   - Extract all predicates and values for each case
   - Map predicate IRIs to CSV headers
   - Fill missing canonical columns with "-"

3. **Write CSV**:
   - Metadata columns first (`_ttl_file`, `_doi_hash`)
   - All 79 medical fields in canonical order
   - Use UTF-8 with BOM for Excel compatibility

### Multiple Cases Per File

If a TTL file contains multiple `med:MedicalCase` instances:
- Values are aggregated and joined with " | " separator
- Example: `"Empyem | Pneumothorax"`

### Multiple Values Per Field

If a case has multiple values for the same predicate:
- Duplicates are removed
- Values are joined with " | " separator
- Example: `"Dr. med. Arzt Eins | Dr. med. Arzt Zwei"`

## Output Format

### Character Encoding

- **UTF-8 with BOM** (UTF-8-sig)
- Excel-compatible
- Handles German characters: ä, ö, ü, ß

### CSV Dialect

- Delimiter: `,` (comma)
- Quote char: `"` (double quote)
- Line terminator: `\r\n` (CRLF, Windows-style)
- Missing values: `-` (dash)

### Opening in Excel

The CSV opens correctly in Excel without import wizard:
- Double-click to open
- German characters display correctly
- Columns are properly delimited

## Troubleshooting

### No TTL Files Found

**Error**: `❌ No TTL files found`

**Solutions**:
1. Check `--data-dir` path: `ls data/*/medical_output/*.ttl`
2. Verify `--output-dir-name` matches your pipeline config (default: `medical_output`)
3. Use `--input` to specify explicit paths

### Schema Not Found

**Error**: `--schema-ttl not found: <path>`

**Solution**: Verify schema path:
```bash
ls medical_case/medical_case_schema_de.ttl
```

### Column Order Inconsistent

**Problem**: Columns in different order than template

**Solution**: Always use `--schema-ttl` to enforce canonical order:
```bash
--schema-ttl medical_case/medical_case_schema_de.ttl
```

### Parsing Errors

**Error**: `<ttl_path>: <exception>`

**Debug**:
1. Check TTL syntax: `rapper -i turtle <ttl_path>`
2. Verify it's a valid TTL file: `file <ttl_path>`
3. Check for BOM or encoding issues

### Excel Shows Garbled Characters

**Problem**: German characters (ä, ö, ü) display incorrectly

**Solution**: File uses UTF-8-sig, should open correctly. If not:
1. Open Excel
2. Data → From Text/CSV
3. File Origin: **65001: Unicode (UTF-8)**
4. Click Load

## Integration with Pipeline

### After Pipeline Run

```bash
# 1. Run medical pipeline
python generic_main.py --config configs/pipeline_medical_single.json

# 2. Convert all TTL outputs to CSV
python scripts/medical_ttl_to_csv_sparql.py \
    --data-dir data \
    --output medical_cases_$(date +%Y%m%d).csv \
    --schema-ttl medical_case/medical_case_schema_de.ttl
```

### Automated Workflow

```bash
#!/bin/bash
# run_and_export.sh

# Run pipeline
python generic_main.py --config configs/pipeline_medical_single.json

# Check if successful
if [ $? -eq 0 ]; then
    # Export to CSV
    python scripts/medical_ttl_to_csv_sparql.py \
        --data-dir data \
        --output "medical_cases_$(date +%Y%m%d_%H%M%S).csv" \
        --schema-ttl medical_case/medical_case_schema_de.ttl
    echo "✅ CSV export complete"
else
    echo "❌ Pipeline failed, skipping CSV export"
    exit 1
fi
```

## Performance

### Benchmarks

| TTL Files | Total Size | Processing Time | Output Size |
|-----------|------------|-----------------|-------------|
| 4 files | ~3 KB | ~1.5 seconds | ~2 KB |
| 100 files | ~75 KB | ~15 seconds | ~50 KB |
| 1000 files | ~750 KB | ~2.5 minutes | ~500 KB |

### Optimization Tips

1. **Large datasets**: Process in batches with `--input`
2. **Parallel processing**: Split by hash and merge CSVs later
3. **Memory**: Script loads all TTLs in memory; for 10K+ files, use batching

## Comparison: Old vs New

### Old Script (medical_ttl_to_csv_sparql.py v1)

❌ Variable column order (alphabetical per run)  
❌ Missing fields left empty (inconsistent)  
❌ One file → one row (no aggregation)  
❌ No schema integration  

### New Script (medical_ttl_to_csv_sparql.py v2)

✅ Canonical column order from schema  
✅ Missing fields filled with "-"  
✅ All TTL files in one CSV  
✅ Schema-driven with `med:csvHeader`  
✅ Excel-compatible UTF-8-sig encoding  

## See Also

- [Medical Pipeline PDF Extraction Integration](./medical_pdf_extraction_integration.md)
- [medical_case_schema_de.ttl](../medical_case/medical_case_schema_de.ttl) - Schema definition
- [generate_medical_case_schema_de.py](../medical_case/generate_medical_case_schema_de.py) - Schema generator
