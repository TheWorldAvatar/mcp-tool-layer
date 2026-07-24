from __future__ import annotations

import difflib
import json
import os
import shlex
import subprocess
from pathlib import Path
from typing import Iterable

from fastmcp import FastMCP


REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUTPUT_ROOT = "ai_generated_contents_agentic_candidate"
DEFAULT_TMP_ROOT = "tmp/agentic_generation"
MAX_COMMAND_SECONDS = 120


class WorkspaceSafetyError(ValueError):
    """Raised when a workspace operation attempts to leave the allowed sandbox."""


def _output_root() -> Path:
    configured = os.environ.get("AGENTIC_GENERATION_OUTPUT_ROOT") or DEFAULT_OUTPUT_ROOT
    if configured.startswith("${") and configured.endswith("}"):
        configured = DEFAULT_OUTPUT_ROOT
    raw = Path(configured)
    if raw.is_absolute():
        return raw.resolve()
    return (REPO_ROOT / configured).resolve()


def _tmp_root() -> Path:
    return (REPO_ROOT / DEFAULT_TMP_ROOT).resolve()


def _allowed_write_roots() -> tuple[Path, ...]:
    roots: list[Path] = [_output_root()]
    roots.extend(
        path.resolve()
        for path in REPO_ROOT.glob("ai_generated_contents_agent*")
        if path.is_dir()
    )
    roots.append(_tmp_root())
    # Semantic MCP closed-loop sandboxes under tmp/.
    roots.extend(
        path.resolve()
        for path in (REPO_ROOT / "tmp").glob("semantic_mcp_loop_*")
        if path.is_dir()
    )
    unique: list[Path] = []
    for root in roots:
        if root not in unique:
            unique.append(root)
    return tuple(unique)


def _repo_path(path: str | Path) -> Path:
    raw = Path(str(path).replace("\\", "/"))
    resolved = raw.resolve() if raw.is_absolute() else (REPO_ROOT / raw).resolve()
    try:
        resolved.relative_to(REPO_ROOT)
    except ValueError as exc:
        raise WorkspaceSafetyError(f"Path is outside repository: {path}") from exc
    return resolved


