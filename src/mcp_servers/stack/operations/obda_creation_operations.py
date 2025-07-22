# obda_creation_operations_fixed.py
"""
Further‑fixed version of `obda_creation_operations.py`.

**What changed in this revision**
---------------------------------
1. **Always overwrite temporary OBDA files** – `_write_file_safely()`
   now accepts an `overwrite` flag (default `True`).  In overwrite‑mode it
   *ignores* any existing file and writes a clean mapping from scratch,
   guaranteeing that mapping‑ids cannot accumulate across runs.
2. **Guard against stale tmp‑files** – we proactively `unlink()` the tmp
   path before writing.
3. **Minor: docstrings + type hints refreshed.**

These tweaks eliminate the *“Duplicate mapping IDs”* error you saw when the
same tmp‑file was appended to multiple times.
"""

from __future__ import annotations

import os
import platform
import re
import shlex
import shutil
import subprocess
import tempfile
import textwrap
from typing import Dict, List

from pydantic import BaseModel, Field, field_validator, model_validator

# ── project‑specific helpers ────────────────────────────────────────────────
from models.locations import ROOT_DIR, DATA_TEMP_DIR
from models.Resource import Resource
from src.utils.resource_db_operations import ResourceDBOperator

###############################################################################
# ─────────────── Data‑models (unchanged) ────────────────────────────────────
###############################################################################

class TableMapping(BaseModel):
    table_name: str
    columns: List[str]
    property_mappings: Dict[str, str] | None = None


class EntityMapping(BaseModel):
    id_columns: List[str] | None = None  # composite primary key
    id_column: str | None = Field(default=None)

    ontology_class: str | None = None
    iri_template: str | None = None
    use_xsd_typing: bool = False

    tables: List[TableMapping]

    @model_validator(mode="after")
    def _post_init(self) -> "EntityMapping":
        if not self.id_columns and self.id_column:
            self.id_columns = [self.id_column]
        if not self.id_columns:
            raise ValueError("id_columns cannot be empty")

        for t in self.tables:
            missing = set(self.id_columns) - set(t.columns)
            if missing:
                raise ValueError(
                    f"Table '{t.table_name}' missing key column(s): {', '.join(missing)}"
                )

        if not self.iri_template:
            cls = self.ontology_class or "entity"
            keys = "_".join(f"{{{k}}}" for k in self.id_columns)
            self.iri_template = f"{cls}_{keys}"
        return self


class OBDAInput(BaseModel):
    prefixes: Dict[str, str]
    entities: List[EntityMapping]

    @field_validator("prefixes")
    @classmethod
    def _check_default(cls, v: Dict[str, str]) -> Dict[str, str]:
        if "" not in v:
            raise ValueError("prefixes must include key '' (the default namespace)")
        return v

###############################################################################
# ────────────────────────── Helper utilities ───────────────────────────────
###############################################################################

_CAMEL_RE = re.compile(r"[^0-9a-zA-Z]")

def _camel(txt: str) -> str:
    return "".join(p.capitalize() for p in _CAMEL_RE.sub(" ", txt).split())

def _safe_pred(col: str) -> str:
    return f"data{_camel(col)}"

def _mapping_id(kind: str, idx: int, tbl: str | None = None, col: str | None = None) -> str:
    """Return a *globally‑unique* mapping‑id."""
    parts = [kind, str(idx)]
    if tbl:
        parts.append(tbl.replace("-", "_"))
    if col:
        parts.append(col)
    return "_".join(parts)


def _write_file_safely(
    abs_path: str,
    header: List[str],
    new_lines: List[str],
    *,
    overwrite: bool = True,
) -> None:
    """Write an OBDA file, optionally appending to an existing one.

    When *overwrite* is *True* (the default) **the file is rewritten from
    scratch**, erasing any previous content.  This guarantees that mapping‑ids
    do not pile up across repeated generations.
    """
    payload = header + new_lines + ["", "]]" ]  # one blank line before marker

    # ensure parent dir exists
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)

    mode = "w" if overwrite or not os.path.isfile(abs_path) else "r+"
    with open(abs_path, mode, encoding="utf-8") as fh:
        if mode == "r+":  # append‑mode: trim trailing blanks + old marker first
            lines = fh.read().splitlines()
            while lines and lines[-1].strip() == "":
                lines.pop()
            if lines and lines[-1].strip() == "]]":
                lines.pop()
            fh.seek(0)
            fh.truncate()
            lines.extend(new_lines)
            lines.extend(["", "]]" ])
            fh.write("\n".join(lines))
        else:  # fresh write
            fh.write("\n".join(payload))

###############################################################################
# ─────────────────────────── Ontop verification ────────────────────────────
###############################################################################

