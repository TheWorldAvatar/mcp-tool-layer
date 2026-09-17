"""Pack, unpack, and check eval PDFs plus frozen MCP packages.

Git does not store PDFs, generated MCP packs, or extraction runtimes.
This module builds two local zip files and unpacks them into the paths
the locked one-click runner expects.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path
from typing import Iterable

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


def _posix(path: Path) -> str:
    return path.as_posix()


def _safe_zip_member(name: str) -> Path:
    relative = Path(name.replace("\\", "/"))
    if relative.is_absolute() or ".." in relative.parts:
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
