"""Pack, unpack, and check eval PDFs plus frozen MCP packages.

Git does not store PDFs, generated MCP packs, or extraction runtimes.
This module builds two local zip files and unpacks them into the paths
the locked one-click runner expects.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from ship_lib import (
    MAIN_PAPERS,
    MAIN_PDF_DIR,
    ONTOMED_PAPERS,
    ONTOMED_PDF_DIR,
    load_papers,
    pdf_stem,
    repo_root,
)

BUNDLE_DIR = Path("data") / "eval_bundles"
PDF_ZIP_NAME = "eval30_pdfs.zip"
MCP_ZIP_NAME = "locked_mcp_packs.zip"
EXPECTED_NAME = "expected_files.json"

CHEMISTRY_PACKS = {
    "s1": Path("generated") / "runs" / "0908-fullpack-s1_newmcp",
    "s2": Path("generated") / "runs" / "0908-fullpack-s2_newmcp",
    "s3": Path("generated") / "runs" / "0908-fullpack-s3_newmcp",
    "s4": Path("generated") / "runs" / "0908-fullpack-kimi_newmcp",
}
MEDICAL_PACK = Path("generated") / "runs" / "med-s1_newmcp"
ALL_MCP_PACKS = tuple(CHEMISTRY_PACKS.values()) + (MEDICAL_PACK,)

# Official 0908-fullpack-s1 OntoSynthesis extraction prompts. Byte-identical
# on s1 / s1_mcp / s1_newmcp. Locked extract must not swap in a later GPT-5
# authoring (e2e1, slimlock_ext, extv2 extension files, run.cmd `gpt5` tag).
FROZEN_S1_ONTOSYNTHESIS_PROMPT_SHA256 = {
    "EXTRACTION_ITER_1.md": "fa489405cf19bb15d62798586fb6ada867eb4223ee770c2b3e0635cf768f7949",
    "EXTRACTION_ITER_2.md": "6b9369d917b596de2f0cdbd1349bba2650f242208a5715bbf737851450453e7b",
    "EXTRACTION_ITER_2.materializable.inc": "3bab8d3cd500c0389fdf53e4500881045347763a3f464b90e93caaaf729d4141",
    "EXTRACTION_ITER_3.md": "9cf6ef2d5d2a9169ad7cec4161c44c84460696ce21464aee1cbcec4ba58088ea",
    "EXTRACTION_ITER_3.materializable.inc": "889c87effe56c1c65e6891bc0d1ae719bdbd0f7a5535a9d9b99056b4156fa03f",
    "EXTRACTION_ITER_4.md": "4ec281cfdab7c4172fe1708fb7dc97c6698ccca1eae955dfb58c0937db9d7653",
    "EXTRACTION_ITER_4.materializable.inc": "02e6ac11c1b851856ba9d7f9b3d8a9389f98e9103d1a354a83dcae86b9583d2e",
    "PRE_EXTRACTION_ITER_3.md": "bc476864804e5f5895c7d2e93e8ef5f653f5518023aac869ac147f4c72186a62",
    "PRE_EXTRACTION_ITER_3.materializable.inc": "ebc755b071eef840dd2614c5a1acc99ec512b85f86e19eb18071ecaf442d6080",
}

SKIP_DIR_NAMES = {"__pycache__", ".pytest_cache", ".git"}
SKIP_SUFFIXES = {".pyc", ".pyo"}


def bundle_dir(root: Path | None = None) -> Path:
    return (root or repo_root()) / BUNDLE_DIR


def pdf_zip_path(root: Path | None = None) -> Path:
    return bundle_dir(root) / PDF_ZIP_NAME


def mcp_zip_path(root: Path | None = None) -> Path:
    return bundle_dir(root) / MCP_ZIP_NAME


def expected_manifest_path(root: Path | None = None) -> Path:
    return bundle_dir(root) / EXPECTED_NAME


def chemistry_pack_path(pack: str, *, root: Path | None = None) -> Path:
    key = str(pack or "s1").strip().lower()
    relative = CHEMISTRY_PACKS.get(key)
    if relative is None:
        raise ValueError(f"Unknown chemistry pack {pack!r}; expected s1, s2, s3, or s4")
    return (root or repo_root()) / relative


def medical_pack_path(*, root: Path | None = None) -> Path:
    return (root or repo_root()) / MEDICAL_PACK


def mcp_pack_ready(path: Path) -> bool:
    return path.is_dir() and (path / "scripts").is_dir()


def _extraction_prompt_files(pack_root: Path) -> list[Path]:
    prompts = Path(pack_root) / "prompts"
    if not prompts.is_dir():
        return []
    files: list[Path] = []
    for path in sorted(prompts.rglob("*")):
        if not path.is_file():
            continue
        name = path.name.upper()
        if name.startswith(("EXTRACTION_ITER_", "PRE_EXTRACTION_ITER_")):
            files.append(path)
    return files


def assert_frozen_extraction_prompts(pack_root: Path, *, pack_id: str | None = None) -> None:
    """Refuse extract if the pack is missing slim-family prompts or s1 drifted."""
    from src.extraction_prompt_generation.slim_family_lock import (
        assert_slim_extraction_prompt_family,
    )

    root = Path(pack_root)
    files = _extraction_prompt_files(root)
    if not files:
        raise FileNotFoundError(
            f"Frozen pack has no EXTRACTION_ITER / PRE_EXTRACTION prompts: {root}"
        )
    for path in files:
        if path.suffix.lower() != ".md":
            continue
        assert_slim_extraction_prompt_family(
            path.name, path.read_text(encoding="utf-8")
        )
    key = str(pack_id or "").strip().lower()
    if key != "s1":
        return
    folder = root / "prompts" / "ontosynthesis"
    for name, expected in FROZEN_S1_ONTOSYNTHESIS_PROMPT_SHA256.items():
        path = folder / name
        if not path.is_file():
            raise FileNotFoundError(f"s1 extraction prompt missing: {path}")
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            raise ValueError(
                f"s1 extraction prompt is not the official 0908-fullpack-s1 file: {name}"
            )


def _posix(path: Path) -> str:
    return path.as_posix()


def _safe_zip_member(name: str) -> Path:
    normalized = name.replace("\\", "/")
    relative = Path(normalized)
    drive = len(normalized) >= 2 and normalized[1] == ":"
    if (
        normalized.startswith("/")
        or drive
        or relative.is_absolute()
        or bool(relative.anchor)
        or ".." in relative.parts
    ):
        raise ValueError(f"Refusing zip member {name!r}")
    return relative


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def expected_pdf_relpaths(*, root: Path | None = None) -> dict[str, list[str]]:
    root = root or repo_root()
    chemistry: list[str] = []
    for paper in load_papers(root / MAIN_PAPERS):
        stem = pdf_stem(paper["doi"])
        chemistry.append(_posix(MAIN_PDF_DIR / f"{stem}.pdf"))
        chemistry.append(_posix(MAIN_PDF_DIR / f"{stem}_si.pdf"))
    medical = [
        _posix(ONTOMED_PDF_DIR / f"{pdf_stem(paper['doi'])}.pdf")
        for paper in load_papers(root / ONTOMED_PAPERS)
    ]
    return {"chemistry": chemistry, "ontomed": medical}


def write_expected_manifest(*, root: Path | None = None) -> Path:
    root = root or repo_root()
    payload = {
        "note": (
            "Filenames only. PDFs and MCP packs are not in git. "
            "Place eval30_pdfs.zip and locked_mcp_packs.zip in data/eval_bundles/ "
            "and run: python scripts/eval_inputs.py unpack"
        ),
        "pdf_zip": PDF_ZIP_NAME,
        "mcp_zip": MCP_ZIP_NAME,
        "pdfs": expected_pdf_relpaths(root=root),
        "mcp_packs": {
            "s1": _posix(CHEMISTRY_PACKS["s1"]),
            "s2": _posix(CHEMISTRY_PACKS["s2"]),
            "s3": _posix(CHEMISTRY_PACKS["s3"]),
            "s4": _posix(CHEMISTRY_PACKS["s4"]),
            "medical": _posix(MEDICAL_PACK),
        },
        "unpack_to": "repository root (paths inside the zip are already relative)",
    }
    path = expected_manifest_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def iter_pdf_files(root: Path | None = None) -> list[Path]:
    root = root or repo_root()
    found: list[Path] = []
    for folder in (root / MAIN_PDF_DIR, root / ONTOMED_PDF_DIR):
        if not folder.is_dir():
            continue
        found.extend(sorted(path for path in folder.glob("*.pdf") if path.is_file()))
    return found


def _skip_mcp_path(path: Path) -> bool:
    if path.suffix.lower() in SKIP_SUFFIXES:
        return True
    return any(part in SKIP_DIR_NAMES for part in path.parts)


def iter_mcp_files(root: Path | None = None) -> list[Path]:
    root = root or repo_root()
    found: list[Path] = []
    for relative in ALL_MCP_PACKS:
        folder = root / relative
        if not folder.is_dir():
            continue
        found.extend(
            sorted(
                path
                for path in folder.rglob("*")
                if path.is_file() and not _skip_mcp_path(path.relative_to(root))
            )
        )
    return found


def missing_required_pdfs(*, cases: int, domain: str, root: Path | None = None) -> list[str]:
    from ship_lib import missing_pdfs, select_eval_cases

    root = root or repo_root()
    missing: list[str] = []
    if domain in {"both", "main"}:
        missing.extend(missing_pdfs(select_eval_cases(cases, kind="main", root=root), root / MAIN_PDF_DIR))
    if domain in {"both", "ontomed"}:
        missing.extend(
            missing_pdfs(select_eval_cases(cases, kind="ontomed", root=root), root / ONTOMED_PDF_DIR)
        )
    return missing


def _write_zip(zip_path: Path, files: Iterable[Path], *, root: Path) -> int:
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, _posix(path.relative_to(root)))
            count += 1
    return count


def pack_pdfs(*, root: Path | None = None, force: bool = False) -> Path:
    root = root or repo_root()
    target = pdf_zip_path(root)
    files = iter_pdf_files(root)
    if not files:
        raise FileNotFoundError(
            f"No PDFs under {MAIN_PDF_DIR} or {ONTOMED_PDF_DIR}. Copy eval papers first."
        )
    if target.is_file() and not force:
        print(f"[OK] Keeping existing {target}")
        return target
    count = _write_zip(target, files, root=root)
    print(f"[OK] Wrote {target} ({count} PDFs)")
    return target


def pack_mcp(*, root: Path | None = None, force: bool = False) -> Path:
    root = root or repo_root()
    target = mcp_zip_path(root)
    files = iter_mcp_files(root)
    if not files:
        raise FileNotFoundError(
            "No frozen MCP packs under generated/runs/. "
            "Need 0908-fullpack-s{1,2,3}_newmcp, 0908-fullpack-kimi_newmcp, and med-s1_newmcp."
        )
    if target.is_file() and not force:
        print(f"[OK] Keeping existing {target}")
        return target
    count = _write_zip(target, files, root=root)
    print(f"[OK] Wrote {target} ({count} files)")
    return target


def unpack_zip(zip_path: Path, *, root: Path | None = None, force: bool = False) -> int:
    root = root or repo_root()
    if not zip_path.is_file():
        raise FileNotFoundError(zip_path)
    written = 0
    with zipfile.ZipFile(zip_path, "r") as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            relative = _safe_zip_member(info.filename)
            destination = root / relative
            if destination.is_file() and not force:
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(archive.read(info.filename))
            written += 1
    print(f"[OK] Unpacked {written} files from {zip_path.name}")
    return written


def unpack_if_present(*, root: Path | None = None, force: bool = False) -> dict[str, int]:
    root = root or repo_root()
    counts = {"pdfs": 0, "mcp": 0}
    pdf_zip = pdf_zip_path(root)
    mcp_zip = mcp_zip_path(root)
    if pdf_zip.is_file():
        counts["pdfs"] = unpack_zip(pdf_zip, root=root, force=force)
    if mcp_zip.is_file():
        counts["mcp"] = unpack_zip(mcp_zip, root=root, force=force)
    return counts


def check_inputs(
    *,
    cases: int = 30,
    domain: str = "both",
    pack: str = "s1",
    root: Path | None = None,
) -> dict[str, object]:
    root = root or repo_root()
    pdf_missing = missing_required_pdfs(cases=cases, domain=domain, root=root)
    chemistry_pack = chemistry_pack_path(pack, root=root)
    medical = medical_pack_path(root=root)
    report = {
        "pdf_zip": str(pdf_zip_path(root)),
        "pdf_zip_present": pdf_zip_path(root).is_file(),
        "mcp_zip": str(mcp_zip_path(root)),
        "mcp_zip_present": mcp_zip_path(root).is_file(),
        "pdfs_missing": pdf_missing,
        "chemistry_pack": str(chemistry_pack),
        "chemistry_pack_ready": mcp_pack_ready(chemistry_pack),
        "medical_pack": str(medical),
        "medical_pack_ready": mcp_pack_ready(medical),
    }
    return report


def print_check(report: dict[str, object]) -> int:
    print("Eval inputs")
    print(f"  PDF zip:  {'yes' if report['pdf_zip_present'] else 'NO '}  {report['pdf_zip']}")
    print(f"  MCP zip:  {'yes' if report['mcp_zip_present'] else 'NO '}  {report['mcp_zip']}")
    missing = list(report["pdfs_missing"] or [])
    if missing:
        print(f"  PDFs missing ({len(missing)}):")
        for item in missing[:12]:
            print(f"    - {item}")
        if len(missing) > 12:
            print(f"    - … {len(missing) - 12} more")
    else:
        print("  PDFs: required files are present")
    print(
        "  Chemistry MCP: "
        + ("ready  " if report["chemistry_pack_ready"] else "MISSING")
        + f"  {report['chemistry_pack']}"
    )
    print(
        "  OntoMed MCP:   "
        + ("ready  " if report["medical_pack_ready"] else "MISSING")
        + f"  {report['medical_pack']}"
    )
    ok = not missing and bool(report["chemistry_pack_ready"]) and bool(report["medical_pack_ready"])
    return 0 if ok else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Pack or unpack eval PDFs and frozen MCP packages (not stored in git)."
    )
    parser.add_argument("action", choices=("pack", "unpack", "check"))
    parser.add_argument("--what", choices=("all", "pdfs", "mcp"), default="all")
    parser.add_argument("--force", action="store_true", help="Overwrite existing files / zips.")
    parser.add_argument(
        "--optional",
        action="store_true",
        help="Unpack: warn and continue when a zip is missing (used by setup.cmd).",
    )
    parser.add_argument("--cases", type=int, default=30)
    parser.add_argument("--domain", choices=("both", "main", "ontomed"), default="both")
    parser.add_argument("--pack", default="s1", help="Chemistry MCP pack id: s1, s2, s3, or s4.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    write_expected_manifest()
    if args.action == "pack":
        if args.what in {"all", "pdfs"}:
            pack_pdfs(force=args.force)
        if args.what in {"all", "mcp"}:
            pack_mcp(force=args.force)
        return 0
    if args.action == "unpack":
        missing = False
        if args.what in {"all", "pdfs"}:
            path = pdf_zip_path()
            if path.is_file():
                unpack_zip(path, force=args.force)
            elif args.optional:
                print(f"[WARN] No {path.name}; skip PDF unpack. See docs/ONE_CLICK_RUN.md.")
            else:
                print(f"[FAIL] Missing {path}")
                print("       Copy eval30_pdfs.zip into data/eval_bundles/ first.")
                missing = True
        if args.what in {"all", "mcp"}:
            path = mcp_zip_path()
            if path.is_file():
                unpack_zip(path, force=args.force)
            elif args.optional:
                print(f"[WARN] No {path.name}; skip MCP unpack. See docs/ONE_CLICK_RUN.md.")
            else:
                print(f"[FAIL] Missing {path}")
                print("       Copy locked_mcp_packs.zip into data/eval_bundles/ first.")
                missing = True
        return 1 if missing else 0
    return print_check(check_inputs(cases=args.cases, domain=args.domain, pack=args.pack))


if __name__ == "__main__":
    raise SystemExit(main())
