"""Structured outputs for each step of the research-gap workflow.

Every Claude call in the workflow returns one of these models (via structured outputs), so each
step's result is validated, saved as JSON, and can be inspected or resumed.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Source = Literal["europepmc", "pubmed", "arxiv", "crossref", "openalex"]


# Step 1: plan -----------------------------------------------------------------------------------

class SearchQuery(BaseModel):
    source: Source
    query: str = Field(description="Query in the syntax of that source.")


class SearchPlan(BaseModel):
    research_question: str = Field(description="The topic restated as a precise research question.")
    key_concepts: list[str]
    inclusion_criteria: list[str]
    exclusion_criteria: list[str]
    queries: list[SearchQuery] = Field(description="6-12 complementary queries spread across sources.")


# Step 3: screen ---------------------------------------------------------------------------------

class ScreeningDecision(BaseModel):
    paper_id: str
    relevance: int = Field(description="0 (off-topic) to 10 (central to the research question).")
    include: bool
    reason: str = Field(description="One short sentence.")


class ScreeningBatch(BaseModel):
    decisions: list[ScreeningDecision]


# Step 5: extract --------------------------------------------------------------------------------

class Finding(BaseModel):
    property: str = Field(description="What was measured or computed, e.g. 'PCE', 'IC50 vs EGFR', 'band gap'.")
    value: str = Field(description="Value with units, e.g. '23.1 %', '4 nM', '1.6 eV (HSE06)'.")
    conditions: str = Field(description="System and conditions, e.g. 'MAPbI3, 1 sun, 25 °C', or '' if none.")


class PaperExtraction(BaseModel):
    study_type: Literal["experimental", "computational", "theoretical", "review", "mixed", "other"]
    research_question: str
    systems_studied: list[str] = Field(description="Compounds, materials, targets, reactions or organisms studied.")
    methods: list[str] = Field(description="Synthesis, computational and characterization methods, each named specifically.")
    key_findings: list[Finding]
    stated_limitations: list[str] = Field(description="Limitations the authors state. Empty if none are stated.")
    future_work: list[str] = Field(description="Future directions the authors propose. Empty if none are stated.")
    open_questions: list[str] = Field(description="Questions the paper leaves unanswered, in your own judgment.")


# Step 6: gaps -----------------------------------------------------------------------------------

GapType = Literal[
    "unexplored_combination",   # system x method / condition never studied together
    "contradictory_evidence",   # studies disagree
    "methodological",           # a technique the question needs is missing (in-situ, long-term, DFT, ...)
    "recurring_limitation",     # the same limitation reported across papers
    "unaddressed_future_work",  # proposed but never done
    "scale_or_conditions",      # only lab scale, narrow conditions, idealized systems
    "mechanistic_understanding",  # results without mechanism
    "data_or_reproducibility",  # missing benchmarks, standards or reproducibility
]


class Gap(BaseModel):
    id: str = Field(description="G1, G2, ...")
    title: str
    gap_type: GapType
    description: str
    evidence: str = Field(description="How the corpus shows this gap, citing paper ids like [P3].")
    supporting_paper_ids: list[str]
    importance: Literal["high", "medium", "low"]
    why_it_matters: str
    novelty_check_queries: list[str] = Field(description="2-3 search queries that would find work already filling this gap.")


class GapAnalysis(BaseModel):
    state_of_the_art: str = Field(description="What the corpus establishes, citing paper ids.")
    consensus: list[str]
    gaps: list[Gap]


# Step 7: novelty check --------------------------------------------------------------------------

class NoveltyVerdict(BaseModel):
    status: Literal["open", "partially_addressed", "addressed"]
    rationale: str = Field(description="Cite the paper ids that address the gap, if any.")
    related_paper_ids: list[str]


# Step 8: methods --------------------------------------------------------------------------------

class TransferableMethod(BaseModel):
    method: str
    source_paper_ids: list[str]
    adaptation: str = Field(description="How to adapt it to this gap.")


class MethodProposal(BaseModel):
    hypothesis: str
    approach_summary: str
    experimental_steps: list[str] = Field(description="Ordered steps; empty if purely computational.")
    computational_methods: list[str] = Field(description="E.g. DFT functional/basis, MD force field, ML model; empty if none.")
    characterization: list[str]
    controls_and_validation: list[str]
    transferable_methods: list[TransferableMethod] = Field(description="Methods from the corpus that can be reused.")
    expected_outcomes: str
    success_metrics: list[str]
    feasibility: Literal["high", "medium", "low"]
    risks_and_mitigations: list[str]
    required_resources: list[str]
    safety_considerations: list[str]
