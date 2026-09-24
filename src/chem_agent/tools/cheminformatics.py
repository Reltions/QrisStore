"""Local cheminformatics with RDKit: properties, similarity, substructures, reactions, drawings."""

from __future__ import annotations

import re
from typing import Any

from rdkit import Chem, DataStructs, RDLogger
from rdkit.Chem import AllChem, Crippen, Descriptors, QED, rdMolDescriptors
from rdkit.Chem.Draw import rdMolDraw2D

from .base import ToolContext, ToolError, tool

RDLogger.DisableLog("rdApp.*")

MAX_MOLECULES = 200


def parse_smiles(smiles: str) -> Chem.Mol:
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        raise ToolError(f"Could not parse SMILES: {smiles!r}")
    return mol


def _properties(mol: Chem.Mol) -> dict[str, Any]:
    mw = Descriptors.MolWt(mol)
    logp = Crippen.MolLogP(mol)
    hbd = rdMolDescriptors.CalcNumHBD(mol)
    hba = rdMolDescriptors.CalcNumHBA(mol)
    tpsa = rdMolDescriptors.CalcTPSA(mol)
    rot = rdMolDescriptors.CalcNumRotatableBonds(mol)
    lipinski_violations = sum([mw > 500, logp > 5, hbd > 5, hba > 10])
    try:
        qed = round(QED.qed(mol), 3)
    except Exception:  # QED fails on some inorganic/organometallic species
        qed = None
    return {
        "canonical_smiles": Chem.MolToSmiles(mol),
        "formula": rdMolDescriptors.CalcMolFormula(mol),
        "molecular_weight": round(mw, 3),
        "exact_mass": round(Descriptors.ExactMolWt(mol), 4),
        "logp_crippen": round(logp, 2),
        "tpsa": round(tpsa, 2),
        "h_bond_donors": hbd,
        "h_bond_acceptors": hba,
        "rotatable_bonds": rot,
        "heavy_atoms": mol.GetNumHeavyAtoms(),
        "rings": rdMolDescriptors.CalcNumRings(mol),
        "aromatic_rings": rdMolDescriptors.CalcNumAromaticRings(mol),
        "fraction_csp3": round(rdMolDescriptors.CalcFractionCSP3(mol), 3),
        "formal_charge": Chem.GetFormalCharge(mol),
        "stereocenters": len(Chem.FindMolChiralCenters(mol, includeUnassigned=True, useLegacyImplementation=False)),
        "qed": qed,
        "lipinski_violations": lipinski_violations,
        "veber_ok": rot <= 10 and tpsa <= 140,
        "inchi": Chem.MolToInchi(mol),
        "inchikey": Chem.MolToInchiKey(mol),
    }


@tool(
    "molecule_properties",
    "Compute properties of molecules from SMILES with RDKit (computed, not experimental): formula, "
    "molecular weight, exact mass, Crippen logP, TPSA, H-bond donors/acceptors, rotatable bonds, rings, "
    "fraction sp3, stereocenters, QED drug-likeness, Lipinski rule-of-5 violations, Veber rule, InChI and "
    "InChIKey. Accepts a batch so you can compare series of compounds in one call.",
    {
        "smiles": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
            "maxItems": MAX_MOLECULES,
            "description": "One or more SMILES strings.",
        }
    },
    ["smiles"],
)
def molecule_properties(smiles: list[str]) -> list[dict[str, Any]]:
    results = []
    for s in smiles:
        mol = Chem.MolFromSmiles(s)
        if mol is None:
            results.append({"input": s, "error": "invalid SMILES"})
        else:
            results.append({"input": s, **_properties(mol)})
    return results


@tool(
    "similarity_search",
    "Rank candidate molecules by Tanimoto similarity to a query molecule using Morgan (ECFP-like) "
    "fingerprints. Use to find close analogues or cluster a series.",
    {
        "query_smiles": {"type": "string", "minLength": 1},
        "candidate_smiles": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": MAX_MOLECULES},
        "radius": {"type": "integer", "minimum": 1, "maximum": 4, "description": "Morgan radius; 2 ~ ECFP4 (default)."},
    },
    ["query_smiles", "candidate_smiles"],
)
def similarity_search(query_smiles: str, candidate_smiles: list[str], radius: int = 2) -> dict[str, Any]:
    gen = AllChem.GetMorganGenerator(radius=radius, fpSize=2048)
    query_fp = gen.GetFingerprint(parse_smiles(query_smiles))
    ranked, invalid = [], []
    for s in candidate_smiles:
        mol = Chem.MolFromSmiles(s)
        if mol is None:
            invalid.append(s)
            continue
        sim = DataStructs.TanimotoSimilarity(query_fp, gen.GetFingerprint(mol))
        ranked.append({"smiles": s, "tanimoto": round(sim, 4)})
    ranked.sort(key=lambda r: r["tanimoto"], reverse=True)
    return {"query": query_smiles, "fingerprint": f"Morgan r={radius}, 2048 bits", "ranked": ranked, "invalid": invalid}


