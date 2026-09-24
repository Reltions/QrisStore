"""The research-gap workflow, end to end with a fake LLM and fake literature search."""

import json

import pytest

from chem_agent.tools.literature import parse_jats_body, parse_openalex
from chem_agent.workflow import GapWorkflow, WorkflowConfig
from chem_agent.workflow.schemas import (
    Finding,
    Gap,
    GapAnalysis,
    MethodProposal,
    NoveltyVerdict,
    PaperExtraction,
    ScreeningBatch,
    ScreeningDecision,
    SearchPlan,
    SearchQuery,
    TransferableMethod,
)

PAPERS = [
    {"title": "Sn perovskite stability under humidity", "doi": "10.1/a", "year": "2022", "abstract": "Humidity degrades FASnI3.", "pmcid": "PMC1"},
    {"title": "SnF2 additives in tin perovskites", "doi": "10.1/b", "year": "2023", "abstract": "SnF2 suppresses Sn4+."},
    {"title": "Unrelated organic synthesis", "doi": "10.1/c", "year": "2021", "abstract": "Aldol reactions."},
    {"title": "No abstract paper", "doi": "10.1/d", "year": "2020", "abstract": ""},
]


class FakeLLM:
    model = "fake-model"

    def __init__(self):
        self.calls = []
        self.usage = {"input_tokens": 0, "output_tokens": 0}

    def structured(self, *, system, prompt, schema, effort):
        self.calls.append((schema.__name__, effort, prompt))
        if schema is SearchPlan:
            return SearchPlan(research_question="What limits tin perovskite stability?", key_concepts=["Sn2+ oxidation"],
                              inclusion_criteria=["tin halide perovskites"], exclusion_criteria=["lead-only"],
                              queries=[SearchQuery(source="openalex", query="tin perovskite stability"),
                                       SearchQuery(source="europepmc", query="Sn perovskite oxidation")])
        if schema is ScreeningBatch:
            ids = [p["id"] for p in json.loads(prompt.split("Papers:\n", 1)[1])]
            return ScreeningBatch(decisions=[
                ScreeningDecision(paper_id=i, relevance=9 if "perovskite" in prompt.split(i)[1][:80].lower() else 1,
                                  include="perovskite" in prompt.split(i)[1][:80].lower(), reason="r")
                for i in ids
            ])
        if schema is PaperExtraction:
            return PaperExtraction(study_type="experimental", research_question="q", systems_studied=["FASnI3"],
                                   methods=["XPS", "in-situ XRD"], key_findings=[Finding(property="PCE", value="10 %", conditions="1 sun")],
                                   stated_limitations=["short test duration"], future_work=["long-term tests"], open_questions=["mechanism?"])
        if schema is GapAnalysis:
            gap = lambda i, t: Gap(id=i, title=t, gap_type="recurring_limitation", description="d", evidence="[P1] [P2]",
                                   supporting_paper_ids=["P1", "P2"], importance="high", why_it_matters="w",
                                   novelty_check_queries=["long-term tin perovskite"])
            return GapAnalysis(state_of_the_art="SnF2 helps [P2].", consensus=["Sn2+ oxidizes"],
                               gaps=[gap("G1", "No 1000 h stability tests"), gap("G2", "Already solved gap")])
        if schema is NoveltyVerdict:
            addressed = "Already solved" in prompt
            return NoveltyVerdict(status="addressed" if addressed else "open", rationale="See N1." if addressed else "Nothing found.",
                                  related_paper_ids=["N1"] if addressed else [])
        if schema is MethodProposal:
            return MethodProposal(hypothesis="h", approach_summary="a", experimental_steps=["make films"], computational_methods=[],
                                  characterization=["XPS"], controls_and_validation=["MAPbI3 control"],
                                  transferable_methods=[TransferableMethod(method="in-situ XRD", source_paper_ids=["P1"], adaptation="x")],
                                  expected_outcomes="e", success_metrics=["T80 > 1000 h"], feasibility="medium",
                                  risks_and_mitigations=["r"], required_resources=["glovebox"], safety_considerations=["Sn toxicity"])
        raise AssertionError(schema)


def fake_search(source, query, n, year_from):
    if "long-term" in query:
        return [{"title": "1000 h operational stability of Sn perovskites", "doi": "10.2/new", "year": "2025", "abstract": "Long test."},
                PAPERS[0]]  # an already-known paper keeps its P id
    return [dict(p) for p in PAPERS]


def run(tmp_path, llm):
    config = WorkflowConfig(topic="tin perovskite stability", run_dir=tmp_path, max_papers=10)
    logs = []
    wf = GapWorkflow(config, llm=llm, search=fake_search, fetch_full_text=lambda pmcid: "## Methods\nFull text body.", log=logs.append)
    return wf.run(), logs


