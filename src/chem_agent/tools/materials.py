"""Materials science: crystal structure databases via OPTIMADE, and the Materials Project API."""

from __future__ import annotations

import math
import os
import re
from functools import reduce
from typing import Any

from .. import http
from .base import ToolError, tool

OPTIMADE_PROVIDERS = {
    "mp": ("Materials Project", "https://optimade.materialsproject.org/v1", "https://next-gen.materialsproject.org/materials/{id}"),
    "oqmd": ("OQMD", "https://oqmd.org/optimade/v1", "https://oqmd.org/optimade/v1/structures/{id}"),
    "cod": ("Crystallography Open Database", "https://www.crystallography.net/cod/optimade/v1", "https://www.crystallography.net/cod/{id}.html"),
}
MP_API = "https://api.materialsproject.org/materials/summary/"


def reduced_formula(formula: str) -> str:
    """Normalize a formula to OPTIMADE's chemical_formula_reduced (alphabetical, gcd-reduced, 1s omitted)."""
    if not re.fullmatch(r"(?:[A-Z][a-z]?\d*)+", formula):
        raise ToolError(f"Use a plain formula without brackets or charges, e.g. 'LiFePO4' (got {formula!r}).")
    counts: dict[str, int] = {}
    for element, n in re.findall(r"([A-Z][a-z]?)(\d*)", formula):
        counts[element] = counts.get(element, 0) + int(n or 1)
    divisor = reduce(math.gcd, counts.values())
    return "".join(f"{el}{'' if c // divisor == 1 else c // divisor}" for el, c in sorted(counts.items()))


@tool(
    "search_crystal_structures",
    "Search open crystal-structure databases through OPTIMADE (no API key): 'mp' Materials Project "
    "(computed), 'oqmd' Open Quantum Materials Database (computed), 'cod' Crystallography Open Database "
    "(experimental). Search by exact formula, or by a set of elements the material must contain.",
    {
        "formula": {"type": "string", "description": "Exact composition, e.g. 'LiFePO4' or 'TiO2'."},
        "elements": {"type": "array", "items": {"type": "string"}, "description": "Elements that must all be present, e.g. ['Li','Co','O']."},
        "exact_element_count": {"type": "boolean", "description": "With 'elements': contain only those elements."},
        "provider": {"type": "string", "enum": list(OPTIMADE_PROVIDERS), "description": "Default 'mp'."},
        "max_results": {"type": "integer", "minimum": 1, "maximum": 50},
    },
)
def search_crystal_structures(formula: str = "", elements: list[str] | None = None, exact_element_count: bool = False,
                              provider: str = "mp", max_results: int = 10) -> dict[str, Any]:
    if formula:
        flt = f'chemical_formula_reduced="{reduced_formula(formula)}"'
    elif elements:
        flt = "elements HAS ALL " + ",".join(f'"{e}"' for e in elements)
        if exact_element_count:
            flt += f" AND nelements={len(elements)}"
    else:
        raise ToolError("Give either 'formula' or 'elements'.")
    name, base, link = OPTIMADE_PROVIDERS[provider]
    data = http.get_json(f"{base}/structures", {
        "filter": flt,
        "page_limit": max_results,
        "response_fields": "chemical_formula_reduced,chemical_formula_descriptive,elements,nelements,nsites,last_modified",
    })
    entries = data.get("data", [])[:max_results]
    if not entries:
        raise ToolError(f"No structures in {name} match {flt}.")
    results = [{"id": e["id"], **(e.get("attributes") or {}), "url": link.format(id=e["id"])} for e in entries]
    return {
        "database": name,
        "filter": flt,
        "total_matches": (data.get("meta") or {}).get("data_returned"),
        "results": results,
    }


@tool(
    "materials_project_properties",
    "Get computed materials properties from the Materials Project (DFT): band gap, energy above hull "
    "(thermodynamic stability), formation energy, crystal system and space group, density, magnetism. "
    "Most stable polymorphs first. Query by formula ('LiFePO4') or chemical system ('Li-Fe-O').",
    {
        "formula": {"type": "string", "description": "e.g. 'LiFePO4'. Comma-separate for several."},
        "chemsys": {"type": "string", "description": "e.g. 'Li-Fe-O': all materials made only of these elements."},
        "max_results": {"type": "integer", "minimum": 1, "maximum": 50},
    },
    requires_env="MP_API_KEY",
)
def materials_project_properties(formula: str = "", chemsys: str = "", max_results: int = 10) -> dict[str, Any]:
    if not (formula or chemsys):
        raise ToolError("Give either 'formula' or 'chemsys'.")
    params: dict[str, Any] = {
        "_fields": "material_id,formula_pretty,symmetry,band_gap,energy_above_hull,formation_energy_per_atom,"
                   "is_stable,is_metal,density,nsites,total_magnetization,theoretical",
        "_sort_fields": "energy_above_hull",
        "_limit": max_results,
    }
    if formula:
        params["formula"] = formula
    else:
        params["chemsys"] = chemsys
    data = http.get_json(MP_API, params, headers={"X-API-KEY": os.environ["MP_API_KEY"]}).get("data", [])
    if not data:
        raise ToolError(f"No Materials Project entries for {formula or chemsys}.")
    for d in data:
        sym = d.pop("symmetry", None) or {}
        d["crystal_system"] = sym.get("crystal_system")
        d["space_group"] = sym.get("symbol")
        d["url"] = f"https://next-gen.materialsproject.org/materials/{d['material_id']}"
    return {"query": formula or chemsys, "units": {"band_gap": "eV", "energies": "eV/atom", "density": "g/cm3"}, "results": data}
