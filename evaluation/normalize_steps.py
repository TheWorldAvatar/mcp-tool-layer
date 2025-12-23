#!/usr/bin/env python3
"""
Normalization script for synthesis steps JSON files.
This script applies normalization rules to make predicted outputs comparable to ground truth.
"""

import json
import argparse
import re
from pathlib import Path
from typing import Any, Dict


# String mapping for normalization: key = predicted value, value = ground truth canonical form
# When normalizing, we replace predicted values with their canonical GT equivalents
STRING_MAPPING = {     
    # Temperature units and common values - normalize to "degree celsius"
    "°c": "degree celsius",
    "degree c": "degree celsius",
    "degrees celsius": "degree celsius",
    "degrees c": "degree celsius",
    "deg c": "degree celsius",
    "degc": "degree celsius",
    "celsius": "degree celsius",
    "room temperature": "25 degree celsius",
    "rt": "25 degree celsius",
    "ambient temperature": "25 degree celsius",
    "kelvin per hour": "degree celsius per hour",
    
    # Temperature rate units
    "°c/min": "degree celsius per minute",
    "°c/hour": "degree celsius per hour",
    "°c per hour": "degree celsius per hour",


    # Volume/mass units
    "milliliter": "ml",
    "milliliters": "ml",
    "millilitre": "ml",
    "millilitres": "ml",
    "liter": "l",
    "liters": "l",
    "litre": "l",
    "litres": "l",
    "gram": "g",
    "grams": "g",
    "milligram": "mg",
    "milligrams": "mg",
    "microgram": "μg",
    "micrograms": "μg",
    "kilogram": "kg",
    "kilograms": "kg",
    "millimole": "mmol",
    "millimoles": "mmol",
    "mole": "mol",
    "moles": "mol",
    "micromole": "μmol",
    "micromoles": "μmol",
    
    # Time units - normalize to short forms
    "hour": "h",
    "hours": "h",
    "hrs": "h",
    "hr": "h",
    "minute": "min",
    "minutes": "min",
    "mins": "min",
    "second": "s",
    "seconds": "s",
    "sec": "s",
    "secs": "s",
    "day": "d",
    "days": "d",
    "week": "wk",
    "weeks": "wk",
    "month": "mo",
    "months": "mo",
    
    # Atmosphere
    "air": "air",
    "nitrogen": "nitrogen",
    "n2": "nitrogen",
    "argon": "argon",
    "ar": "argon",
    "vacuum": "vacuum",
    "inert": "inert",
    "inert atmosphere": "inert",
    
    # Common N/A variants
    "n/a": "n/a",
    "na": "n/a",
    "not applicable": "n/a",
    "not available": "n/a",
    "none": "n/a",
    "no data": "n/a",
    "unknown": "n/a",
    "-": "n/a",
    "": "n/a",

    "; n/a mmol": "",
    ", n/a mmol": "",


    "slowly": "n/a",

    # Unicode primes/quotes to ASCII
    "’": "'",
    "′": "'",
    "″": '"',
    "”": '"',
    "one drop": "1 drop",
    "two drops": "2 drops",
    "three drops": "3 drops",
    "four drops": "4 drops",
    "five drops": "5 drops",
    "six drops": "6 drops",
    "seven drops": "7 drops",
    "eight drops": "8 drops",
    "nine drops": "9 drops",

    "cu(no3)2·2.5h2o":"cu(no3)2·2.5h2o",
    "cu(no3)2.6h2o": "cu(no3)2·6h2o",
    "h2ipr-cdc" : "h2(ipr-cdc)", 
}