def _display(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return str(path)


def _error_payload(exc: Exception, **context: object) -> str:
    """Return recoverable tool failures as data instead of aborting ReAct."""
    return json.dumps(
        {
            "ok": False,
            "error_type": type(exc).__name__,
            "error": str(exc),
            **context,
        },
        indent=2,
    )


def _read_path(path: str | Path) -> Path:
    target = _repo_path(path)
    if not target.is_file():
        raise FileNotFoundError(f"File does not exist: {_display(target)}")
    return target


def _write_path(path: str | Path) -> Path:
    target = _repo_path(path)
    for root in _allowed_write_roots():
        try:
            target.relative_to(root)
            return target
        except ValueError:
            continue
    allowed = ", ".join(_display(root) for root in _allowed_write_roots())
    raise WorkspaceSafetyError(
        f"Write denied for {_display(target)}. Allowed roots: {allowed}"
    )


def list_workspace_files(
    relative_path: str = DEFAULT_OUTPUT_ROOT, max_entries: int = 200
) -> str:
    root = _repo_path(relative_path)
    if not root.exists():
        return json.dumps({"ok": True, "root": _display(root), "files": []}, indent=2)
    if not root.is_dir():
        return json.dumps(
            {"ok": False, "error": f"Not a directory: {_display(root)}"}, indent=2
        )
    files = sorted(_display(p) for p in root.rglob("*") if p.is_file())
    return json.dumps(
        {"ok": True, "root": _display(root), "files": files[: max(1, max_entries)]},
        indent=2,
    )


def read_workspace_file(path: str, max_chars: int = 20000) -> str:
    target = _read_path(path)
    text = target.read_text(encoding="utf-8", errors="replace")
    limit = max(1, int(max_chars))
    return json.dumps(
        {
            "ok": True,
            "path": _display(target),
            "truncated": len(text) > limit,
            "content": text[:limit],
        },
        indent=2,
    )


def write_workspace_file(path: str, content: str) -> str:
    target = _write_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return json.dumps(
        {"ok": True, "path": _display(target), "bytes": len(content.encode("utf-8"))},
        indent=2,
    )


def _lines(text: str) -> list[str]:
    return text.splitlines(keepends=True)


def _hunks(patch_text: str) -> Iterable[list[str]]:
    current: list[str] = []
    for line in _lines(patch_text):
        if line.startswith("@@"):
            if current:
                yield current
            current = []
            continue
        if line.startswith(("---", "+++")):
            continue
        if line[:1] in {" ", "-", "+"}:
            current.append(line)
    if current:
        yield current


def apply_unified_patch(path: str, patch_text: str) -> str:
    target = _write_path(path)
    original = target.read_text(encoding="utf-8") if target.exists() else ""
    content = _lines(original)
    applied = 0
    for hunk in _hunks(patch_text):
        old = [line[1:] for line in hunk if line.startswith((" ", "-"))]
        new = [line[1:] for line in hunk if line.startswith((" ", "+"))]
        if not old:
            content.extend(new)
            applied += 1
            continue
        match_at = -1
        for idx in range(0, len(content) - len(old) + 1):
            if content[idx : idx + len(old)] == old:
                match_at = idx
                break
        if match_at < 0:
            raise WorkspaceSafetyError(f"Patch hunk did not match {_display(target)}")
        content[match_at : match_at + len(old)] = new
        applied += 1
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("".join(content), encoding="utf-8")
    return json.dumps(
        {"ok": True, "path": _display(target), "hunks_applied": applied}, indent=2
    )


def show_workspace_diff(path: str) -> str:
    target = _repo_path(path)
    if not target.exists():
        raise FileNotFoundError(f"Path does not exist: {_display(target)}")
    if target.is_dir():
        files = sorted(
            candidate
            for candidate in target.rglob("*")
            if candidate.is_file() and candidate.suffix in {".md", ".py", ".json"}
        )
        return "\n".join(
            show_workspace_diff(_display(candidate)) for candidate in files
        )
    current = _lines(target.read_text(encoding="utf-8", errors="replace"))
    return "".join(
        difflib.unified_diff(
            [],
            current,
            fromfile=f"a/{_display(target)}",
            tofile=f"b/{_display(target)}",
        )
    )


def _split_command(command: str) -> list[str]:
    if not command.strip():
        raise WorkspaceSafetyError("Empty command is not allowed")
    return shlex.split(command, posix=os.name != "nt")


def _is_output_path_arg(part: str) -> bool:
    target = _repo_path(part.strip('"'))
    return any(
        (target == root or target.is_relative_to(root))
        for root in _allowed_write_roots()
    )


def _is_allowed_command(parts: list[str]) -> bool:
    if not parts:
        return False
    executable = Path(parts[0].strip('"')).name.lower()
    if executable not in {"python", "python.exe", "py", "pytest", "pytest.exe"}:
        return False
    if executable.startswith("pytest"):
        return all(
            p.startswith("-") or p.replace("\\", "/").startswith("tests/")
            for p in parts[1:]
        )
    if len(parts) >= 3 and parts[1] == "-m" and parts[2] == "pytest":
        return all(
            p.startswith("-") or p.replace("\\", "/").startswith("tests/")
            for p in parts[3:]
        )
    if len(parts) >= 3 and parts[1] == "-m" and parts[2] == "py_compile":
        return all(_is_output_path_arg(p) for p in parts[3:])
    return False


def _expand_validation_args(parts: list[str]) -> list[str]:
    if not (len(parts) >= 3 and parts[1] == "-m" and parts[2] == "py_compile"):
        return parts
    expanded = parts[:3]
    for part in parts[3:]:
        if any(ch in part for ch in "*?["):
            base = _repo_path(part)
            matches = sorted(base.parent.glob(base.name))
            expanded.extend(_display(match) for match in matches if match.is_file())
        else:
            expanded.append(part)
    return expanded


def run_allowed_validation_command(
    command: str, timeout_seconds: int = MAX_COMMAND_SECONDS
) -> str:
    parts = _split_command(command)
    if not _is_allowed_command(parts):
        allowed = ", ".join(_display(root) + "/" for root in _allowed_write_roots())
        raise WorkspaceSafetyError(
            "Command denied. Allowed commands are pytest under tests/ and py_compile under "
            f"{allowed}."
        )
    expanded_parts = _expand_validation_args(parts)
    try:
        result = subprocess.run(
            expanded_parts,
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=max(1, min(int(timeout_seconds), MAX_COMMAND_SECONDS)),
        )
    except subprocess.TimeoutExpired as exc:
        return json.dumps(
            {
                "ok": False,
                "returncode": None,
                "timeout": True,
                "command": expanded_parts,
                "stdout": (exc.stdout or "")[-12000:]
                if isinstance(exc.stdout, str)
                else "",
                "stderr": (exc.stderr or "")[-12000:]
                if isinstance(exc.stderr, str)
                else "",
                "error": f"Timed out after {exc.timeout} seconds",
            },
            indent=2,
        )
    return json.dumps(
        {
            "ok": result.returncode == 0,
            "returncode": result.returncode,
            "command": expanded_parts,
            "stdout": result.stdout[-12000:],
            "stderr": result.stderr[-12000:],
        },
        indent=2,
    )


mcp = FastMCP(
    name="agentic_generation_workspace",
    instructions=(
        "Use this workspace only for isolated prompt/script generation experiments. "
        "Writes are restricted to agentic generated-content output roots and tmp/agentic_generation/."
    ),
)


@mcp.prompt(name="instruction")
def instruction_prompt() -> str:
    return (
        "Generate and revise files incrementally. Inspect diffs and run allowed validation "
        "commands before reporting completion. Do not write outside the isolated output roots."
    )


@mcp.tool(name="list_workspace_files")
def list_workspace_files_tool(
    relative_path: str = DEFAULT_OUTPUT_ROOT, max_entries: int = 200
) -> str:
    try:
        return list_workspace_files(relative_path, max_entries)
    except (OSError, ValueError) as exc:
        return _error_payload(exc, path=relative_path)


@mcp.tool(name="read_workspace_file")
def read_workspace_file_tool(path: str, max_chars: int = 20000) -> str:
    try:
        return read_workspace_file(path, max_chars)
    except (OSError, ValueError) as exc:
        return _error_payload(exc, path=path)


@mcp.tool(name="write_workspace_file")
def write_workspace_file_tool(path: str, content: str) -> str:
    try:
        return write_workspace_file(path, content)
    except (OSError, ValueError) as exc:
        return _error_payload(exc, path=path)


@mcp.tool(name="apply_unified_patch")
def apply_unified_patch_tool(path: str, patch_text: str) -> str:
    try:
        return apply_unified_patch(path, patch_text)
    except (OSError, ValueError) as exc:
        return _error_payload(exc, path=path)


@mcp.tool(name="show_workspace_diff")
def show_workspace_diff_tool(path: str) -> str:
    try:
        return show_workspace_diff(path)
    except (OSError, ValueError) as exc:
        return _error_payload(exc, path=path)


@mcp.tool(name="run_allowed_validation_command")
def run_allowed_validation_command_tool(
    command: str, timeout_seconds: int = MAX_COMMAND_SECONDS
) -> str:
    try:
        return run_allowed_validation_command(command, timeout_seconds)
    except (OSError, ValueError) as exc:
        return _error_payload(exc, command=command)


if __name__ == "__main__":
    mcp.run(transport="stdio")
