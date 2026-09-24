"""PubChem: compound identity and properties, GHS safety data and experimental annotations."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import requests

from .. import http
from .base import ToolError, tool

PUG = "https://pubchem.ncbi.nlm.nih.gov/rest/pug"
PUG_VIEW = "https://pubchem.ncbi.nlm.nih.gov/rest/pug_view"

PROPERTIES = ",".join([
    "MolecularFormula", "MolecularWeight", "SMILES", "ConnectivitySMILES", "InChI", "InChIKey", "IUPACName",
    "XLogP", "TPSA", "HBondDonorCount", "HBondAcceptorCount", "RotatableBondCount", "ExactMass", "Charge",
    "Complexity", "HeavyAtomCount", "DefinedAtomStereoCount",
])
NAMESPACES = ["name", "smiles", "cid", "inchikey", "formula"]
ANNOTATION_HEADINGS = [
    "Melting Point", "Boiling Point", "Solubility", "Density", "Vapor Pressure", "LogP",
    "Dissociation Constants", "Flash Point", "Decomposition", "Stability/Shelf Life", "Odor", "Color/Form",
    "Toxicity Summary", "Drug Indication", "Mechanism of Action", "Use and Manufacturing",
]

IDENTIFIER_PROPS = {
    "identifier": {"type": "string", "minLength": 1, "description": "Compound name, SMILES, CID, InChIKey or formula."},
    "namespace": {"type": "string", "enum": NAMESPACES, "description": "What the identifier is. Default 'name'."},
}


def _not_found(exc: requests.HTTPError) -> bool:
    return exc.response is not None and exc.response.status_code == 404


def _cids(identifier: str, namespace: str) -> list[int]:
    if namespace == "cid":
        try:
            return [int(identifier)]
        except ValueError as exc:
            raise ToolError(f"CID must be an integer, got {identifier!r}") from exc
    try:
        if namespace == "formula":
            data = http.get_json(f"{PUG}/compound/fastformula/{quote(identifier)}/cids/JSON", {"MaxRecords": 10})
        else:
            data = http.post_json(f"{PUG}/compound/{namespace}/cids/JSON", {namespace: identifier})
    except requests.HTTPError as exc:
        if _not_found(exc):
            raise ToolError(f"No PubChem compound found for {namespace} {identifier!r}.") from exc
        raise
    cids = [c for c in data.get("IdentifierList", {}).get("CID", []) if c]
    if not cids:
        raise ToolError(f"No PubChem compound found for {namespace} {identifier!r}.")
    return cids


@tool(
    "pubchem_compound",
    "Look up a compound in PubChem: CID, IUPAC name, formula, molecular weight, SMILES, InChI/InChIKey, "
    "XLogP, TPSA, H-bond counts, synonyms and a short description. The fastest way to turn a compound name "
    "into a SMILES for the RDKit tools. Formula searches return up to 5 matching compounds.",
    IDENTIFIER_PROPS,
    ["identifier"],
)
def pubchem_compound(identifier: str, namespace: str = "name") -> dict[str, Any]:
    cids = _cids(identifier, namespace)[:5]
    data = http.post_json(f"{PUG}/compound/cid/property/{PROPERTIES}/JSON", {"cid": ",".join(map(str, cids))})
    compounds = data.get("PropertyTable", {}).get("Properties", [])
    for c in compounds:
        c["url"] = f"https://pubchem.ncbi.nlm.nih.gov/compound/{c['CID']}"
    first = compounds[0]["CID"]
    try:
        syn = http.get_json(f"{PUG}/compound/cid/{first}/synonyms/JSON")
        compounds[0]["synonyms"] = syn["InformationList"]["Information"][0].get("Synonym", [])[:15]
    except (requests.RequestException, KeyError, IndexError):
        pass
    try:
        desc = http.get_json(f"{PUG}/compound/cid/{first}/description/JSON")
        compounds[0]["descriptions"] = [
            {"text": d["Description"], "source": d.get("DescriptionSourceName", "")}
            for d in desc["InformationList"]["Information"] if "Description" in d
        ][:2]
    except (requests.RequestException, KeyError):
        pass
    return {"query": identifier, "namespace": namespace, "compounds": compounds}


def _walk_information(sections: list[dict[str, Any]]):
    for section in sections:
        yield from section.get("Information", [])
        yield from _walk_information(section.get("Section", []))


def _strings(info: dict[str, Any]) -> list[str]:
    value = info.get("Value", {})
    out = [s.get("String", "") for s in value.get("StringWithMarkup", [])]
    if "Number" in value:
        out.append(" ".join(str(n) for n in value["Number"]) + (f" {value['Unit']}" if value.get("Unit") else ""))
    return [s for s in out if s]


def _dedupe(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


def _pug_view(cid: int, heading: str) -> dict[str, Any]:
    try:
        return http.get_json(f"{PUG_VIEW}/data/compound/{cid}/JSON", {"heading": heading})["Record"]
    except requests.HTTPError as exc:
        if _not_found(exc):
            raise ToolError(f"PubChem has no '{heading}' data for CID {cid}.") from exc
        raise


def parse_ghs(record: dict[str, Any]) -> dict[str, Any]:
    pictograms, signals, hazards, precautions = [], [], [], []
    for info in _walk_information(record.get("Section", [])):
        name = info.get("Name")
        if name == "Pictogram(s)":
            for s in info.get("Value", {}).get("StringWithMarkup", []):
                pictograms += [m["Extra"] for m in s.get("Markup", []) if m.get("Extra")]
        elif name == "Signal":
            signals += _strings(info)
        elif name == "GHS Hazard Statements":
            hazards += _strings(info)
        elif name == "Precautionary Statement Codes":
            precautions += _strings(info)
    return {
        "pictograms": _dedupe(pictograms),
        "signal_words": _dedupe(signals),
        "hazard_statements": _dedupe(hazards)[:40],
        "precautionary_codes": _dedupe(precautions)[:5],
    }


@tool(
    "pubchem_safety",
    "Get the GHS hazard classification of a compound from PubChem: pictograms, signal word, H-statements "
    "(with the % of notifying sources) and precautionary codes. Use for any safety or handling question.",
    IDENTIFIER_PROPS,
    ["identifier"],
)
def pubchem_safety(identifier: str, namespace: str = "name") -> dict[str, Any]:
    cid = _cids(identifier, namespace)[0]
    ghs = parse_ghs(_pug_view(cid, "GHS Classification"))
    return {"cid": cid, "url": f"https://pubchem.ncbi.nlm.nih.gov/compound/{cid}#section=Safety-and-Hazards", **ghs}


def parse_annotations(record: dict[str, Any], limit: int = 25) -> list[dict[str, str]]:
    sources = {r.get("ReferenceNumber"): r.get("SourceName", "") for r in record.get("Reference", [])}
    values = []
    for info in _walk_information(record.get("Section", [])):
        for text in _strings(info):
            values.append({"value": text, "source": sources.get(info.get("ReferenceNumber"), "")})
    unique = list({(v["value"], v["source"]): v for v in values}.values())
    return unique[:limit]


@tool(
    "pubchem_experimental_data",
    "Get curated experimental or descriptive annotations for a compound from PubChem (with the source "
    "database for each value), e.g. melting/boiling point, solubility, density, pKa ('Dissociation "
    "Constants'), flash point, stability, toxicity summary, drug indication, mechanism of action.",
    {**IDENTIFIER_PROPS, "heading": {"type": "string", "enum": ANNOTATION_HEADINGS}},
    ["identifier", "heading"],
)
def pubchem_experimental_data(identifier: str, heading: str, namespace: str = "name") -> dict[str, Any]:
    cid = _cids(identifier, namespace)[0]
    values = parse_annotations(_pug_view(cid, heading))
    if not values:
        raise ToolError(f"PubChem has no '{heading}' values for CID {cid}.")
    return {"cid": cid, "heading": heading, "url": f"https://pubchem.ncbi.nlm.nih.gov/compound/{cid}", "values": values}