# Chemical synonymy mapping: define canonical species -> list of equivalent names.
# Only include obvious and certain equivalences
chemical_synomy_dict = {
    # Common solvents - very certain
    "dmf": ["n, n-dimethylformamide", "dimethylformamide", "n, n'-dimethylformamide"],
    "n, n-dimethylformamide": ["dmf", "dimethylformamide", "n, n'-dimethylformamide"],
    "n, n'-dimethylformamide": ["dmf", "n, n-dimethylformamide", "dimethylformamide"],
    
    "def": ["n, n-diethylformamide", "diethylformamide"],
    "n, n-diethylformamide": ["def", "diethylformamide"],
    
    "dma": ["n, n-dimethylacetamide", "dimethylacetamide", "n, n'-dimethylacetamide", "n, n'-dimethylacetamide"],
    "n, n-dimethylacetamide": ["dma", "dimethylacetamide", "n, n'-dimethylacetamide", "n, n'-dimethylacetamide"],
    "n, n'-dimethylacetamide": ["dma", "n, n-dimethylacetamide", "dimethylacetamide"],
    
    "h2o": ["water"],
    "water": ["h2o"],
    
    "meoh": ["methanol", "ch3oh", "methyl alcohol"],
    "methanol": ["meoh", "ch3oh", "methyl alcohol"],
    "ch3oh": ["methanol", "meoh", "methyl alcohol"],
    
    "etoh": ["ethanol", "ch3ch2oh"],
    "ethanol": ["etoh", "ch3ch2oh"],
    "ch3ch2oh": ["ethanol", "etoh"],
    
    "thf": ["tetrahydrofuran"],
    "tetrahydrofuran": ["thf"],
    
    "mecn": ["acetonitrile"],
    "acetonitrile": ["mecn"],
    
    # Zirconium compounds - very certain
    "cp2zrcl2": ["zirconocene dichloride", "bis(cyclopentadienyl)zirconium dichloride"],
    "zirconocene dichloride": ["cp2zrcl2", "bis(cyclopentadienyl)zirconium dichloride"],
    
    # Vanadium compounds - certain
    "vcl3": ["vanadium(iii) chloride"],
    "vanadium(iii) chloride": ["vcl3"],
    
    # Organic ligands - certain
    "h3tatb": ["1, 3, 5-triamino-2, 4, 6-trinitrobenzene"],
    "1, 3, 5-triamino-2, 4, 6-trinitrobenzene": ["h3tatb"],
    
    # Metal salts with hydrates - certain common ones
    "ni(acetate)2·4h2o": ["nickel(ii) acetate tetrahydrate"],
    "nickel(ii) acetate tetrahydrate": ["ni(acetate)2·4h2o"],
    
    "cu(no3)2·3h2o": ["copper(ii) nitrate trihydrate"],
    "copper(ii) nitrate trihydrate": ["cu(no3)2·3h2o"],
    
    "cucl2·2h2o": ["copper(ii) chloride dihydrate"],
    "copper(ii) chloride dihydrate": ["cucl2·2h2o"],
    
    # Vanadium compounds - certain
    "voso4·xh2o": ["vanadyl sulfate hydrate"],
    "vanadyl sulfate hydrate": ["voso4·xh2o"],
    
    "vo(acac)2": ["vanadyl acetylacetonate"],
    "vanadyl acetylacetonate": ["vo(acac)2"],
    
    # Organic ligands with abbreviations - certain
    "5-eia": ["5-ethoxy isophthalic acid"],
    "5-ethoxy isophthalic acid": ["5-eia"],

    "5-pria": ["5-(n-propyloxy) isophthalic acid"],
    "5-(n-propyloxy) isophthalic acid": ["5-pria"],

    # Additional mappings from evaluation results
    "h2d-cam": ["d-camphoric acid"],
    "d-camphoric acid": ["h2d-cam"],

    "5-mia": ["5-methoxy isophthalic acid"],
    "5-methoxy isophthalic acid": ["5-mia"],

    # From addedChemical.names.md evaluation
    "h2ndbdc": ["4, 4'-(naphthalene-1, 4-diyl)dibenzoic acid"],
    "4, 4'-(naphthalene-1, 4-diyl)dibenzoic acid": ["h2ndbdc"],

    "h2adbdc": ["4, 4'-(anthracene-9, 10-diyl)dibenzoic acid"],
    "4, 4'-(anthracene-9, 10-diyl)dibenzoic acid": ["h2adbdc"],

    "h2dcpp": ["4, 4'-(porphyrin-5, 15-diyl)dibenzoic acid"],
    "4, 4'-(porphyrin-5, 15-diyl)dibenzoic acid": ["h2dcpp"],
}



def apply_string_mapping(s: str) -> str:
    """Apply string mapping to normalize variations."""
    s_lower = s.lower().strip()
    return STRING_MAPPING.get(s_lower, s_lower)


