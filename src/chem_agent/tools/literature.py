"""Scientific literature search: PubMed, Europe PMC (incl. ChemRxiv preprints), arXiv and Crossref."""

from __future__ import annotations

import html
import os
import re
import xml.etree.ElementTree as ET
from typing import Any

from .. import http
from .base import ToolError, tool

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
EUROPEPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
ARXIV = "https://export.arxiv.org/api/query"
CROSSREF = "https://api.crossref.org/works"
OPENALEX = "https://api.openalex.org/works"
EUROPEPMC_FULLTEXT = "https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}/fullTextXML"

ATOM = "{http://www.w3.org/2005/Atom}"
ARXIV_NS = "{http://arxiv.org/schemas/atom}"

MAX_AUTHORS = 6


def _clean(text: str | None) -> str:
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def _authors(names: list[str]) -> str:
    names = [n for n in names if n]
    if len(names) > MAX_AUTHORS:
        return ", ".join(names[:MAX_AUTHORS]) + " et al."
    return ", ".join(names)


def _text(elem: ET.Element | None) -> str:
    return _clean("".join(elem.itertext())) if elem is not None else ""


def parse_pubmed_xml(xml: str) -> list[dict[str, Any]]:
    root = ET.fromstring(xml)
    papers = []
    for art in root.iter("PubmedArticle"):
        cit = art.find("MedlineCitation")
        article = cit.find("Article")
        pmid = cit.findtext("PMID", "")
        abstract_parts = []
        for part in article.findall("Abstract/AbstractText"):
            label = part.get("Label")
            body = _text(part)
            abstract_parts.append(f"{label}: {body}" if label else body)
        authors = []
        for a in article.findall("AuthorList/Author"):
            if a.findtext("CollectiveName"):
                authors.append(a.findtext("CollectiveName"))
            else:
                authors.append(f"{a.findtext('LastName', '')} {a.findtext('Initials', '')}".strip())
        pub_date = article.find("Journal/JournalIssue/PubDate")
        year = ""
        if pub_date is not None:
            year = pub_date.findtext("Year") or pub_date.findtext("MedlineDate", "")[:4]
        doi = pmcid = ""
        for aid in art.findall("PubmedData/ArticleIdList/ArticleId"):
            if aid.get("IdType") == "doi":
                doi = aid.text or ""
            elif aid.get("IdType") == "pmc":
                pmcid = aid.text or ""
        papers.append({
            "title": _text(article.find("ArticleTitle")),
            "authors": _authors(authors),
            "journal": article.findtext("Journal/Title", ""),
            "year": year,
            "doi": doi,
            "pmid": pmid,
            "pmcid": pmcid,
            "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
            "abstract": " ".join(abstract_parts),
        })
    return papers


def _search_pubmed(query: str, max_results: int, year_from: int | None) -> list[dict[str, Any]]:
    term = f'({query}) AND ("{year_from}"[dp] : "3000"[dp])' if year_from else query
    params: dict[str, Any] = {"db": "pubmed", "term": term, "retmax": max_results, "retmode": "json", "sort": "relevance"}
    if os.environ.get("NCBI_API_KEY"):
        params["api_key"] = os.environ["NCBI_API_KEY"]
    ids = http.get_json(f"{EUTILS}/esearch.fcgi", params)["esearchresult"].get("idlist", [])
    if not ids:
        return []
    fetch_params = {"db": "pubmed", "id": ",".join(ids), "retmode": "xml", "rettype": "abstract"}
    if "api_key" in params:
        fetch_params["api_key"] = params["api_key"]
    return parse_pubmed_xml(http.get_text(f"{EUTILS}/efetch.fcgi", fetch_params))


