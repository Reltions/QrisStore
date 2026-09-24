"""Markdown report for a research-gap workflow run."""

from __future__ import annotations

import datetime as dt
import re
from typing import Any

from .schemas import GapAnalysis, MethodProposal, SearchPlan

STATUS_LABEL = {
    "open": "Open",
    "partially_addressed": "Partially addressed",
    "addressed": "Already addressed",
    "unchecked": "Not checked",
}


def _cell(text: Any) -> str:
    return str(text or "").replace("|", "/").replace("\n", " ")


def _bullets(items: list[str]) -> str:
    return "\n".join(f"- {i}" for i in items) if items else "- (none)"


def _cited_ids(text: str) -> list[str]:
    return re.findall(r"\b[PN]\d+\b", text)


def render_report(*, plan: SearchPlan, corpus: dict[str, dict[str, Any]], n_retrieved: int, included: list[str],
                  extracted: dict[str, dict[str, Any]], analysis: GapAnalysis, novelty: dict[str, dict[str, Any]],
                  proposals: dict[str, MethodProposal], model: str) -> str:
    n_full = sum(e["evidence"] == "full_text" for e in extracted.values())
    candidates = sum(1 for pid in corpus if pid.startswith("P"))
    out: list[str] = []
    add = out.append

    add(f"# Research gaps: {plan.research_question}\n")
    add(f"_Generated {dt.date.today().isoformat()} with {model}. Corpus: {len(extracted)} papers "
        f"({n_full} read in full text, {len(extracted) - n_full} from abstracts), screened from {candidates} "
        f"unique candidates. AI-generated: verify gaps and designs against the cited papers._\n")

    add("## Summary\n")
    add("| Gap | Type | Importance | Status | Study designed |")
    add("|---|---|---|---|---|")
    for g in analysis.gaps:
        status = STATUS_LABEL[novelty.get(g.id, {}).get("status", "unchecked")]
        add(f"| {g.id}: {_cell(g.title)} | {g.gap_type.replace('_', ' ')} | {g.importance} | {status} | "
            f"{'yes' if g.id in proposals else 'no'} |")
    add("")

    add("## Search strategy\n")
    add(f"**Research question:** {plan.research_question}\n")
    add(f"**Key concepts:** {', '.join(plan.key_concepts)}\n")
    add(f"**Inclusion criteria:**\n{_bullets(plan.inclusion_criteria)}\n")
    add(f"**Exclusion criteria:**\n{_bullets(plan.exclusion_criteria)}\n")
    add("| Source | Query |\n|---|---|")
    for q in plan.queries:
        add(f"| {q.source} | `{_cell(q.query)}` |")
    add("")
    add(f"Records retrieved: {n_retrieved}. Unique papers: {candidates}. Included after screening: "
        f"{len(included)}. Extracted: {len(extracted)} ({n_full} full text).\n")

    add("## State of the art\n")
    add(analysis.state_of_the_art + "\n")
    if analysis.consensus:
        add(f"**Points of consensus:**\n{_bullets(analysis.consensus)}\n")

    add("## Evidence table\n")
    add("| Paper | Year | Type | Systems | Methods | Read from |")
    add("|---|---|---|---|---|---|")
    for pid, ext in extracted.items():
        add(f"| {pid} | {corpus[pid].get('year', '')} | {ext['study_type']} | {_cell('; '.join(ext['systems_studied'][:3]))} | "
            f"{_cell('; '.join(ext['methods'][:4]))} | {ext['evidence'].replace('_', ' ')} |")
    add("")

    add("## Gaps and proposed studies\n")
    for g in analysis.gaps:
        verdict = novelty.get(g.id, {"status": "unchecked", "rationale": "", "related_paper_ids": []})
        add(f"### {g.id}. {g.title}\n")
        add(f"**Type:** {g.gap_type.replace('_', ' ')}. **Importance:** {g.importance}. "
            f"**Novelty check:** {STATUS_LABEL[verdict['status']]}.\n")
        add(f"{g.description}\n")
        add(f"**Evidence:** {g.evidence}\n")
        add(f"**Why it matters:** {g.why_it_matters}\n")
        add(f"**Novelty check:** {verdict['rationale']}"
            + (f" Related: {', '.join(verdict['related_paper_ids'])}." if verdict["related_paper_ids"] else "") + "\n")
        p = proposals.get(g.id)
        if p is None:
            add("_No study designed (gap already addressed or design step skipped)._\n")
            continue
        add("#### Proposed study\n")
        add(f"**Hypothesis:** {p.hypothesis}\n")
        add(f"{p.approach_summary}\n")
        if p.experimental_steps:
            add("**Experimental steps:**\n" + "\n".join(f"{i}. {s}" for i, s in enumerate(p.experimental_steps, 1)) + "\n")
        if p.computational_methods:
            add(f"**Computational methods:**\n{_bullets(p.computational_methods)}\n")
        add(f"**Characterization:**\n{_bullets(p.characterization)}\n")
        add(f"**Controls and validation:**\n{_bullets(p.controls_and_validation)}\n")
        if p.transferable_methods:
            add("**Methods to reuse from the literature:**")
            for m in p.transferable_methods:
                add(f"- {m.method} ({', '.join(m.source_paper_ids)}): {m.adaptation}")
            add("")
        add(f"**Expected outcomes:** {p.expected_outcomes}\n")
        add(f"**Success metrics:**\n{_bullets(p.success_metrics)}\n")
        add(f"**Feasibility:** {p.feasibility}. **Resources:** {'; '.join(p.required_resources)}\n")
        add(f"**Risks and mitigations:**\n{_bullets(p.risks_and_mitigations)}\n")
        add(f"**Safety:**\n{_bullets(p.safety_considerations)}\n")

    add("## Limitations of this analysis\n")
    add(_bullets([
        f"The corpus is a sample of {len(extracted)} papers chosen by automated search and screening; "
        "relevant work may be missing, especially from sources without abstracts.",
        f"Papers read from abstracts only: {len(extracted) - n_full}. Details missing from an abstract may be in the full text.",
        "Gaps, novelty verdicts and study designs are generated by a language model. Check each against the cited papers.",
    ]) + "\n")

    # References: every paper cited anywhere in the report, plus the extracted corpus.
    body = "\n".join(out)
    cited = list(dict.fromkeys(list(extracted) + [i for i in _cited_ids(body) if i in corpus]))
    add("## References\n")
    for pid in sorted(cited, key=lambda i: (i[0] != "P", int(i[1:]))):
        p = corpus[pid]
        link = f"https://doi.org/{p['doi']}" if p.get("doi") else p.get("url", "")
        add(f"- **[{pid}]** {p.get('authors', '')} ({p.get('year', '')}). {p.get('title', '')}. "
            f"_{p.get('journal', '')}_. {link}")
    return "\n".join(out) + "\n"