def normalize_chemical_amount(s: Any) -> str:
    """Normalize chemical amount strings, converting parentheses/semicolons to comma format.
    Examples:
    - "0.045 g (0.276 mmol)" -> "0.045 g, 0.276 mmol"
    - "0.045 g; 0.276 mmol" -> "0.045 g, 0.276 mmol"
    """
    val = str(s or "").strip()
    # Normalize whitespace
    val = re.sub(r'\s+', ' ', val)
    # Normalize comma-space patterns
    val = re.sub(r',\s*', ', ', val)
    val = val.lower().strip()
    
    # Convert parentheses and semicolons to comma format for compound amounts
    val = re.sub(r'\s*\(\s*', ', ', val)  # Replace " (" with ", "
    val = re.sub(r'\s*\)\s*', '', val)     # Remove ")"
    val = re.sub(r'\s*;\s*', ', ', val)    # Replace ";" with ", "
    
    # Helper: classify unit category for ordering (lower comes first)
    def _unit_rank(unit_str: str) -> int:
        u = apply_string_mapping(unit_str)  # canonicalize common variants
        # Order: amount-of-substance (mol) < mass (g) < volume (l) < others
        mol_units = {"mol", "mmol", "μmol", "umol"}
        mass_units = {"kg", "g", "mg", "μg"}
        vol_units = {"l", "ml"}
        # Extract first token (in case of composite like "/60 v/v")
        token = u.split()[0]
        if token in mol_units:
            return 0
        if token in mass_units:
            return 1
        if token in vol_units:
            return 2
        return 3

    # Helper: format number with up to two decimals; if integer keep integer
    def _format_number(num_str: str) -> str:
        try:
            f = float(num_str)
        except ValueError:
            return num_str.strip()
        if abs(f - round(f)) < 1e-9:
            return str(int(round(f)))
        # Always keep two decimals when fractional part exists
        return f"{f:.2f}"

    # Try to normalize as a compound amount list: "number unit, number unit, ..."
    if ',' in val:
        parts = [p.strip() for p in val.split(',') if p.strip()]
        normalized_items = []
        all_ok = True
        for part in parts:
            m = re.match(r'^([\d.]+)\s*(.+)$', part)
            if not m:
                all_ok = False
                break
            num_raw, unit_raw = m.groups()
            num_fmt = _format_number(num_raw)
            unit_fmt = apply_string_mapping(unit_raw)
            normalized_items.append(( _unit_rank(unit_fmt), f"{num_fmt} {unit_fmt}" ))
        if all_ok and normalized_items:
            # Order by preferred unit rank while preserving relative order within same rank
            normalized_items.sort(key=lambda x: x[0])
            return ', '.join(item for _rank, item in normalized_items)

    # Fallback: single value with unit
    m = re.match(r'^([\d.]+)\s*(.+)$', val)
    if m:
        num_raw, unit_raw = m.groups()
        num_fmt = _format_number(num_raw)
        unit_fmt = apply_string_mapping(unit_raw)
        return f"{num_fmt} {unit_fmt}"

    # Now apply the regular normalize_string logic if no number/unit pattern
    return normalize_string(val)


def normalize_string(s: Any) -> str:
    """Normalize a value to lowercase string, treating N/A as empty, with string mapping.
    Includes numerical normalization for values with units (e.g., "160.0 degree celsius" -> "160 degree celsius").
    """
    val = str(s or "").strip()
    
    # First, replace unicode characters that should be normalized (before lowercasing)
    # This handles primes, special quotes, subscripts, and superscripts
    char_replacements = {
        "'": "'",   # Right single curly quote to ASCII apostrophe
        "'": "'",   # Left single curly quote to ASCII apostrophe
        "′": "'",   # Prime to ASCII apostrophe
        "‵": "'",   # Reversed prime to ASCII apostrophe
        """: '"',   # Left double curly quote to ASCII double quote
        """: '"',   # Right double curly quote to ASCII double quote
        "″": '"',   # Double prime to ASCII double quote
        # Unicode subscripts to regular numbers
        "₀": "0", "₁": "1", "₂": "2", "₃": "3", "₄": "4",
        "₅": "5", "₆": "6", "₇": "7", "₈": "8", "₉": "9",
        # Unicode superscripts to regular numbers
        "⁰": "0", "¹": "1", "²": "2", "³": "3", "⁴": "4",
        "⁵": "5", "⁶": "6", "⁷": "7", "⁸": "8", "⁹": "9",
        # Unicode multiplication/dot to regular dot
        "·": "·",  # Middle dot (keep as is for hydrates)
        "•": "·",  # Bullet to middle dot
    }
    for old_char, new_char in char_replacements.items():
        val = val.replace(old_char, new_char)
    
    # Normalize whitespace
    val = re.sub(r'\s+', ' ', val)
    # Normalize comma-space patterns (remove spaces before commas, ensure single space after)
    val = re.sub(r'\s*,\s*', ', ', val)
    # Normalize spaces around hyphens (e.g., "5 -obut" -> "5-obut")
    val = re.sub(r'\s+-\s+', '-', val)
    val = re.sub(r'\s+-', '-', val)
    val = re.sub(r'-\s+', '-', val)
    val = val.lower().strip()
    
    # Try to apply string mapping to the whole string first
    mapped = apply_string_mapping(val)
    if mapped != val:
        return mapped
    
    # Check if this is a compound amount (multiple value+unit pairs separated by commas)
    # Pattern: "number unit, number unit" or "number unit, number unit, ..."
    # We look for at least one comma to indicate compound format
    if ',' in val:
        # Split by comma and try to normalize each part
        parts = [p.strip() for p in val.split(',')]
        normalized_parts = []
        all_parts_valid = True
        
        for part in parts:
            # Each part should be "number unit"
            match_part = re.match(r'^([\d.]+)\s*(.+)$', part)
            if match_part:
                number_str, unit = match_part.groups()
                try:
                    number_float = float(number_str)
                    # If the float is an integer (e.g., 160.0), convert to int string (160)
                    if number_float == int(number_float):
                        number_str = str(int(number_float))
                except ValueError:
                    all_parts_valid = False
                    break
                
                # Try to map the unit part
                mapped_unit = apply_string_mapping(unit)
                normalized_parts.append(f"{number_str} {mapped_unit}")
            else:
                # Part doesn't match number+unit pattern
                all_parts_valid = False
                break
        
        # If all parts were successfully normalized, return the compound amount
        if all_parts_valid and normalized_parts:
            return ', '.join(normalized_parts)
    
    # If no direct mapping and not a compound amount, try to handle simple numeric values with units
    # Pattern: optional number + optional space + unit
    # NOTE: Unit must NOT start with a digit to avoid splitting pure numbers (e.g., CCDC numbers)
    match_with_unit = re.match(r'^([\d.]+)\s+([^\d].*)$', val)
    if match_with_unit:
        number_str, unit = match_with_unit.groups()
        try:
            number_float = float(number_str)
            # If the float is an integer (e.g., 160.0), convert to int string (160)
            if number_float == int(number_float):
                number_str = str(int(number_float))
        except ValueError:
            pass # Not a valid number, keep as is

        # Try to map the unit part
        mapped_unit = apply_string_mapping(unit)
        return f"{number_str} {mapped_unit}"

    # Handle standalone numbers (e.g., "24.0" -> "24")
    try:
        number_float = float(val)
        if number_float == int(number_float):
            return str(int(number_float))
    except ValueError:
        pass # Not a standalone number

    return val