def parse_europepmc(data: dict[str, Any]) -> list[dict[str, Any]]:
    papers = []
    for r in data.get("resultList", {}).get("result", []):
        doi = r.get("doi", "")
        journal = (r.get("journalInfo") or {}).get("journal", {}).get("title") or r.get("journalTitle", "")
        if r.get("source") == "PPR":
            journal = journal or "Preprint"
        papers.append({
            "title": _clean(r.get("title")),
            "authors": r.get("authorString", ""),
            "journal": journal,
            "year": r.get("pubYear", ""),
            "doi": doi,
            "pmid": r.get("pmid", ""),
            "pmcid": r.get("pmcid", ""),
            "url": f"https://doi.org/{doi}" if doi else f"https://europepmc.org/article/{r.get('source')}/{r.get('id')}",
            "cited_by": r.get("citedByCount"),
            "open_access": r.get("isOpenAccess") == "Y",
            "abstract": _clean(r.get("abstractText")),
        })
    return papers


def _search_europepmc(query: str, max_results: int, year_from: int | None) -> list[dict[str, Any]]:
    q = f"({query}) AND PUB_YEAR:[{year_from} TO 3000]" if year_from else query
    data = http.get_json(EUROPEPMC, {"query": q, "format": "json", "resultType": "core", "pageSize": max_results})
    return parse_europepmc(data)


def parse_arxiv(xml: str) -> list[dict[str, Any]]:
    root = ET.fromstring(xml)
    papers = []
    for entry in root.findall(f"{ATOM}entry"):
        abs_url = entry.findtext(f"{ATOM}id", "")
        category = entry.find(f"{ARXIV_NS}primary_category")
        papers.append({
            "title": _clean(entry.findtext(f"{ATOM}title")),
            "authors": _authors([a.findtext(f"{ATOM}name", "") for a in entry.findall(f"{ATOM}author")]),
            "journal": entry.findtext(f"{ARXIV_NS}journal_ref") or "arXiv preprint",
            "year": entry.findtext(f"{ATOM}published", "")[:4],
            "doi": entry.findtext(f"{ARXIV_NS}doi", ""),
            "arxiv_id": abs_url.rsplit("/abs/", 1)[-1],
            "category": category.get("term") if category is not None else "",
            "url": abs_url,
            "abstract": _clean(entry.findtext(f"{ATOM}summary")),
        })
    return papers


def _search_arxiv(query: str, max_results: int, year_from: int | None) -> list[dict[str, Any]]:
    # Accept raw arXiv syntax (e.g. "cat:physics.chem-ph AND ti:catalysis"); otherwise AND the words.
    search = query if ":" in query else " AND ".join(f"all:{w}" for w in query.split())
    if year_from:
        search = f"({search}) AND submittedDate:[{year_from}01010000 TO 300001010000]"
    xml = http.get_text(ARXIV, {"search_query": search, "max_results": max_results, "sortBy": "relevance"})
    return parse_arxiv(xml)


def parse_crossref(data: dict[str, Any]) -> list[dict[str, Any]]:
    papers = []
    for item in data.get("message", {}).get("items", []):
        parts = (item.get("issued") or {}).get("date-parts") or [[None]]
        doi = item.get("DOI", "")
        papers.append({
            "title": _clean((item.get("title") or [""])[0]),
            "authors": _authors([f"{a.get('given', '')} {a.get('family', '')}".strip() for a in item.get("author", [])]),
            "journal": (item.get("container-title") or [""])[0],
            "year": str(parts[0][0] or ""),
            "doi": doi,
            "type": item.get("type", ""),
            "url": f"https://doi.org/{doi}",
            "cited_by": item.get("is-referenced-by-count"),
            "abstract": _clean(item.get("abstract")),
        })
    return papers


def _search_crossref(query: str, max_results: int, year_from: int | None) -> list[dict[str, Any]]:
    params: dict[str, Any] = {
        "query": query,
        "rows": max_results,
        "select": "DOI,title,author,container-title,issued,abstract,is-referenced-by-count,type",
    }
    if year_from:
        params["filter"] = f"from-pub-date:{year_from}"
    return parse_crossref(http.get_json(CROSSREF, params))


def _openalex_abstract(inverted: dict[str, list[int]] | None) -> str:
    if not inverted:
        return ""
    words = sorted((pos, word) for word, positions in inverted.items() for pos in positions)
    return _clean(" ".join(word for _, word in words))


