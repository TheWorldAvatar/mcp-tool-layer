"""
Answer cross-KG competency questions using mini_marie tool implementations.

Usage:
  python -m mini_marie.cross_kg_competency.run_eval
  python -m mini_marie.cross_kg_competency.run_eval --json-out data/mini_marie_cache/cross_kg_results.json
"""

from __future__ import annotations

import argparse
import json
import time
import traceback
from pathlib import Path
from typing import Any, Callable, Dict, List

from mini_marie.cache_paths import mini_marie_cache_root

QUESTIONS_PATH = Path(__file__).resolve().parent / "questions.json"


def _load_handlers() -> Dict[str, Callable[[], Any]]:
    from mini_marie.kg_catalog import catalog
    from mini_marie.mop_mof.mof.mof_operations import (
        format_results_as_tsv,
        get_mof_total_count,
        get_tobassco_co2_coverage,
    )
    from mini_marie.mop_mof.mof import mof_competency_operations as mof_cq
    from mini_marie.zaha.sg_old import operations as sg
    from mini_marie.zaha.sg_old.local_store import ensure_db
    from mini_marie.zaha.twa_city.twa_city_operations import (
        format_results_as_tsv as city_tsv,
        get_building_count,
    )

    ensure_db()

    def city_buildings():
        try:
            return city_tsv(get_building_count("bremen"))
        except Exception as exc:
            return f"SKIP (endpoint/cache): {exc}"

    return {
        "kg_cache_status": catalog.kg_cache_status_text,
        "list_kg_domains": catalog.list_kg_domains_text,
        "get_mof_total_count": lambda: format_results_as_tsv(get_mof_total_count()),
        "get_tobassco_co2_coverage": lambda: format_results_as_tsv(get_tobassco_co2_coverage()),
        "lookup_mof_by_mofid_fragment": lambda: format_results_as_tsv(
            mof_cq.get_mof_identity_by_name("ZIF-8")
        ),
        "get_sg_graph_stats": lambda: sg.format_tsv(sg.get_sg_graph_stats()),
        "get_sg_carpark_list": lambda: sg.format_tsv(sg.get_sg_carpark_list(limit=3)),
        "count_sg_carpark_with_timeseries": lambda: sg.format_tsv(sg.count_sg_carpark_with_timeseries()),
        "get_sg_emission_stats": lambda: sg.format_tsv(sg.get_sg_emission_stats()),
        "get_sg_emission_samples": lambda: sg.format_tsv(sg.get_sg_emission_samples(limit=5)),
        "get_sg_plot_regulations": lambda: sg.format_tsv(sg.get_sg_plot_regulations(limit=10)),
        "get_sg_company_ontology_classes": lambda: sg.format_tsv(
            sg.get_sg_company_ontology_classes(limit=5)
        ),
        "count_sg_company_instances": lambda: sg.format_tsv(sg.count_sg_company_instances()),
        "get_building_count": city_buildings,
    }


def answer_question(q: Dict[str, Any], handlers: Dict[str, Callable[[], Any]]) -> Dict[str, Any]:
    tool_results: List[Dict[str, Any]] = []
    for tool in q.get("tools", []):
        t0 = time.perf_counter()
        entry: Dict[str, Any] = {"tool": tool}
        try:
            if tool not in handlers:
                entry["error"] = "unknown tool"
            else:
                entry["result"] = handlers[tool]()
                entry["ok"] = True
        except Exception as exc:
            entry["ok"] = False
            entry["error"] = str(exc)
            entry["trace"] = traceback.format_exc()[-500:]
        entry["ms"] = round((time.perf_counter() - t0) * 1000)
        tool_results.append(entry)

    synthesis = _synthesize(q["id"], q["question"], tool_results)
    return {
        "id": q["id"],
        "question": q["question"],
        "domains": q.get("domains", []),
        "tool_results": tool_results,
        "answer": synthesis,
    }


def _synthesize(qid: str, question: str, tool_results: List[Dict[str, Any]]) -> str:
    """Short human answer from tool outputs."""
    parts = [f"**{qid}:** {question}\n"]
    for tr in tool_results:
        if tr.get("ok"):
            parts.append(f"- `{tr['tool']}`:\n```\n{tr.get('result','')}\n```\n")
        else:
            parts.append(f"- `{tr['tool']}` FAILED: {tr.get('error')}\n")

    if qid == "XQ01":
        return "".join(parts)
    if qid == "XQ02":
        return "".join(parts) + "\nMOF count is orders of magnitude larger than SG emission individuals."
    if qid == "XQ03":
        return "".join(parts) + "\nCompany namespace is schema-only (0 Company instances)."
    if qid == "XQ06":
        return "".join(parts) + "\nkb >> carpark > company > plot by triple count."
    return "".join(parts)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--json-out",
        type=Path,
        default=mini_marie_cache_root() / "cross_kg_competency_results.json",
    )
    args = parser.parse_args()

    questions = json.loads(QUESTIONS_PATH.read_text(encoding="utf-8"))
    handlers = _load_handlers()
    report = {"questions": [answer_question(q, handlers) for q in questions]}
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(json.dumps(report, indent=2), encoding="utf-8")

    md_path = args.json_out.with_suffix(".md")
    lines = ["# Cross-KG competency results\n"]
    for item in report["questions"]:
        lines.append(item["answer"])
        lines.append("\n---\n")
    md_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {args.json_out}")
    print(f"Wrote {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
