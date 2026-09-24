"""The agent loop, driven by a fake Claude client."""

import copy
from types import SimpleNamespace

import pytest

from chem_agent.agent import FALLBACK_BETA, AgentEvents, RefusalError, ResearchAgent
from chem_agent.tools import validate
from chem_agent.tools.cheminformatics import molecule_properties


def text(t):
    return SimpleNamespace(type="text", text=t)


def tool_use(id, name, input):
    return SimpleNamespace(type="tool_use", id=id, name=name, input=input)


def message(stop_reason, *content):
    usage = SimpleNamespace(input_tokens=10, output_tokens=5, cache_read_input_tokens=0, cache_creation_input_tokens=0)
    return SimpleNamespace(stop_reason=stop_reason, content=list(content), usage=usage, stop_details=None)


class FakeStream:
    def __init__(self, response):
        self.response = response

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        return iter([])

    def get_final_message(self):
        return self.response


class FakeClient:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self._stream))

    def _stream(self, **kwargs):
        self.requests.append(copy.copy(kwargs) | {"messages": list(kwargs["messages"])})
        return FakeStream(self.responses.pop(0))


class Recorder(AgentEvents):
    def __init__(self):
        self.calls, self.notices = [], []

    def on_tool_call(self, name, args):
        self.calls.append(name)

    def on_notice(self, message):
        self.notices.append(message)


def make_agent(client, **kw):
    return ResearchAgent(client=client, model="claude-opus-5", events=Recorder(), **kw)


def test_tool_loop_runs_tools_and_returns_answer(tmp_path):
    client = FakeClient(
        message("tool_use", text("Let me compute."),
                tool_use("t1", "molecule_properties", {"smiles": ["CCO"]}),
                tool_use("t2", "search_literature", {"query": 5})),  # invalid input type
        message("end_turn", text("Ethanol weighs 46.07 g/mol.")),
    )
    agent = make_agent(client, output_dir=tmp_path)
    assert agent.ask("What is the MW of ethanol?") == "Ethanol weighs 46.07 g/mol."

    results = agent.messages[2]["content"]
    assert [r["tool_use_id"] for r in results] == ["t1", "t2"]  # order kept, one message
    assert "C2H6O" in results[0]["content"] and "is_error" not in results[0]
    assert results[1]["is_error"] and "expected string" in results[1]["content"]
    assert agent.events.calls == ["molecule_properties"]  # invalid call never ran

    req = client.requests[0]
    assert req["model"] == "claude-opus-5"
    assert req["fallbacks"] == "default" and req["betas"] == [FALLBACK_BETA]
    assert req["thinking"]["type"] == "adaptive"
    assert {t["name"] for t in req["tools"]} >= {"molecule_properties", "web_search", "web_fetch"}
    assert agent.usage["input_tokens"] == 20


def test_pause_turn_resumes_without_extra_user_message():
    client = FakeClient(message("pause_turn", text("Searching...")), message("end_turn", text("Done.")))
    agent = make_agent(client)
    assert agent.ask("Find reviews on MOFs") == "Done."
    second = client.requests[1]["messages"]
    assert [m["role"] for m in second] == ["user", "assistant"]


def test_refusal_rolls_back_the_turn():
    client = FakeClient(message("end_turn", text("Hi.")), message("refusal"))
    agent = make_agent(client)
    agent.ask("hello")
    with pytest.raises(RefusalError):
        agent.ask("something declined")
    assert len(agent.messages) == 2  # only the first exchange remains


def test_api_error_rolls_back_the_turn():
    class Boom(FakeClient):
        def _stream(self, **kwargs):
            raise RuntimeError("network down")

    agent = make_agent(Boom())
    with pytest.raises(RuntimeError):
        agent.ask("hello")
    assert agent.messages == []


def test_truncated_tool_input_is_not_executed():
    client = FakeClient(
        message("max_tokens", tool_use("t1", "molecule_properties", {"smiles": ["CC"]})),
        message("end_turn", text("ok")),
    )
    agent = make_agent(client)
    agent.ask("q")
    [result] = agent.messages[2]["content"]
    assert result["is_error"] and "cut off" in result["content"]
    assert agent.events.calls == []


def test_no_fallbacks_for_other_models():
    client = FakeClient(message("end_turn", text("ok")))
    ResearchAgent(client=client, model="claude-sonnet-5").ask("q")
    assert "fallbacks" not in client.requests[0]


def test_no_web_tools():
    client = FakeClient(message("end_turn", text("ok")))
    make_agent(client, web=False).ask("q")
    assert not any(t["name"].startswith("web_") for t in client.requests[0]["tools"])


def test_validate():
    schema = {"type": "object", "properties": {"smiles": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                                               "n": {"type": "integer", "maximum": 5}},
              "required": ["smiles"], "additionalProperties": False}
    assert validate(schema, {"smiles": ["C"], "n": 3}) == []
    assert validate(schema, {"smiles": []}) == ["smiles: needs at least 1 item(s)"]
    assert validate(schema, {"smiles": [1], "n": True, "x": 0}) == [
        "smiles[0]: expected string, got int", "n: expected integer, got bool", "unknown field 'x'"]
    assert validate(schema, "C") == ["input must be an object, got str"]


def test_tool_schemas_are_well_formed():
    agent = make_agent(FakeClient())
    for param in agent.tool_params:
        if "input_schema" in param:
            assert param["input_schema"]["additionalProperties"] is False
            assert set(param["input_schema"]["required"]) <= set(param["input_schema"]["properties"])
            assert param["eager_input_streaming"] is True
    assert molecule_properties(["O"])[0]["formula"] == "H2O"