def normalize_chemical_name(name: str) -> str:
    """Normalize a chemical name using the synonym dictionary.
    
    Returns the canonical form if found in the dictionary, otherwise returns normalized string.
    """
    normalized = normalize_string(name)
    
    # Check if this name has a canonical form in the dictionary
    for canonical, synonyms in chemical_synomy_dict.items():
        if normalized == canonical.lower():
            return canonical.lower()
        if normalized in [s.lower() for s in synonyms]:
            return canonical.lower()
    
    return normalized


def normalize_json_structure(obj: Any, parent_key: str = None, grandparent_key: str = None) -> Any:
    """Recursively normalize all string values in a JSON structure.
    
    Args:
        obj: The object to normalize
        parent_key: The key of the parent dict (used to apply special normalization)
        grandparent_key: The key of the grandparent (for nested structures)
    """
    if isinstance(obj, dict):
        return {k: normalize_json_structure(v, parent_key=k, grandparent_key=parent_key) for k, v in obj.items()}
    elif isinstance(obj, list):
        # When processing a list, keep the parent_key so items know their context
        return [normalize_json_structure(item, parent_key=parent_key, grandparent_key=grandparent_key) for item in obj]
    elif isinstance(obj, str):
        if obj:
            # Apply special normalization for chemicalAmount fields
            if parent_key in ("chemicalAmount", "amount"):
                return normalize_chemical_amount(obj)
            # Apply chemical name normalization for chemicalName fields (array items)
            elif parent_key == "chemicalName":
                return normalize_chemical_name(obj)
            else:
                return normalize_string(obj)
        else:
            return obj
    else:
        return obj


def normalize_steps_file(input_file: Path, output_file: Path = None) -> None:
    """
    Normalize a steps JSON file.
    
    Args:
        input_file: Path to input JSON file
        output_file: Path to output JSON file (if None, overwrites input)
    """
    # Load input file
    with open(input_file, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    # Normalize the entire structure
    normalized_data = normalize_json_structure(data)
    
    # Write output
    output_path = output_file if output_file else input_file
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(normalized_data, f, indent=2, ensure_ascii=False)
    
    print(f"Normalized {input_file} -> {output_path}")


def main():
    parser = argparse.ArgumentParser(
        description='Normalize synthesis steps JSON files for consistent comparison with ground truth.'
    )
    parser.add_argument(
        'input',
        type=Path,
        help='Input JSON file to normalize'
    )
    parser.add_argument(
        '-o', '--output',
        type=Path,
        default=None,
        help='Output JSON file (default: overwrite input)'
    )
    
    args = parser.parse_args()
    
    if not args.input.exists():
        print(f"Error: Input file {args.input} does not exist")
        return 1
    
    normalize_steps_file(args.input, args.output)
    return 0


if __name__ == '__main__':
    exit(main())

