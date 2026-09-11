import pytest

from bisim.extract import parse_signature
from bisim.llm import AnthropicClient, build_prompt, parse_generation
from bisim.witness import Witness


def test_parse_generation_tolerates_fences_and_prose():
    txt = (
        "Here you go:\n```json\n"
        '{"candidates": ["def f(x: int) -> int:\\n    return x"], "notes": ["n"], "suggested_args": [[0], [1]]}'
        "\n```\nDone."
    )
    g = parse_generation(txt)
    assert g.candidates == ["def f(x: int) -> int:\n    return x"] and g.suggested_args == [[0], [1]] and g.notes == ["n"]


def test_parse_generation_drops_non_string_candidates_and_missing_keys():
    g = parse_generation('{"candidates": ["def f(): pass", 3, null]}')
    assert g.candidates == ["def f(): pass"] and g.notes == [] and g.suggested_args == []


def test_parse_generation_bad_json_raises():
    with pytest.raises(ValueError):
        parse_generation("nope")
    with pytest.raises(ValueError):
        parse_generation('{"candidates": "not a list"}')


def test_build_prompt_includes_constraints():
    spec = parse_signature("def median(xs: list[float]) -> float")
    w = [Witness(["l", [["l", [["f", "1.0"], ["f", "2.0"]]]]], {"ok": True, "value": ["f", "1.5"]}, "avg")]
    p = build_prompt("median of a list", spec, 4, w)
    assert "def median(xs: list[float]) -> float" in p and "median of a list" in p
    assert "([1.0, 2.0],)" in p and "1.5" in p and "4" in p


class _Block:
    def __init__(self, type_, text=""):
        self.type, self.text = type_, text


class _Msg:
    def __init__(self, text, stop_reason="end_turn"):
        self.content = [_Block("thinking"), _Block("text", text)]
        self.stop_reason = stop_reason
        self.stop_details = None


class _Stream:
    def __init__(self, msg):
        self.msg = msg

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        return self.msg


class _Messages:
    def __init__(self, msg):
        self.msg, self.kwargs = msg, None

    def stream(self, **kwargs):
        self.kwargs = kwargs
        return _Stream(self.msg)


class _SDK:
    def __init__(self, msg):
        self.messages = _Messages(msg)


def test_anthropic_client_generate_uses_stream_and_parses():
    sdk = _SDK(_Msg('{"candidates": ["def f(x: int) -> int:\\n    return x + 1"], "notes": [], "suggested_args": [[7]]}'))
    c = AnthropicClient(client=sdk)
    g = c.generate("increment", parse_signature("def f(x: int) -> int"), 3, [])
    assert g.candidates[0].endswith("x + 1") and g.suggested_args == [[7]]
    assert sdk.messages.kwargs["model"] == "claude-opus-5" and sdk.messages.kwargs["max_tokens"] >= 8000
    assert "increment" in sdk.messages.kwargs["messages"][0]["content"]


def test_anthropic_client_refusal_is_an_error():
    sdk = _SDK(_Msg("", stop_reason="refusal"))
    with pytest.raises(RuntimeError, match="refus"):
        AnthropicClient(client=sdk).generate("x", parse_signature("def f(x: int) -> int"), 3, [])
