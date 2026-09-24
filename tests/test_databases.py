"""Database tools, tested against canned API responses (no network needed)."""

import pytest
import requests

from chem_agent import http
from chem_agent.tools import ToolError
from chem_agent.tools import chembl, literature, materials, pubchem

PUBMED_XML = """<?xml version="1.0"?>
<PubmedArticleSet><PubmedArticle>
  <MedlineCitation><PMID>123</PMID><Article>
    <Journal><JournalIssue><PubDate><Year>2023</Year></PubDate></JournalIssue><Title>J Med Chem</Title></Journal>
    <ArticleTitle>Aspirin <i>analogues</i> as COX inhibitors</ArticleTitle>
    <Abstract><AbstractText Label="BACKGROUND">Why.</AbstractText><AbstractText Label="RESULTS">What.</AbstractText></Abstract>
    <AuthorList><Author><LastName>Curie</LastName><Initials>M</Initials></Author><Author><CollectiveName>COX Group</CollectiveName></Author></AuthorList>
  </Article></MedlineCitation>
  <PubmedData><ArticleIdList><ArticleId IdType="pubmed">123</ArticleId><ArticleId IdType="doi">10.1/abc</ArticleId></ArticleIdList></PubmedData>
</PubmedArticle></PubmedArticleSet>"""

ARXIV_XML = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/2401.00001v1</id>
    <published>2024-01-02T00:00:00Z</published>
    <title>DFT study of
      perovskites</title>
    <summary>We compute band gaps.</summary>
    <author><name>A. Researcher</name></author>
    <arxiv:primary_category term="cond-mat.mtrl-sci"/>
    <arxiv:doi>10.2/xyz</arxiv:doi>
  </entry>
