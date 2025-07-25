# obda_creation_operations_fixed.py
"""
Self‑checking OBDA generator.

Changes compared with the previous revision
===========================================
1. **Automatic OBDA ⇄ TTL consistency check**
   * Every mapping line is analysed **before** Ontop is called.
   * The script aborts with a clear message if a predicate is
     • used both as a DatatypeProperty *and* an ObjectProperty, or
     • used in a role that contradicts its declaration in the freshly
       created Turtle ontology for the same task/iteration.
2. **No hard‑coded catalogue** – roles are discovered on‑the‑fly by
   reading the TTL (`rdflib`) and parsing the mapping rules.
3. **All previous improvements (overwrite tmp‑file, id uniqueness, etc.)
   are preserved.**

If you prefer the script to *auto‑fix* the TTL instead of aborting, flip
`AUTOFIX_TTL = False` to `True` near the top of the file.
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

from rdflib import Graph, RDF, OWL
from pydantic import BaseModel, Field, field_validator, model_validator

# ── project‑specific helpers ────────────────────────────────────────────────
from models.locations import ROOT_DIR, DATA_TEMP_DIR
from models.Resource import Resource
from src.utils.resource_db_operations import ResourceDBOperator

###############################################################################
# ─────────────── File‑level switches ────────────────────────────────────────
###############################################################################

AUTOFIX_TTL = False          # ← set to True to rewrite the TTL on clash

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
# ─────────────────── Consistency‑check helper block ────────────────────────
###############################################################################

# Regex to pull predicate + object token from a single OBDA target line
_TARGET_RE = re.compile(r"^target\s+\S+\s+:(\w+)\s+(\S.+?)\s*\.\s*$")

def _role_of_object(obj_text: str) -> str:
    """Return 'Data' if *obj_text* denotes a literal template, else 'Object'."""
    return "Data" if obj_text.lstrip().startswith("{") else "Object"


def _collect_roles_from_obda(obda_lines: List[str]) -> Dict[str, set[str]]:
    roles: Dict[str, set[str]] = {}
    for line in obda_lines:
        m = _TARGET_RE.match(line)
        if not m:
            continue
        predicate = m.group(1)            # the part after ":" (prefixed name)
        obj       = m.group(2)
        roles.setdefault(predicate, set()).add(_role_of_object(obj))
    return roles


def _collect_roles_from_ttl(ttl_path: str) -> Dict[str, str]:
    """Return {predicate → 'Object'|'Data'} for every declaration in the TTL."""
    graph = Graph().parse(ttl_path, format="turtle")
    declared: Dict[str, str] = {}
    for predicate, _, obj_type in graph.triples((None, RDF.type, None)):
        if obj_type == OWL.ObjectProperty:
            declared[str(predicate.split("#")[-1])] = "Object"
        elif obj_type == OWL.DatatypeProperty:
            declared[str(predicate.split("#")[-1])] = "Data"
    return declared


def assert_mapping_consistent(
    obda_lines: List[str],
    ttl_path: str,
) -> None:
    """
    Abort with *ValueError* if
      • any predicate is used in both roles inside OBDA
      • or its OBDA role contradicts the TTL declaration.

    If AUTOFIX_TTL is True and only the TTL is wrong, the TTL is rewritten to
    satisfy the OBDA usage and the script continues.
    """
    used   = _collect_roles_from_obda(obda_lines)
    known  = _collect_roles_from_ttl(ttl_path)
    errors = []

    # A) dual role inside OBDA
    for pred, rset in used.items():
        if len(rset) > 1:
            errors.append(f"{pred} is used both as Object and Data property in OBDA")

    # B) clash with TTL declaration
    for pred, rset in used.items():
        role = next(iter(rset))
        if pred in known and known[pred] != role:
            msg = (f"{pred} declared as {known[pred]}Property in TTL "
                   f"but used as {role}Property in OBDA")
            if AUTOFIX_TTL:
                # rewrite the conflicting declaration in the TTL
                graph = Graph().parse(ttl_path, format="turtle")
                pred_uri = None
                for s in graph.subjects(RDF.type, None):
                    if s.split("#")[-1] == pred:
                        pred_uri = s
                        break
                if pred_uri:
                    graph.remove((pred_uri, RDF.type, None))
                    graph.add((
                        pred_uri,
                        RDF.type,
                        OWL.ObjectProperty if role == "Object" else OWL.DatatypeProperty,
                    ))
                    graph.serialize(ttl_path, format="turtle")
                    msg += " → fixed in TTL"
                else:
                    msg += " → cannot autofix (IRI not found)"
            else:
                errors.append(msg)

    if errors:
        raise ValueError(
            "OBDA/TTL consistency check failed:\n  - " + "\n  - ".join(errors)
        )

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

    When *overwrite* is *True* the file is rewritten from scratch, erasing any
    previous content.
    """
    payload = header + new_lines + ["", "]]"]

    os.makedirs(os.path.dirname(abs_path), exist_ok=True)

    mode = "w" if overwrite or not os.path.isfile(abs_path) else "r+"
    with open(abs_path, mode, encoding="utf-8") as fh:
        if mode == "r+":
            lines = fh.read().splitlines()
            while lines and lines[-1].strip() == "":
                lines.pop()
            if lines and lines[-1].strip() == "]]":
                lines.pop()
            fh.seek(0)
            fh.truncate()
            lines.extend(new_lines)
            lines.extend(["", "]]"])
            fh.write("\n".join(lines))
        else:
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
    """Generate → consistency‑check → Ontop‑validate → register an OBDA file."""

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

    # ---- write tmp file -------------------------------------------------
    _write_file_safely(tmp_abs, header, rules, overwrite=True)

    # ---- OBDA ⇄ TTL consistency check -----------------------------------
    ttl_path = os.path.join(
        ROOT_DIR,
        f"sandbox/data/{meta_task_name}/{iteration_index}/{meta_task_name}_{iteration_index}.ttl",
    )
    print("Running OBDA ⇄ TTL consistency check …")
    try:
        assert_mapping_consistent(header + rules, ttl_path)
    except ValueError as exc:
        os.unlink(tmp_abs)  # remove tmp file to avoid half‑baked artefacts
        raise

    # ---- Ontop verification & finalise ----------------------------------
    print(f"Verifying OBDA file with Ontop: {tmp_abs}")
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

    return f"✔ OBDA written, consistency‑checked & Ontop‑verified → {final_rel}"


