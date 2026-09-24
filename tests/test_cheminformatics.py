import pytest

from chem_agent.tools import ToolContext, ToolError
from chem_agent.tools.cheminformatics import (
    draw_molecule,
    molecule_properties,
    run_reaction,
    similarity_search,
    substructure_search,
)

ASPIRIN = "CC(=O)OC1=CC=CC=C1C(=O)O"


def test_aspirin_properties():
    [props] = molecule_properties([ASPIRIN])
    assert props["formula"] == "C9H8O4"
    assert props["molecular_weight"] == pytest.approx(180.16, abs=0.01)
    assert props["inchikey"] == "BSYNRYMUTXBXSQ-UHFFFAOYSA-N"
    assert props["lipinski_violations"] == 0
    assert props["h_bond_donors"] == 1
    assert 0 < props["qed"] < 1


def test_invalid_smiles_is_reported_per_molecule():
    results = molecule_properties(["CCO", "not-a-smiles"])
    assert results[0]["formula"] == "C2H6O"
    assert results[1]["error"] == "invalid SMILES"


def test_similarity_ranks_closest_first():
    result = similarity_search(ASPIRIN, ["CCCCCC", "OC(=O)C1=CC=CC=C1O", ASPIRIN, "xx"])
    ranked = [r["smiles"] for r in result["ranked"]]
    assert ranked[0] == ASPIRIN and result["ranked"][0]["tanimoto"] == 1.0
    assert ranked[1] == "OC(=O)C1=CC=CC=C1O"  # salicylic acid
    assert result["invalid"] == ["xx"]


def test_similarity_rejects_bad_query():
    with pytest.raises(ToolError):
        similarity_search("((", ["CCO"])


def test_substructure_carboxylic_acid():
    result = substructure_search("[CX3](=O)[OX2H1]", [ASPIRIN, "CCO"])
    assert [r["match"] for r in result["results"]] == [True, False]


def test_amide_coupling_reaction():
    result = run_reaction("[C:1](=O)[OH].[N!H0:2]>>[C:1](=O)[N:2]", ["CC(=O)O", "NCc1ccccc1"])
    assert result["products"] == [["CC(=O)NCc1ccccc1"]]


def test_reaction_reactant_count_mismatch():
    with pytest.raises(ToolError, match="expects 2"):
        run_reaction("[C:1](=O)[OH].[N!H0:2]>>[C:1](=O)[N:2]", ["CC(=O)O"])


def test_draw_molecule_writes_svg(tmp_path):
    result = draw_molecule(ASPIRIN, ctx=ToolContext(output_dir=tmp_path), name="Aspirin")
    assert result["file"] == "aspirin.svg"
    assert (tmp_path / "aspirin.svg").read_text().startswith("<?xml")