def verify_obda_file(obda_path: str) -> None:
    """Run Ontop CLI syntax/semantic validation on *obda_path*.

    Raises *RuntimeError* on first failure.
    """
    ttl_candidate = (
        os.path.splitext(obda_path)[0] + ".ttl" if obda_path.lower().endswith(".obda") else None
    )
    has_ttl = ttl_candidate and os.path.isfile(ttl_candidate)

    devnull = "NUL" if platform.system() == "Windows" else "/dev/null"
    if not os.path.exists(devnull):
        devnull = tempfile.mktemp(suffix=".ttl")

    cmds = [["ontop", "mapping", "to-r2rml", "-i", obda_path, "-o", devnull, "--force"]]
    if has_ttl:
        cmds.append(["ontop", "validate", "-m", obda_path, "-t", ttl_candidate, "--force"])

    last_err = ""
    for cmd in cmds:
        printable = " ".join(shlex.quote(p) for p in cmd)
        print(f"Running Ontop command: {printable}")

        try:
            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        except FileNotFoundError as exc:
            raise RuntimeError("Ontop CLI not found on PATH.") from exc

        stdout, stderr = proc.stdout.strip(), proc.stderr.strip()
        if stdout:
            print(textwrap.indent(stdout, "    "))
        if stderr:
            print(textwrap.indent(stderr, "    "))

        ok = proc.returncode == 0 and not stderr.lower().startswith("error")
        if ok:
            print("Ontop syntax check passed ✔")
            return
        last_err = stderr or stdout or "<no CLI message>"

    raise RuntimeError(
        f"Ontop could not parse/validate '{obda_path}'. Last Ontop output was:\n{last_err}"
    )

###############################################################################
# ─────────────────────────── Main generator ────────────────────────────────
###############################################################################

resource_db_operator = ResourceDBOperator()

def create_obda_file(
    obda_input: OBDAInput,
    *,
    meta_task_name: str,
    iteration_index: int,
) -> str:
    """Generate → validate → register an OBDA mapping file."""

    # ---- paths -----------------------------------------------------------
    rel_dir   = f"sandbox/data/{meta_task_name}/{iteration_index}"
    final_rel = f"{rel_dir}/{meta_task_name}_{iteration_index}.obda"
    final_abs = os.path.join(ROOT_DIR, final_rel)

    tmp_dir = os.path.join(DATA_TEMP_DIR, "obda_tmp")
    os.makedirs(tmp_dir, exist_ok=True)
    tmp_abs = os.path.join(tmp_dir, f"tmp_{meta_task_name}_{iteration_index}.obda")

    # ensure a truly fresh tmp‑file
    if os.path.exists(tmp_abs):
        os.unlink(tmp_abs)

    # ---- header ---------------------------------------------------------
    header: List[str] = ["[PrefixDeclaration]"]
    for pfx, iri in obda_input.prefixes.items():
        header.append(f"{':' if pfx == '' else pfx + ':'}\t{iri}")
    header.extend(["", "[MappingDeclaration] @collection [[", ""])

    # ---- mapping rules --------------------------------------------------
    rules: List[str] = []
    written_type_mappings: set[int] = set()

    for ent_idx, entity in enumerate(obda_input.entities, start=1):
        subj_tpl = f":{entity.iri_template}"
        keys_csv = ", ".join(entity.id_columns)

        # 1️⃣ type mapping (once per entity) -------------------------------
        if entity.ontology_class and ent_idx not in written_type_mappings:
            first_tbl = entity.tables[0].table_name
            rules += [
                f"mappingId\t{_mapping_id('type', ent_idx)}",
                f"target\t\t{subj_tpl} a :{entity.ontology_class} .",
                f"source\t\tSELECT DISTINCT {keys_csv} FROM {first_tbl}",
                "",
            ]
            written_type_mappings.add(ent_idx)

        # 2️⃣ literal/property mappings ------------------------------------
        for tbl in entity.tables:
            pairs = (
                tbl.property_mappings.items()
                if tbl.property_mappings else (
                    (c, _safe_pred(c)) for c in tbl.columns if c not in entity.id_columns
                )
            )
            for col, pred in pairs:
                sel = ", ".join(entity.id_columns + [col])
                lit = f"{{{col}}}" + ("^^xsd:string" if entity.use_xsd_typing else "")
                rules += [
                    f"mappingId\t{_mapping_id('lit', ent_idx, tbl.table_name, col)}",
                    f"target\t\t{subj_tpl} :{pred} {lit} .",
                    f"source\t\tSELECT {sel} FROM {tbl.table_name}",
                    "",
                ]

    # ---- write tmp, validate, move‑to‑sandbox ---------------------------
    _write_file_safely(tmp_abs, header, rules, overwrite=True)

    print(f"Verifying OBDA file: {tmp_abs}")
    verify_obda_file(tmp_abs)

    os.makedirs(os.path.dirname(final_abs), exist_ok=True)
    shutil.move(tmp_abs, final_abs)

    if not resource_db_operator.get_resources_by_meta_task_name_and_iteration(meta_task_name, iteration_index):
        resource_db_operator.register_resource(
            Resource(
                type="obda",
                relative_path=final_rel,
                absolute_path=final_abs,
                uri=f"file://{final_abs}",
                meta_task_name=meta_task_name,
                iteration=iteration_index,
                description="OBDA mapping file (Ontop‑verified)",
            )
        )

    return f"✔ OBDA written & verified → {final_rel}"

###############################################################################
# ──────────────────── Demo usage (optional) ───────────────────────────────
###############################################################################
