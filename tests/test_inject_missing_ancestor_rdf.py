"""
Regression tests for _inject_missing_ancestor_rdf_types (T-Box-driven superclass rdf:type lines).
"""
from __future__ import annotations

import ast
import textwrap
import unittest
from pathlib import Path


class TestInjectMissingAncestorRdfTypes(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from src.agents.scripts_and_prompts_generation.direct_script_generation import (
            extract_concise_ontology_structure,
        )

        ttl = Path("data/ontologies/ontospecies-subgraph.ttl")
        if not ttl.is_file():
            raise unittest.SkipTest(f"Missing ontology: {ttl}")
        st = extract_concise_ontology_structure(str(ttl), include_om2_mock=True)
        cls._cs = st.get("class_structures") or {}
        if "Device" not in cls._cs or "ElementalAnalysisDevice" not in cls._cs:
            raise unittest.SkipTest("T-Box missing Device / ElementalAnalysisDevice")

    def _closure(self, class_name: str) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        frontier = [class_name]
        for _ in range(20):
            if not frontier:
                break
            cur = frontier.pop()
            for p in (self._cs.get(cur, {}) or {}).get("parent_classes") or []:
                if not p or p in seen:
                    continue
                seen.add(p)
                out.append(p)
                frontier.append(p)
        return out

    def test_injects_ancestor_crlf_with_correct_indent(self) -> None:
        from src.agents.scripts_and_prompts_generation.direct_script_generation import (
            _inject_missing_ancestor_rdf_types,
        )

        body = textwrap.dedent(
            """
            @d
            def create_ElementalAnalysisDevice(label: str) -> str:
                try:
                    with locked_graph() as g:
                        lbl = 'a'
                        rdf_type = NAMESPACE['ElementalAnalysisDevice']
                        iri = _mint_hash_iri('ElementalAnalysisDevice')
                        g.add((iri, RDF.type, rdf_type))
                        _set_single_label(g, iri, lbl)
                        return 'ok'
                except Exception:
                    return 'e'
            """
        ).lstrip()
        # CRLF as on Windows; leave leading "x" line to mirror decorated-module layout
        src = "x=1\r\n" + body.replace("\n", "\r\n")
        cta = {"ElementalAnalysisDevice": self._closure("ElementalAnalysisDevice")}
        known = set(self._cs.keys())
        out = _inject_missing_ancestor_rdf_types(
            src, class_to_ancestors=cta, known_classes=known
        )
        self.assertIn("NAMESPACE['Device']", out)
        for line in out.splitlines(keepends=True):
            if "NAMESPACE['Device']" in line and "g.add" in line:
                self.assertTrue(line.startswith("            "), line)
        ast.parse(out)

    def test_injects_ancestor_lf_only(self) -> None:
        from src.agents.scripts_and_prompts_generation.direct_script_generation import (
            _inject_missing_ancestor_rdf_types,
        )

        src = textwrap.dedent(
            """
            def create_ElementalAnalysisDevice():
                with x:
                    rdf_type = NAMESPACE['ElementalAnalysisDevice']
                    iri = y()
                    g.add((iri, RDF.type, rdf_type))
                    return 0
            """
        ).lstrip()
        cta = {"ElementalAnalysisDevice": self._closure("ElementalAnalysisDevice")}
        out = _inject_missing_ancestor_rdf_types(
            src, class_to_ancestors=cta, known_classes=set(self._cs.keys())
        )
        self.assertIn("NAMESPACE['Device']", out)
        ast.parse(out)

    def test_idempotent_when_ancestor_edge_present(self) -> None:
        from src.agents.scripts_and_prompts_generation.direct_script_generation import (
            _inject_missing_ancestor_rdf_types,
        )

        src = textwrap.dedent(
            """
            def create_ElementalAnalysisDevice():
                with x:
                    iri = y()
                    g.add((iri, RDF.type, rdf_type))
                    g.add((iri, RDF.type, NAMESPACE['Device']))
                    g.add((iri, RDF.type, NAMESPACE['ElementalAnalysisDevice']))
            """
        ).lstrip()
        cta = {"ElementalAnalysisDevice": self._closure("ElementalAnalysisDevice")}
        out = _inject_missing_ancestor_rdf_types(
            src, class_to_ancestors=cta, known_classes=set(self._cs.keys())
        )
        # Must not add a second g.add for Device
        self.assertEqual(out.count("NAMESPACE['Device']"), 1, out)
        ast.parse(out)


if __name__ == "__main__":
    unittest.main()
