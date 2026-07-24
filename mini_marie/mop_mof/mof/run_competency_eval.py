"""
Run human-made competency questions from mini_marie/data/CompetencyQs.md
against the OntoMOFs SPARQL endpoint and report pass/fail.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from mini_marie.mop_mof.mof.mof_operations import DEFAULT_SPARQL_ENDPOINT, execute_sparql

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
COMPETENCY_MD = DATA_DIR / "CompetencyQs.md"
RESULTS_JSON = Path(__file__).resolve().parent / "competency_eval_results.json"
RESULTS_MD = Path(__file__).resolve().parent / "competency_eval_results.md"

# Per-question timeout (seconds); heavy joins get more time
DEFAULT_TIMEOUT = 180
HEAVY_TIMEOUT = 600

HEAVY_KEYWORDS = (
    "doesn't run",
    "union",
    "values ?refcode",
    "across literature",
    "optional {",
    "group by",
    "count(distinct",
    "filter exists",
    "minus {",
    "not exists",
)


@dataclass
class CompetencyCase:
    section: str
    number: str
    title: str
    sparql: str
    timeout: int = DEFAULT_TIMEOUT


@dataclass
class CompetencyResult:
    case: CompetencyCase
    status: str  # pass | empty | error | timeout
    row_count: int = 0
    elapsed_ms: int = 0
    error: Optional[str] = None
    sample_rows: List[Dict[str, Any]] = field(default_factory=list)


def parse_competency_md(path: Path) -> List[CompetencyCase]:
    text = path.read_text(encoding="utf-8")
    cases: List[CompetencyCase] = []
    current_section = "main"
    for m in re.finditer(
        r"(?:^|\n)##\s+([^\n]+)\n",
        text,
    ):
        header = m.group(1).strip()
        if header[0].isdigit():
            continue
        if "david" in header.lower():
            current_section = "david"
        elif "more questions" in header.lower():
            current_section = "more"

    for m in re.finditer(
        r"(?:^|\n)##\s+(\d+)\.\s+(.+?)\n+```sparql\n(.*?)```",
        text,
        re.DOTALL | re.IGNORECASE,
    ):
        num, title, sparql = m.group(1), m.group(2).strip(), m.group(3).strip()
        pos = m.start()
        section = "main"
        if re.search(r"David's Competency Questions", text[:pos], re.IGNORECASE):
            section = "david"
        elif re.search(r"# More Questions", text[:pos]):
            section = "more"
        sparql_lc = sparql.lower()
        title_lc = title.lower()
        is_heavy = any(k in title_lc or k in sparql_lc for k in HEAVY_KEYWORDS)
        if sparql_lc.count("union") >= 2 or sparql_lc.count("optional") >= 4:
            is_heavy = True
        timeout = HEAVY_TIMEOUT if is_heavy else DEFAULT_TIMEOUT
        cases.append(
            CompetencyCase(
                section=section,
                number=num,
                title=title,
                sparql=sparql,
                timeout=timeout,
            )
        )
    return cases


def run_case(case: CompetencyCase) -> CompetencyResult:
    started = time.perf_counter()
    try:
        rows = execute_sparql(case.sparql, timeout=case.timeout)
        elapsed = round((time.perf_counter() - started) * 1000)
        if not rows:
            return CompetencyResult(case=case, status="empty", elapsed_ms=elapsed)
        return CompetencyResult(
            case=case,
            status="pass",
            row_count=len(rows),
            elapsed_ms=elapsed,
            sample_rows=rows[:5],
        )
    except Exception as exc:
        elapsed = round((time.perf_counter() - started) * 1000)
        msg = str(exc)
        status = "timeout" if "timed out" in msg.lower() or "timeout" in msg.lower() else "error"
        return CompetencyResult(
            case=case,
            status=status,
            elapsed_ms=elapsed,
            error=msg[:2000],
        )


def _result_dict(r: CompetencyResult) -> Dict[str, Any]:
    return {
        "section": r.case.section,
        "number": r.case.number,
        "title": r.case.title,
        "status": r.status,
        "row_count": r.row_count,
        "elapsed_ms": r.elapsed_ms,
        "timeout_s": r.case.timeout,
        "error": r.error,
        "sample_rows": r.sample_rows,
    }


def _write_reports(results: List[CompetencyResult], *, partial: bool = False) -> None:
    passed = sum(1 for r in results if r.status == "pass")
    empty = sum(1 for r in results if r.status == "empty")
    errors = sum(1 for r in results if r.status == "error")
    timeouts = sum(1 for r in results if r.status == "timeout")
    payload = {
        "endpoint": DEFAULT_SPARQL_ENDPOINT,
        "source": str(COMPETENCY_MD),
        "partial": partial,
        "total": len(results),
        "passed": passed,
        "empty": empty,
        "errors": errors,
        "timeouts": timeouts,
        "results": [_result_dict(r) for r in results],
    }
    RESULTS_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    lines = [
        "# Competency question evaluation (raw SPARQL)",
        "",
        f"Source: `{COMPETENCY_MD.name}`",
        f"Endpoint: `{DEFAULT_SPARQL_ENDPOINT}`",
        f"Status: {'in progress' if partial else 'complete'}",
        "",
        f"| Metric | Count |",
        f"|--------|-------|",
        f"| Total | {len(results)} |",
        f"| Pass | {passed} |",
        f"| Empty | {empty} |",
        f"| Error | {errors} |",
        f"| Timeout | {timeouts} |",
        "",
        "## Results",
        "",
        "| Section | Q | Status | Rows | ms | Timeout | Title |",
        "|---------|---|--------|------|-----|---------|-------|",
    ]
    for r in results:
        title = r.case.title.replace("|", "/")[:60]
        lines.append(
            f"| {r.case.section} | {r.case.number} | {r.status} | {r.row_count} | "
            f"{r.elapsed_ms} | {r.case.timeout}s | {title} |"
        )
    lines.append("")
    lines.append("## Failures and empty (detail)")
    lines.append("")
    for r in results:
        if r.status == "pass":
            continue
        lines.append(f"### [{r.case.section}] Q{r.case.number}: {r.case.title}")
        lines.append(f"- **Status:** {r.status}")
        lines.append(f"- **Rows:** {r.row_count}")
        lines.append(f"- **Elapsed:** {r.elapsed_ms} ms")
        if r.error:
            lines.append(f"- **Error:** `{r.error[:800]}`")
        if r.sample_rows:
            lines.append(f"- **Sample:** `{json.dumps(r.sample_rows[:2], ensure_ascii=False)[:400]}`")
        lines.append("")
    RESULTS_MD.write_text("\n".join(lines), encoding="utf-8")


def _safe_print(msg: str) -> None:
    try:
        print(msg, flush=True)
    except UnicodeEncodeError:
        print(msg.encode(sys.stdout.encoding or "utf-8", errors="replace").decode(
            sys.stdout.encoding or "utf-8", errors="replace"
        ), flush=True)


def _load_resume(cases: List[CompetencyCase]) -> List[CompetencyResult]:
    if not RESULTS_JSON.exists():
        return []
    try:
        payload = json.loads(RESULTS_JSON.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    saved = payload.get("results") or []
    if not saved:
        return []
    by_key = {(c.section, c.number, c.title): c for c in cases}
    resumed: List[CompetencyResult] = []
    for row in saved:
        key = (row.get("section"), row.get("number"), row.get("title"))
        case = by_key.get(key)
        if case is None:
            return []
        resumed.append(
            CompetencyResult(
                case=case,
                status=row.get("status", "error"),
                row_count=int(row.get("row_count") or 0),
                elapsed_ms=int(row.get("elapsed_ms") or 0),
                error=row.get("error"),
                sample_rows=row.get("sample_rows") or [],
            )
        )
    return resumed


def main() -> None:
    parser = argparse.ArgumentParser(description="Run CompetencyQs.md SPARQL against OntoMOFs")
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Continue from competency_eval_results.json if present",
    )
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="Ignore any partial results and run all questions",
    )
    args = parser.parse_args()

    cases = parse_competency_md(COMPETENCY_MD)
    _safe_print(f"Parsed {len(cases)} SPARQL blocks from {COMPETENCY_MD.name}")
    _safe_print(f"Endpoint: {DEFAULT_SPARQL_ENDPOINT}")
    _safe_print(f"Timeouts: default={DEFAULT_TIMEOUT}s heavy={HEAVY_TIMEOUT}s\n")

    results: List[CompetencyResult] = []
    start_idx = 0
    if args.resume and not args.fresh:
        results = _load_resume(cases)
        if results:
            if len(results) > len(cases):
                results = results[: len(cases)]
            start_idx = len(results)
            _safe_print(f"Resuming after {start_idx} completed question(s)\n")

    for i, case in enumerate(cases[start_idx:], start_idx + 1):
        label = f"[{case.section}] Q{case.number}: {case.title[:60]}"
        _safe_print(f"{i}/{len(cases)} {label} (timeout={case.timeout}s) ...")
        result = run_case(case)
        results.append(result)
        _safe_print(f"  -> {result.status} rows={result.row_count} ms={result.elapsed_ms}")
        if result.error:
            _safe_print(f"     {result.error[:200]}")
        _write_reports(results, partial=(i < len(cases)))

    passed = sum(1 for r in results if r.status == "pass")
    empty = sum(1 for r in results if r.status == "empty")
    errors = sum(1 for r in results if r.status == "error")
    timeouts = sum(1 for r in results if r.status == "timeout")

    _write_reports(results, partial=False)
    _safe_print(f"\nWrote {RESULTS_JSON} and {RESULTS_MD}")
    _safe_print(f"Summary: pass={passed} empty={empty} error={errors} timeout={timeouts}")


if __name__ == "__main__":
    main()