</feed>"""


def test_parse_pubmed():
    [paper] = literature.parse_pubmed_xml(PUBMED_XML)
    assert paper["title"] == "Aspirin analogues as COX inhibitors"
    assert paper["authors"] == "Curie M, COX Group"
    assert paper["abstract"] == "BACKGROUND: Why. RESULTS: What."
    assert paper["doi"] == "10.1/abc" and paper["year"] == "2023"


def test_parse_arxiv():
    [paper] = literature.parse_arxiv(ARXIV_XML)
    assert paper["title"] == "DFT study of perovskites"
    assert paper["arxiv_id"] == "2401.00001v1"
    assert paper["category"] == "cond-mat.mtrl-sci" and paper["doi"] == "10.2/xyz"


def test_parse_europepmc_preprint():
    data = {"resultList": {"result": [{
        "id": "PPR1", "source": "PPR", "title": "A <i>ChemRxiv</i> preprint", "authorString": "Doe J.",
        "pubYear": "2025", "abstractText": "<p>Abstract &amp; more</p>", "isOpenAccess": "Y",
    }]}}
    [paper] = literature.parse_europepmc(data)
    assert paper["title"] == "A ChemRxiv preprint"
    assert paper["journal"] == "Preprint"
    assert paper["abstract"] == "Abstract & more"
    assert paper["url"] == "https://europepmc.org/article/PPR/PPR1"


def test_parse_crossref():
    data = {"message": {"items": [{
        "DOI": "10.1021/jacs.0", "title": ["MOF synthesis"], "author": [{"given": "Omar", "family": "Yaghi"}],
        "container-title": ["JACS"], "issued": {"date-parts": [[2020, 5]]}, "is-referenced-by-count": 42,
        "abstract": "<jats:p>MOFs.</jats:p>",
    }]}}
    [paper] = literature.parse_crossref(data)
    assert paper["authors"] == "Omar Yaghi" and paper["year"] == "2020"
    assert paper["abstract"] == "MOFs." and paper["url"] == "https://doi.org/10.1021/jacs.0"


def test_search_literature_no_results(monkeypatch):
    monkeypatch.setattr(http, "get_json", lambda url, params=None, headers=None: {"message": {"items": []}})
    with pytest.raises(ToolError, match="No results"):
        literature.search_literature("nothing", source="crossref")


def test_pubchem_compound(monkeypatch):
    def fake_post(url, data):
        if url.endswith("/cids/JSON"):
            assert data == {"name": "aspirin"}
            return {"IdentifierList": {"CID": [2244]}}
        return {"PropertyTable": {"Properties": [{"CID": 2244, "MolecularFormula": "C9H8O4"}]}}

    def fake_get(url, params=None, headers=None):
        if "synonyms" in url:
            return {"InformationList": {"Information": [{"Synonym": ["aspirin", "acetylsalicylic acid"]}]}}
        raise requests.HTTPError("no description")

    monkeypatch.setattr(http, "post_json", fake_post)
    monkeypatch.setattr(http, "get_json", fake_get)
    result = pubchem.pubchem_compound("aspirin")
    [compound] = result["compounds"]
    assert compound["synonyms"] == ["aspirin", "acetylsalicylic acid"]
    assert compound["url"].endswith("/2244")


def test_pubchem_not_found(monkeypatch):
    resp = requests.Response()
    resp.status_code = 404

    def fake_post(url, data):
        raise requests.HTTPError(response=resp)

    monkeypatch.setattr(http, "post_json", fake_post)
    with pytest.raises(ToolError, match="No PubChem compound"):
        pubchem.pubchem_compound("unobtainium")


GHS_RECORD = {"Section": [{"Section": [{"Information": [
    {"Name": "Pictogram(s)", "Value": {"StringWithMarkup": [{"String": " ", "Markup": [{"Extra": "Irritant"}]}]}},
    {"Name": "Signal", "Value": {"StringWithMarkup": [{"String": "Warning"}]}},
    {"Name": "GHS Hazard Statements", "Value": {"StringWithMarkup": [
        {"String": "H302 (90%): Harmful if swallowed"}, {"String": "H302 (90%): Harmful if swallowed"}]}},
    {"Name": "Precautionary Statement Codes", "Value": {"StringWithMarkup": [{"String": "P264, P270"}]}},
]}]}]}


def test_parse_ghs_dedupes():
    ghs = pubchem.parse_ghs(GHS_RECORD)
    assert ghs["pictograms"] == ["Irritant"]
    assert ghs["signal_words"] == ["Warning"]
    assert ghs["hazard_statements"] == ["H302 (90%): Harmful if swallowed"]


def test_parse_annotations_with_sources():
    record = {
        "Reference": [{"ReferenceNumber": 7, "SourceName": "HSDB"}],
        "Section": [{"Information": [
            {"ReferenceNumber": 7, "Value": {"StringWithMarkup": [{"String": "135 °C"}]}},
            {"ReferenceNumber": 8, "Value": {"Number": [136], "Unit": "°C"}},
        ]}],
    }
    assert pubchem.parse_annotations(record) == [
        {"value": "135 °C", "source": "HSDB"},
        {"value": "136 °C", "source": ""},
    ]


def test_chembl_activities(monkeypatch):
    seen = {}

    def fake_get(url, params=None, headers=None):
        seen.update(params)
        return {"activities": [{"molecule_chembl_id": "CHEMBL1", "canonical_smiles": "C", "standard_type": "IC50",
                                "standard_value": "5", "standard_units": "nM", "pchembl_value": "8.3"}]}

    monkeypatch.setattr(http, "get_json", fake_get)
    result = chembl.chembl_activities("chembl203", "target")
    assert seen["target_chembl_id"] == "CHEMBL203" and seen["order_by"] == "-pchembl_value"
    assert result["activities"][0]["smiles"] == "C"


@pytest.mark.parametrize("formula, reduced", [
    ("LiFePO4", "FeLiO4P"),
    ("TiO2", "O2Ti"),
    ("Fe2O4", "FeO2"),
    ("NaCl", "ClNa"),
])
def test_reduced_formula(formula, reduced):
    assert materials.reduced_formula(formula) == reduced


def test_reduced_formula_rejects_brackets():
    with pytest.raises(ToolError):
        materials.reduced_formula("Ca(OH)2")


def test_crystal_structure_filter(monkeypatch):
    seen = {}

    def fake_get(url, params=None, headers=None):
        seen["url"], seen["filter"] = url, params["filter"]
        return {"data": [{"id": "mp-19017", "attributes": {"chemical_formula_reduced": "FeLiO4P"}}], "meta": {"data_returned": 1}}

    monkeypatch.setattr(http, "get_json", fake_get)
    result = materials.search_crystal_structures(elements=["Li", "Fe", "O"], exact_element_count=True)
    assert seen["filter"] == 'elements HAS ALL "Li","Fe","O" AND nelements=3'
    assert result["results"][0]["url"] == "https://next-gen.materialsproject.org/materials/mp-19017"
