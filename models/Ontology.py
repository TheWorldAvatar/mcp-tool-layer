import logging
import re
from pathlib import Path
from typing import Dict, List, Literal, Optional, Union

from fastmcp import FastMCP
from pydantic import BaseModel, Field, field_validator

try:
    from rdflib import (
        BNode,
        Graph,
        Literal as RDFLiteral,  # avoid clash with typing.Literal
        Namespace,
        OWL,
        RDF,
        RDFS,
        URIRef,
        XSD,
    )
except ImportError as exc:
    raise ImportError("rdflib is required.  pip install rdflib") from exc

# ── logging ------------------------------------------------------------------
log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# ── prefix helper ------------------------------------------------------------

NCNAME_BAD = re.compile(r"[^A-Za-z0-9_-]+")


def slug(text: str) -> str:
    """RDFlib-safe prefix: replace bad chars with '_' (never empty)."""
    return (NCNAME_BAD.sub("_", text).strip("_") or "base").lower()


# ── Pydantic models ----------------------------------------------------------

class ClassInput(BaseModel):
    name: str
    label: Optional[str] = None
    comment: Optional[str] = None
    subclass_of: List[str] = Field(default_factory=list, alias="subClassOf")
    equivalent_to: List[str] = Field(default_factory=list, alias="equivalentClass")
    disjoint_with: List[str] = Field(default_factory=list, alias="disjointWith")
    annotations: Dict[str, str] = Field(default_factory=dict)


class PropertyInput(BaseModel):
    name: str
    property_type: Literal["object", "datatype"] = "object"
    label: Optional[str] = None
    comment: Optional[str] = None
    domain: Optional[str] = None
    range: Optional[str] = None
    inverse_of: Optional[str] = None

    functional: bool = False
    symmetric: bool = False
    transitive: bool = False
    annotations: Dict[str, str] = Field(default_factory=dict)


class CardinalityInput(BaseModel):
    class_name: str
    property_name: str
    min: Optional[int] = None
    max: Optional[int] = None
    exact: Optional[int] = None

    @field_validator("exact")
    @classmethod
    def _exclusive(cls, v, values):
        if v is not None and (values.data.get("min") or values.data.get("max")):
            raise ValueError("exact cannot be combined with min or max")
        return v


class OntologyInput(BaseModel):
    name: str
    description: str
    base_uri: str = "http://example.org/ontology"
    version: Optional[str] = None
    prefixes: Dict[str, str] = Field(default_factory=dict)
    imports: List[str] = Field(default_factory=list)

    classes: List[ClassInput] = Field(default_factory=list)
    properties: List[PropertyInput] = Field(default_factory=list)
    cardinalities: List[CardinalityInput] = Field(default_factory=list)

# ── Ontology builder ---------------------------------------------------------

class OntologyBuilder:
    """Turn OntologyInput → rdflib.Graph (OWL)"""

    def __init__(self, spec: OntologyInput):
        self.spec = spec
        self.ns_base = Namespace(f"{spec.base_uri.rstrip('#/')}#")
        self.main_prefix = slug(spec.name)

        g = self.g = Graph()
        g.bind("rdf", RDF)
        g.bind("rdfs", RDFS)
        g.bind("owl", OWL)
        g.bind("xsd", XSD)
        g.bind(self.main_prefix, self.ns_base)

        # user prefixes (auto-slugify if illegal)
        for raw, iri in spec.prefixes.items():
            g.bind(slug(raw), Namespace(iri))

    # ── helpers --------------------------------------------------------------

    def _uri(self, local: str) -> URIRef:
        if ":" in local:
            if local.startswith(("http://", "https://")):
                return URIRef(local)
            return URIRef(local)  # assume CURIE already valid
        return URIRef(f"{self.ns_base}{local}")

    # ── build graph ---------------------------------------------------------

    def build(self) -> Graph:
        self._header()
        self._classes()
        self._properties()
        self._cardinalities()
        return self.g

    def _header(self):
        onto = self._uri("")
        self.g.add((onto, RDF.type, OWL.Ontology))
        self.g.add((onto, RDFS.comment, RDFLiteral(self.spec.description)))
        if self.spec.version:
            self.g.add((onto, OWL.versionInfo, RDFLiteral(self.spec.version)))
            self.g.add((onto, OWL.versionIRI, URIRef(f"{self.ns_base}{self.spec.version}")))
        for imp in self.spec.imports:
            self.g.add((onto, OWL.imports, URIRef(imp)))

    def _classes(self):
        for c in self.spec.classes:
            uri = self._uri(c.name)
            self.g.add((uri, RDF.type, OWL.Class))
            self.g.add((uri, RDFS.label, RDFLiteral(c.label or c.name)))
            if c.comment:
                self.g.add((uri, RDFS.comment, RDFLiteral(c.comment)))
            for parent in c.subclass_of:
                self.g.add((uri, RDFS.subClassOf, self._uri(parent)))
            for eq in c.equivalent_to:
                self.g.add((uri, OWL.equivalentClass, self._uri(eq)))
            for dj in c.disjoint_with:
                self.g.add((uri, OWL.disjointWith, self._uri(dj)))
            for p, v in c.annotations.items():
                self.g.add((uri, self._uri(p), RDFLiteral(v)))

    def _properties(self):
        for p in self.spec.properties:
            uri = self._uri(p.name)
            ptype = OWL.ObjectProperty if p.property_type == "object" else OWL.DatatypeProperty
            self.g.add((uri, RDF.type, ptype))
            self.g.add((uri, RDFS.label, RDFLiteral(p.label or p.name)))
            if p.comment:
                self.g.add((uri, RDFS.comment, RDFLiteral(p.comment)))
            if p.domain:
                self.g.add((uri, RDFS.domain, self._uri(p.domain)))
            if p.range:
                self.g.add((uri, RDFS.range, self._uri(p.range)))
            if p.inverse_of:
                self.g.add((uri, OWL.inverseOf, self._uri(p.inverse_of)))
            if p.functional:
                self.g.add((uri, RDF.type, OWL.FunctionalProperty))
            if p.symmetric:
                self.g.add((uri, RDF.type, OWL.SymmetricProperty))
            if p.transitive:
                self.g.add((uri, RDF.type, OWL.TransitiveProperty))
            for ann, val in p.annotations.items():
                self.g.add((uri, self._uri(ann), RDFLiteral(val)))

    def _cardinalities(self):
        for c in self.spec.cardinalities:
            cls_uri = self._uri(c.class_name)
            prop_uri = self._uri(c.property_name)
            b = BNode()
            self.g.add((b, RDF.type, OWL.Restriction))
            self.g.add((b, OWL.onProperty, prop_uri))
            if c.exact is not None:
                self.g.add((b, OWL.cardinality, RDFLiteral(c.exact, datatype=XSD.nonNegativeInteger)))
            else:
                if c.min is not None:
                    self.g.add((b, OWL.minCardinality, RDFLiteral(c.min, datatype=XSD.nonNegativeInteger)))
                if c.max is not None:
                    self.g.add((b, OWL.maxCardinality, RDFLiteral(c.max, datatype=XSD.nonNegativeInteger)))
            self.g.add((cls_uri, RDFS.subClassOf, b))


