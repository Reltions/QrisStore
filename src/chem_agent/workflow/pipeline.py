"""Research-gap workflow: from a topic to evidence-backed gaps and methods to fill them.

Steps (each result is saved in the run folder, so an interrupted run resumes where it stopped):

1. plan      Claude turns the topic into a research question, criteria and search queries.
2. retrieve  The queries run against literature APIs; results are merged and deduplicated.
3. screen    Claude scores each title/abstract against the inclusion criteria.
4. full text Open-access full texts are fetched from Europe PMC where available.
5. extract   Claude extracts structured data from each paper (methods, findings, limitations...).
6. gaps      Claude compares all extractions and identifies research gaps, citing papers.
7. novelty   Each gap is searched for again; Claude judges whether newer work already fills it.
8. methods   For each open gap, Claude designs a study, reusing methods found in the corpus.
9. report    Everything is written to a Markdown report with references.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel

from ..agent import RefusalError
from ..tools.literature import SOURCES, europepmc_full_text
from .llm import LLM, IncompleteOutputError
from .report import render_report
from .schemas import (
    GapAnalysis,
    MethodProposal,
    NoveltyVerdict,
    PaperExtraction,
    ScreeningBatch,
    SearchPlan,
)

SCREEN_BATCH = 20
NOVELTY_SOURCES = ("openalex", "europepmc")
NOVELTY_RESULTS = 8

Search = Callable[[str, str, int, "int | None"], list[dict[str, Any]]]


@dataclass
class WorkflowConfig:
    topic: str
    run_dir: Path
    context: str = ""
    year_from: int | None = None
    results_per_query: int = 20
    max_candidates: int = 150
    max_papers: int = 30
    max_gaps: int = 6
    full_text: bool = True
    effort: str = "high"          # gap analysis and study design
    extract_effort: str = "medium"  # planning, screening, extraction, novelty checks
    workers: int = 4


@dataclass
class Corpus:
    """Papers by id: P* from the main search, N* found only during novelty checks."""
    papers: dict[str, dict[str, Any]] = field(default_factory=dict)
    keys: dict[str, str] = field(default_factory=dict)

    def add(self, paper: dict[str, Any], prefix: str) -> str:
        key = paper_key(paper)
        if key in self.keys:
            pid = self.keys[key]
            merge_paper(self.papers[pid], paper)
            return pid
        pid = f"{prefix}{sum(1 for p in self.papers if p.startswith(prefix)) + 1}"
        self.keys[key] = pid
        self.papers[pid] = {"id": pid, **paper}
        return pid


def paper_key(paper: dict[str, Any]) -> str:
    if paper.get("doi"):
        return "doi:" + paper["doi"].lower().strip()
    return "title:" + re.sub(r"[^a-z0-9]", "", paper.get("title", "").lower())[:120]


def merge_paper(into: dict[str, Any], other: dict[str, Any]) -> None:
    for k, v in other.items():
        if k == "abstract" and len(v or "") > len(into.get("abstract") or ""):
            into[k] = v
        elif k == "cited_by":
            into[k] = max(into.get(k) or 0, v or 0)
        elif k == "sources":
            into[k] = sorted(set(into.get(k, [])) | set(v))
        elif not into.get(k) and v:
            into[k] = v


def default_search(source: str, query: str, n: int, year_from: int | None) -> list[dict[str, Any]]:
    return SOURCES[source](query, n, year_from)


class GapWorkflow:
    def __init__(self, config: WorkflowConfig, llm: LLM | None = None, search: Search | None = None,
                 fetch_full_text: Callable[[str], str] | None = None, log: Callable[[str], None] = print) -> None:
        self.cfg = config
        self.llm = llm or LLM()
        self.search = search or default_search
        self.fetch_full_text = fetch_full_text or europepmc_full_text
        self.log = log
        self.dir = Path(config.run_dir)

    # -- persistence ------------------------------------------------------------------------------

    def _path(self, name: str) -> Path:
        return self.dir / name

    def _load(self, name: str, schema: type[BaseModel] | None = None) -> Any:
        path = self._path(name)
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return schema.model_validate(data) if schema else data

    def _save(self, name: str, data: Any) -> None:
        path = self._path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, BaseModel):
            data = data.model_dump()
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def _parallel(self, func: Callable[[Any], Any], items: list[Any]) -> list[Any]:
        if not items:
            return []
        with ThreadPoolExecutor(max_workers=self.cfg.workers) as pool:
            return list(pool.map(func, items))

    # -- steps ------------------------------------------------------------------------------------

    def run(self) -> Path:
        self.dir.mkdir(parents=True, exist_ok=True)
        self._save("config.json", {k: str(v) if isinstance(v, Path) else v for k, v in asdict(self.cfg).items()})
        plan = self.plan()
        corpus, n_retrieved = self.retrieve(plan)
        included = self.screen(plan, corpus)
        self.add_full_text(corpus, included)
        extracted = self.extract(plan, corpus, included)
        analysis = self.find_gaps(plan, corpus, extracted)
        novelty = self.check_novelty(analysis, corpus)
        proposals = self.propose_methods(plan, analysis, novelty, corpus, extracted)
        self._save("corpus.json", corpus.papers)
        report = render_report(
            plan=plan, corpus=corpus.papers, n_retrieved=n_retrieved, included=included, extracted=extracted,
            analysis=analysis, novelty=novelty, proposals=proposals, model=self.llm.model,
        )
        path = self._path("report.md")
        path.write_text(report, encoding="utf-8")
        self.log(f"Report: {path}")
        return path

    def plan(self) -> SearchPlan:
        if plan := self._load("plan.json", SearchPlan):
            return plan
        self.log("[1/9] Planning the literature search")
        prompt = (
            f"Research topic: {self.cfg.topic}\n"
            + (f"Context from the researcher: {self.cfg.context}\n" if self.cfg.context else "")
            + f"Available sources: {', '.join(SOURCES)}.\n"
            "Write a search plan for a systematic review aimed at finding research gaps. Use queries that "
            "together cover the topic from different angles (synonyms, key materials or compounds, methods, "
            "applications). Europe PMC and PubMed accept boolean queries; arXiv accepts 'all:term AND "
            "cat:physics.chem-ph' syntax; OpenAlex and Crossref take plain keywords."
        )
        plan = self.llm.structured(
            system="You are an expert chemist planning a systematic literature search.",
            prompt=prompt, schema=SearchPlan, effort=self.cfg.extract_effort,
        )
        self._save("plan.json", plan)
        return plan

    def retrieve(self, plan: SearchPlan) -> tuple[Corpus, int]:
        corpus = Corpus()
        cached = self._load("candidates.json")
        if cached is not None:
            for paper in cached["papers"]:
                corpus.add({k: v for k, v in paper.items() if k != "id"}, "P")
            return corpus, cached["retrieved"]

        self.log(f"[2/9] Searching {len(plan.queries)} queries")

        def run_query(q: Any) -> list[dict[str, Any]]:
            try:
                results = self.search(q.source, q.query, self.cfg.results_per_query, self.cfg.year_from)
            except Exception as exc:
                self.log(f"  ! {q.source} '{q.query}': {exc}")
                return []
            return [{**r, "sources": [q.source]} for r in results]

        batches = self._parallel(run_query, plan.queries)
        found = [p for batch in batches for p in batch if p.get("title")]
        if not found:
            raise RuntimeError("No papers were retrieved. Check your network connection and the queries.")
        # Papers with abstracts first (they can be screened properly), then cap the candidate pool.
        found.sort(key=lambda p: not p.get("abstract"))
        for paper in found:
            if len(corpus.papers) >= self.cfg.max_candidates and paper_key(paper) not in corpus.keys:
                continue
            corpus.add(paper, "P")
        self.log(f"  {len(found)} results, {len(corpus.papers)} unique papers")
        self._save("candidates.json", {"retrieved": len(found), "papers": list(corpus.papers.values())})
        return corpus, len(found)

    def screen(self, plan: SearchPlan, corpus: Corpus) -> list[str]:
        if (cached := self._load("screening.json")) is not None:
            return cached["included"]
        ids = list(corpus.papers)
        self.log(f"[3/9] Screening {len(ids)} papers")
        criteria = (
            f"Research question: {plan.research_question}\n"
            f"Inclusion criteria: {json.dumps(plan.inclusion_criteria)}\n"
            f"Exclusion criteria: {json.dumps(plan.exclusion_criteria)}\n"
        )

        def screen_batch(batch: list[str]) -> list[dict[str, Any]]:
            papers = [{k: corpus.papers[i].get(k, "") for k in ("id", "title", "year", "journal", "abstract")} for i in batch]
            try:
                result = self.llm.structured(
                    system="You screen papers for a systematic review in chemistry. Judge each paper only by "
                           "its title and abstract. Give a decision for every paper id you are given.",
                    prompt=f"{criteria}\nPapers:\n{json.dumps(papers, ensure_ascii=False)}",
                    schema=ScreeningBatch, effort=self.cfg.extract_effort,
                )
            except (RefusalError, IncompleteOutputError) as exc:
                self.log(f"  ! screening batch {batch[0]}..{batch[-1]} skipped: {exc}")
                return []
            return [d.model_dump() for d in result.decisions if d.paper_id in batch]

        batches = [ids[i:i + SCREEN_BATCH] for i in range(0, len(ids), SCREEN_BATCH)]
        decisions = [d for batch in self._parallel(screen_batch, batches) for d in batch]
        chosen = sorted(
            (d for d in decisions if d["include"]),
            key=lambda d: (d["relevance"], corpus.papers[d["paper_id"]].get("cited_by") or 0),
            reverse=True,
        )[: self.cfg.max_papers]
        included = [d["paper_id"] for d in chosen]
        self.log(f"  {sum(d['include'] for d in decisions)} relevant; keeping the top {len(included)}")
        self._save("screening.json", {"decisions": decisions, "included": included})
        return included

    def add_full_text(self, corpus: Corpus, included: list[str]) -> None:
        if not self.cfg.full_text:
            return
        todo = [pid for pid in included if corpus.papers[pid].get("pmcid") and not self._path(f"fulltext/{pid}.txt").exists()]
        if todo:
            self.log(f"[4/9] Fetching open-access full text for {len(todo)} papers")

        def fetch(pid: str) -> None:
            text = self.fetch_full_text(corpus.papers[pid]["pmcid"])
            if text:
                path = self._path(f"fulltext/{pid}.txt")
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8")

        self._parallel(fetch, todo)

    def extract(self, plan: SearchPlan, corpus: Corpus, included: list[str]) -> dict[str, dict[str, Any]]:
        self.log(f"[5/9] Extracting data from {len(included)} papers")

        def extract_one(pid: str) -> tuple[str, dict[str, Any] | None]:
            if (cached := self._load(f"extractions/{pid}.json")) is not None:
                return pid, cached
            paper = corpus.papers[pid]
            ft_path = self._path(f"fulltext/{pid}.txt")
            full_text = ft_path.read_text(encoding="utf-8") if ft_path.exists() else ""
            text = full_text or paper.get("abstract", "")
            if not text:
                self.log(f"  ! {pid} skipped: no abstract or full text available")
                return pid, None
            prompt = (
                f"Research question of the review: {plan.research_question}\n\n"
                f"Paper {pid}: {paper.get('title')} ({paper.get('journal')}, {paper.get('year')})\n\n"
                f"{'Full text' if full_text else 'Abstract'}:\n{text}"
            )
            try:
                ext = self.llm.structured(
                    system=(
                        "You extract structured data from chemistry papers for a systematic review. Report "
                        "only what the text states, except 'open_questions', which is your own judgment. Name "
                        "methods specifically (e.g. 'in-situ GIWAXS', 'DFT PBE+U', 'Suzuki coupling'), give "
                        "every value with units and conditions, and leave lists empty rather than guessing."
                    ),
                    prompt=prompt, schema=PaperExtraction, effort=self.cfg.extract_effort,
                )
            except (RefusalError, IncompleteOutputError) as exc:
                self.log(f"  ! {pid} skipped: {exc}")
                return pid, None
            record = {"evidence": "full_text" if full_text else "abstract", **ext.model_dump()}
            self._save(f"extractions/{pid}.json", record)
            return pid, record

        results = dict(self._parallel(extract_one, included))
        extracted = {pid: r for pid, r in results.items() if r is not None}
        if not extracted:
            raise RuntimeError("No papers could be extracted.")
        n_full = sum(r["evidence"] == "full_text" for r in extracted.values())
        self.log(f"  {len(extracted)} extracted ({n_full} from full text)")
        return extracted

    def find_gaps(self, plan: SearchPlan, corpus: Corpus, extracted: dict[str, dict[str, Any]]) -> GapAnalysis:
        if analysis := self._load("gaps.json", GapAnalysis):
            return analysis
        self.log(f"[6/9] Analysing {len(extracted)} papers for research gaps")
        studies = [
            {"id": pid, "title": corpus.papers[pid].get("title"), "year": corpus.papers[pid].get("year"), **ext}
            for pid, ext in extracted.items()
        ]
        prompt = (
            f"Research question: {plan.research_question}\n"
            + (f"Researcher's context: {self.cfg.context}\n" if self.cfg.context else "")
            + f"\nEvidence map (counts across the corpus):\n{json.dumps(evidence_map(extracted), ensure_ascii=False)}\n"
            f"\nExtracted studies:\n{json.dumps(studies, ensure_ascii=False)}\n\n"
            f"Identify up to {self.cfg.max_gaps} of the most important research gaps, most important first."
        )
        analysis = self.llm.structured(
            system=(
                "You are a senior chemistry researcher identifying research gaps from a systematic review. "
                "Look for: system/method combinations never studied together, contradictory results, "
                "missing methods (in-situ, long-term, mechanistic, computational), limitations that recur "
                "across papers, proposed future work nobody has done, and narrow conditions or scale. Ground "
                "every gap in the corpus and cite paper ids like [P3]. A gap must be specific and actionable; "
                "'more research is needed' is not a gap. Some studies were read from abstracts only "
                "('evidence': 'abstract'), so absence of a detail there is weak evidence; say so when it matters."
            ),
            prompt=prompt, schema=GapAnalysis, effort=self.cfg.effort,
        )
        self._save("gaps.json", analysis)
        self.log(f"  {len(analysis.gaps)} gaps identified")
        return analysis

    def check_novelty(self, analysis: GapAnalysis, corpus: Corpus) -> dict[str, dict[str, Any]]:
        cached = self._load("novelty.json")
        if cached is not None:
            for paper in cached.get("papers", []):
                corpus.papers.setdefault(paper["id"], paper)
                corpus.keys.setdefault(paper_key(paper), paper["id"])
            return cached["verdicts"]
        self.log(f"[7/9] Checking whether {len(analysis.gaps)} gaps are already addressed")

        def search_gap(gap: Any) -> list[dict[str, Any]]:
            hits = []
            for query in gap.novelty_check_queries[:3]:
                for source in NOVELTY_SOURCES:
                    try:
                        hits += self.search(source, query, NOVELTY_RESULTS, None)
                    except Exception as exc:
                        self.log(f"  ! novelty search {source} '{query}': {exc}")
            return hits

        # Searching runs in parallel; ids are assigned afterwards, in a fixed order.
        hit_lists = self._parallel(search_gap, analysis.gaps)
        found = {gap.id: list(dict.fromkeys(corpus.add(p, "N") for p in hits if p.get("title")))
                 for gap, hits in zip(analysis.gaps, hit_lists)}

        def judge(gap: Any) -> tuple[str, dict[str, Any]]:
            ids = found[gap.id]
            if not ids:
                return gap.id, {"status": "unchecked", "rationale": "The novelty search returned no results.", "related_paper_ids": []}
            papers = [{k: corpus.papers[i].get(k, "") for k in ("id", "title", "year", "abstract")} for i in ids]
            try:
                verdict = self.llm.structured(
                    system="You check whether a proposed research gap has already been addressed in the literature. "
                           "Judge strictly: only mark it addressed if a paper directly does what the gap describes.",
                    prompt=f"Gap:\n{gap.model_dump_json()}\n\nSearch results:\n{json.dumps(papers, ensure_ascii=False)}",
                    schema=NoveltyVerdict, effort=self.cfg.extract_effort,
                )
            except (RefusalError, IncompleteOutputError) as exc:
                return gap.id, {"status": "unchecked", "rationale": str(exc), "related_paper_ids": []}
            return gap.id, verdict.model_dump()

        verdicts = dict(self._parallel(judge, analysis.gaps))
        novelty_papers = [p for pid, p in corpus.papers.items() if pid.startswith("N")]
        self._save("novelty.json", {"verdicts": verdicts, "papers": novelty_papers})
        counts = Counter(v["status"] for v in verdicts.values())
        self.log("  " + ", ".join(f"{n} {s.replace('_', ' ')}" for s, n in counts.items()))
        return verdicts

    def propose_methods(self, plan: SearchPlan, analysis: GapAnalysis, novelty: dict[str, dict[str, Any]],
                        corpus: Corpus, extracted: dict[str, dict[str, Any]]) -> dict[str, MethodProposal]:
        targets = [g for g in analysis.gaps if novelty.get(g.id, {}).get("status") != "addressed"]
        self.log(f"[8/9] Designing studies for {len(targets)} open gaps")
        inventory: dict[str, list[str]] = {}
        for pid, ext in extracted.items():
            for method in ext["methods"]:
                inventory.setdefault(method, []).append(pid)

        def design(gap: Any) -> tuple[str, MethodProposal | None]:
            if (cached := self._load(f"methods/{gap.id}.json", MethodProposal)) is not None:
                return gap.id, cached
            support = {pid: extracted[pid] for pid in gap.supporting_paper_ids if pid in extracted}
            prompt = (
                f"Research question of the review: {plan.research_question}\n"
                + (f"Researcher's context: {self.cfg.context}\n" if self.cfg.context else "")
                + f"\nGap:\n{gap.model_dump_json()}\n"
                f"\nNovelty check: {json.dumps(novelty.get(gap.id, {}))}\n"
                f"\nExtractions of the papers behind this gap:\n{json.dumps(support, ensure_ascii=False)}\n"
                f"\nMethods used across the corpus (method: paper ids):\n{json.dumps(inventory, ensure_ascii=False)}\n\n"
                "Design a study that would fill this gap."
            )
            try:
                proposal = self.llm.structured(
                    system=(
                        "You are a principal investigator in chemistry designing a study to fill a research gap. "
                        "Be concrete: name techniques, instruments, computational levels of theory, key "
                        "parameters and controls. Reuse and adapt methods from the corpus where they fit and cite "
                        "their paper ids. Keep the design feasible for a well-equipped academic lab, and flag "
                        "hazards. Do not design anything related to chemical weapons, explosives or illicit drugs."
                    ),
                    prompt=prompt, schema=MethodProposal, effort=self.cfg.effort,
                )
            except (RefusalError, IncompleteOutputError) as exc:
                self.log(f"  ! {gap.id} study design skipped: {exc}")
                return gap.id, None
            self._save(f"methods/{gap.id}.json", proposal)
            return gap.id, proposal

        return {gid: p for gid, p in self._parallel(design, targets) if p is not None}


def evidence_map(extracted: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Counts that make coverage (and missing combinations) visible at a glance."""
    methods, systems, types, evidence = Counter(), Counter(), Counter(), Counter()
    for ext in extracted.values():
        methods.update({m.strip().lower() for m in ext["methods"]})
        systems.update({s.strip().lower() for s in ext["systems_studied"]})
        types[ext["study_type"]] += 1
        evidence[ext.get("evidence", "")] += 1
    return {
        "papers": len(extracted),
        "study_types": dict(types),
        "evidence_level": dict(evidence),
        "methods": methods.most_common(40),
        "systems": systems.most_common(40),
    }


def default_run_dir(topic: str, root: str | Path = "runs") -> Path:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", topic).strip("-").lower()[:50] or "topic"
    return Path(root) / f"{dt.date.today().isoformat()}-{slug}"