def test_full_workflow(tmp_path):
    llm = FakeLLM()
    report_path, logs = run(tmp_path, llm)
    report = report_path.read_text()

    screening = json.loads((tmp_path / "screening.json").read_text())
    assert screening["included"] == ["P1", "P2"]  # off-topic and abstract-less papers excluded

    ext = json.loads((tmp_path / "extractions/P1.json").read_text())
    assert ext["evidence"] == "full_text"  # P1 had a PMC id
    assert json.loads((tmp_path / "extractions/P2.json").read_text())["evidence"] == "abstract"
    assert "Full text:\n## Methods" in next(p for s, _, p in llm.calls if s == "PaperExtraction" and "Paper P1" in p)

    # Only the gap that is still open gets a study design.
    assert (tmp_path / "methods/G1.json").exists() and not (tmp_path / "methods/G2.json").exists()

    efforts = {s: e for s, e, _ in llm.calls}
    assert efforts["GapAnalysis"] == "high" and efforts["PaperExtraction"] == "medium"

    assert "| G1: No 1000 h stability tests | recurring limitation | high | Open | yes |" in report
    assert "| G2: Already solved gap | recurring limitation | high | Already addressed | no |" in report
    assert "**[P1]**" in report and "**[N1]**" in report and "10.2/new" in report
    assert "**[P3]**" not in report  # screened out, never cited


def test_resume_makes_no_new_calls(tmp_path):
    run(tmp_path, FakeLLM())
    llm = FakeLLM()
    report_path, _ = run(tmp_path, llm)
    assert llm.calls == []
    assert "**[N1]**" in report_path.read_text()


def test_resume_after_failure_in_extraction(tmp_path):
    class Flaky(FakeLLM):
        def structured(self, **kw):
            if kw["schema"] is GapAnalysis:
                raise RuntimeError("API down")
            return super().structured(**kw)

    with pytest.raises(RuntimeError):
        run(tmp_path, Flaky())
    llm = FakeLLM()
    run(tmp_path, llm)
    assert "SearchPlan" not in [s for s, _, _ in llm.calls]
    assert "PaperExtraction" not in [s for s, _, _ in llm.calls]


def test_no_results_raises(tmp_path):
    config = WorkflowConfig(topic="t", run_dir=tmp_path)
    wf = GapWorkflow(config, llm=FakeLLM(), search=lambda *a: [], log=lambda m: None)
    with pytest.raises(RuntimeError, match="No papers"):
        wf.run()


def test_parse_openalex():
    data = {"results": [{
        "id": "https://openalex.org/W1", "doi": "https://doi.org/10.1/x", "display_name": "MOF water harvesting",
        "publication_year": 2024, "cited_by_count": 12, "ids": {"pmcid": "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC999"},
        "authorships": [{"author": {"display_name": "O. Yaghi"}}],
        "primary_location": {"source": {"display_name": "Science"}},
        "abstract_inverted_index": {"harvest": [1], "MOFs": [0], "water.": [2]},
    }]}
    [paper] = parse_openalex(data)
    assert paper["doi"] == "10.1/x" and paper["pmcid"] == "PMC999"
    assert paper["abstract"] == "MOFs harvest water."
    assert paper["journal"] == "Science" and paper["year"] == "2024"


def test_parse_jats_body():
    xml = """<article><front><title>ignored</title></front><body>
      <sec><title>Methods</title><p>Films were <italic>spin</italic>-coated.</p></sec>
    </body><back><ref-list><title>References</title></ref-list></back></article>"""
    assert parse_jats_body(xml) == "## Methods\nFilms were spin-coated."


def _sdk_client(stop_reason, text):
    """A real Anthropic client whose HTTP transport replays a canned streaming response."""
    import anthropic
    import httpx2

    events = [
        {"type": "message_start", "message": {"id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5",
                                              "content": [], "stop_reason": None, "stop_sequence": None,
                                              "usage": {"input_tokens": 50, "output_tokens": 0}}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text}},
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta", "delta": {"stop_reason": stop_reason, "stop_sequence": None}, "usage": {"output_tokens": 20}},
        {"type": "message_stop"},
    ]
    sent = {}

    def handler(request):
        sent["body"] = json.loads(request.content)
        sent["beta"] = request.headers.get("anthropic-beta")
        body = "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events)
        return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=body.encode())

    client = anthropic.Anthropic(api_key="test", http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handler)))
    return client, sent


def test_llm_structured_through_sdk():
    from chem_agent.workflow.llm import LLM

    verdict = {"status": "open", "rationale": "none found", "related_paper_ids": []}
    client, sent = _sdk_client("end_turn", json.dumps(verdict))
    llm = LLM(client=client, model="claude-opus-5")
    result = llm.structured(system="s", prompt="p", schema=NoveltyVerdict, effort="medium")
    assert result == NoveltyVerdict(**verdict)
    assert sent["body"]["output_config"]["effort"] == "medium"
    assert sent["body"]["output_config"]["format"]["type"] == "json_schema"
    assert sent["body"]["fallbacks"] == "default" and sent["beta"] == "server-side-fallback-2026-07-01"
    assert llm.usage["input_tokens"] == 50


def test_llm_refusal_raises():
    from chem_agent.agent import RefusalError
    from chem_agent.workflow.llm import LLM

    client, _ = _sdk_client("refusal", "")
    with pytest.raises(RefusalError):
        LLM(client=client, model="claude-opus-5").structured(system="s", prompt="p", schema=NoveltyVerdict, effort="low")