def parse_openalex(data: dict[str, Any]) -> list[dict[str, Any]]:
    papers = []
    for w in data.get("results", []):
        doi = (w.get("doi") or "").removeprefix("https://doi.org/")
        pmcid = ((w.get("ids") or {}).get("pmcid") or "").rstrip("/").rsplit("/", 1)[-1]
        source = ((w.get("primary_location") or {}).get("source") or {})
        papers.append({
            "title": _clean(w.get("display_name")),
            "authors": _authors([(a.get("author") or {}).get("display_name", "") for a in w.get("authorships", [])]),
            "journal": source.get("display_name") or "",
            "year": str(w.get("publication_year") or ""),
            "doi": doi,
            "pmcid": pmcid,
            "url": f"https://doi.org/{doi}" if doi else w.get("id", ""),
            "cited_by": w.get("cited_by_count"),
            "open_access": bool((w.get("open_access") or {}).get("is_oa")),
            "abstract": _openalex_abstract(w.get("abstract_inverted_index")),
        })
    return papers


def _search_openalex(query: str, max_results: int, year_from: int | None) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"search": query, "per-page": max_results}
    if year_from:
        params["filter"] = f"from_publication_date:{year_from}-01-01"
    if os.environ.get("OPENALEX_API_KEY"):
        params["api_key"] = os.environ["OPENALEX_API_KEY"]
    return parse_openalex(http.get_json(OPENALEX, params))


def parse_jats_body(xml: str) -> str:
    """Plain text of a JATS article body (section titles and paragraphs), without references."""
    root = ET.fromstring(xml)
    body = root.find(".//body")
    if body is None:
        return ""
    parts = []
    for el in body.iter():
        if el.tag == "title":
            parts.append(f"\n## {_text(el)}")
        elif el.tag == "p":
            parts.append(_text(el))
    return "\n".join(p for p in parts if p.strip()).strip()


def europepmc_full_text(pmcid: str) -> str:
    """Open-access full text from Europe PMC, or '' if it is not available."""
    try:
        return parse_jats_body(http.get_text(EUROPEPMC_FULLTEXT.format(pmcid=pmcid)))
    except Exception:
        return ""


SOURCES = {
    "europepmc": _search_europepmc,
    "pubmed": _search_pubmed,
    "arxiv": _search_arxiv,
    "crossref": _search_crossref,
    "openalex": _search_openalex,
}


@tool(
    "search_literature",
    "Search the scientific literature and return papers with title, authors, journal, year, DOI, URL and "
    "abstract. Sources: 'europepmc' (default; life sciences + chemistry, includes ChemRxiv preprints and "
    "citation counts), 'pubmed' (biomedical, medicinal chemistry, toxicology), 'arxiv' (physical/theoretical/"
    "computational chemistry, materials; supports arXiv syntax like 'cat:physics.chem-ph AND ti:DFT'), "
    "'crossref' (all publishers incl. ACS, RSC, Elsevier, Wiley; abstracts often missing), 'openalex' "
    "(broadest index of all disciplines and publishers, with abstracts and citation counts). Call several sources in parallel for a thorough review.",
    {
        "query": {"type": "string", "minLength": 1, "description": "Keywords or a boolean query."},
        "source": {"type": "string", "enum": list(SOURCES)},
        "max_results": {"type": "integer", "minimum": 1, "maximum": 25, "description": "Default 10."},
        "year_from": {"type": "integer", "minimum": 1900, "maximum": 2100, "description": "Only papers from this year on."},
    },
    ["query"],
)
def search_literature(query: str, source: str = "europepmc", max_results: int = 10,
                      year_from: int | None = None) -> dict[str, Any]:
    papers = SOURCES[source](query, max_results, year_from)
    if not papers:
        raise ToolError(f"No results on {source} for {query!r}. Try broader terms or another source.")
    return {"source": source, "query": query, "count": len(papers), "results": papers}