@tool(
    "substructure_search",
    "Check which molecules contain a substructure. The pattern is SMARTS (e.g. 'c1ccccc1' benzene, "
    "'[CX3](=O)[OX2H1]' carboxylic acid, '[NX3][CX3](=O)' amide); plain SMILES also works.",
    {
        "pattern": {"type": "string", "minLength": 1, "description": "SMARTS or SMILES pattern."},
        "smiles": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": MAX_MOLECULES},
    },
    ["pattern", "smiles"],
)
def substructure_search(pattern: str, smiles: list[str]) -> dict[str, Any]:
    query = Chem.MolFromSmarts(pattern) or Chem.MolFromSmiles(pattern)
    if query is None:
        raise ToolError(f"Could not parse pattern as SMARTS or SMILES: {pattern!r}")
    results = []
    for s in smiles:
        mol = Chem.MolFromSmiles(s)
        if mol is None:
            results.append({"smiles": s, "error": "invalid SMILES"})
            continue
        matches = mol.GetSubstructMatches(query)
        results.append({"smiles": s, "match": bool(matches), "match_count": len(matches)})
    return {"pattern": pattern, "results": results}


@tool(
    "run_reaction",
    "Apply a reaction template (reaction SMARTS) to reactants and return the unique sanitized products. "
    "Useful for enumerating products of a proposed synthetic step, e.g. amide coupling "
    "'[C:1](=O)[OH].[N!H0:2]>>[C:1](=O)[N:2]'. Reactants must be given in template order.",
    {
        "reaction_smarts": {"type": "string", "minLength": 1},
        "reactants": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 5},
    },
    ["reaction_smarts", "reactants"],
)
def run_reaction(reaction_smarts: str, reactants: list[str]) -> dict[str, Any]:
    try:
        rxn = AllChem.ReactionFromSmarts(reaction_smarts)
    except Exception as exc:
        raise ToolError(f"Invalid reaction SMARTS: {exc}") from exc
    if rxn.GetNumReactantTemplates() != len(reactants):
        raise ToolError(
            f"Template expects {rxn.GetNumReactantTemplates()} reactant(s), got {len(reactants)}."
        )
    mols = tuple(parse_smiles(s) for s in reactants)
    products: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()
    for product_set in rxn.RunReactants(mols):
        smiles_set = []
        try:
            for p in product_set:
                Chem.SanitizeMol(p)
                smiles_set.append(Chem.MolToSmiles(p))
        except Exception:
            continue  # chemically invalid product from an over-general template
        key = tuple(smiles_set)
        if key not in seen:
            seen.add(key)
            products.append(smiles_set)
    return {"reaction": reaction_smarts, "reactants": reactants, "products": products}


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()[:60] or "molecule"


@tool(
    "draw_molecule",
    "Draw a 2D structure depiction of a molecule and save it as an SVG in the reports folder. Returns the "
    "file name so a saved report can embed it with ![name](file.svg).",
    {
        "smiles": {"type": "string", "minLength": 1},
        "name": {"type": "string", "description": "Label under the drawing and basis for the file name."},
    },
    ["smiles"],
)
def draw_molecule(smiles: str, ctx: ToolContext, name: str = "") -> dict[str, Any]:
    mol = parse_smiles(smiles)
    AllChem.Compute2DCoords(mol)
    drawer = rdMolDraw2D.MolDraw2DSVG(400, 320)
    drawer.DrawMolecule(mol, legend=name)
    drawer.FinishDrawing()
    ctx.output_dir.mkdir(parents=True, exist_ok=True)
    path = ctx.output_dir / f"{_slug(name or Chem.MolToSmiles(mol))}.svg"
    path.write_text(drawer.GetDrawingText(), encoding="utf-8")
    return {"file": path.name, "path": str(path)}
