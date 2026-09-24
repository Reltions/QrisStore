"""ChEMBL: bioactive molecules, drug targets and measured activities (drug discovery)."""

from __future__ import annotations

from typing import Any

from .. import http
from .base import ToolError, tool

CHEMBL = "https://www.ebi.ac.uk/chembl/api/data"


def _molecule(m: dict[str, Any]) -> dict[str, Any]:
    props = m.get("molecule_properties") or {}
    return {
        "chembl_id": m.get("molecule_chembl_id"),
        "name": m.get("pref_name"),
        "type": m.get("molecule_type"),
        "max_phase": m.get("max_phase"),
        "first_approval": m.get("first_approval"),
        "smiles": (m.get("molecule_structures") or {}).get("canonical_smiles"),
        "mw": props.get("full_mwt"),
        "alogp": props.get("alogp"),
        "qed": props.get("qed_weighted"),
        "ro5_violations": props.get("num_ro5_violations"),
        "url": f"https://www.ebi.ac.uk/chembl/explore/compound/{m.get('molecule_chembl_id')}",
    }


def _target(t: dict[str, Any]) -> dict[str, Any]:
    return {
        "chembl_id": t.get("target_chembl_id"),
        "name": t.get("pref_name"),
        "type": t.get("target_type"),
        "organism": t.get("organism"),
        "url": f"https://www.ebi.ac.uk/chembl/explore/target/{t.get('target_chembl_id')}",
    }


@tool(
    "chembl_search",
    "Search ChEMBL for bioactive molecules (by name/synonym, returns clinical phase, SMILES, properties) or "
    "for drug targets (by protein/gene name, e.g. 'EGFR', 'acetylcholinesterase'). Use the returned CHEMBL "
    "ids with chembl_activities.",
    {
        "query": {"type": "string", "minLength": 1},
        "entity": {"type": "string", "enum": ["molecule", "target"], "description": "Default 'molecule'."},
        "max_results": {"type": "integer", "minimum": 1, "maximum": 25},
    },
    ["query"],
)
def chembl_search(query: str, entity: str = "molecule", max_results: int = 10) -> dict[str, Any]:
    data = http.get_json(f"{CHEMBL}/{entity}/search.json", {"q": query, "limit": max_results})
    items = data.get(f"{entity}s", [])
    if not items:
        raise ToolError(f"No ChEMBL {entity}s match {query!r}.")
    shape = _molecule if entity == "molecule" else _target
    return {"query": query, "entity": entity, "results": [shape(i) for i in items]}


@tool(
    "chembl_activities",
    "Get measured bioactivities from ChEMBL, strongest first (by pChEMBL value): for a molecule (which "
    "targets it hits) or for a target (the most potent known ligands, with SMILES). Values are "
    "IC50/Ki/Kd/EC50 etc. with units, assay description and year.",
    {
        "chembl_id": {"type": "string", "minLength": 1, "description": "e.g. CHEMBL25 (molecule) or CHEMBL203 (target)."},
        "id_kind": {"type": "string", "enum": ["molecule", "target"]},
        "activity_types": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Filter, e.g. ['IC50', 'Ki']. Default: IC50, Ki, Kd, EC50.",
        },
        "max_results": {"type": "integer", "minimum": 1, "maximum": 50},
    },
    ["chembl_id", "id_kind"],
)
def chembl_activities(chembl_id: str, id_kind: str, activity_types: list[str] | None = None,
                      max_results: int = 20) -> dict[str, Any]:
    params = {
        f"{id_kind}_chembl_id": chembl_id.upper(),
        "standard_type__in": ",".join(activity_types or ["IC50", "Ki", "Kd", "EC50"]),
        "pchembl_value__isnull": "false",
        "order_by": "-pchembl_value",
        "limit": max_results,
    }
    acts = http.get_json(f"{CHEMBL}/activity.json", params).get("activities", [])
    if not acts:
        raise ToolError(f"No ChEMBL activities with pChEMBL values for {id_kind} {chembl_id}.")
    results = [{
        "molecule": a.get("molecule_chembl_id"),
        "molecule_name": a.get("molecule_pref_name"),
        "smiles": a.get("canonical_smiles") if id_kind == "target" else None,
        "target": a.get("target_chembl_id"),
        "target_name": a.get("target_pref_name"),
        "organism": a.get("target_organism"),
        "type": a.get("standard_type"),
        "relation": a.get("standard_relation"),
        "value": a.get("standard_value"),
        "units": a.get("standard_units"),
        "pchembl": a.get("pchembl_value"),
        "assay": a.get("assay_description"),
        "year": a.get("document_year"),
    } for a in acts]
    return {"chembl_id": chembl_id, "id_kind": id_kind, "activities": results}
