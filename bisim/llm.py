"""Candidate generation with Claude (optional; ``pip install "bisim[llm]"``)."""
from __future__ import annotations

import json
import re

from .canon import uncanon
from .extract import FunctionSpec
from .fmt import describe_obs
from .mint import Generation
from .witness import Witness

DEFAULT_MODEL = "claude-opus-5"

SYSTEM = """You help discover what a function is *intended* to do by proposing several deliberately
different implementations. Natural-language intent is ambiguous; your job is to make the ambiguities
concrete so a human can settle them by looking at specific inputs.

Rules for every candidate:
- Complete, standalone Python 3.11+ source. Any imports at the top. The function must have EXACTLY the
  given name and signature (parameter names, type hints, return type).
- Pure and deterministic: no randomness, no time, no I/O, no network, no global state.
- Each candidate must be a *reasonable* reading of the intent, and the candidates must differ in
  observable behavior on some input (e.g. how empty input, ties, negative numbers, unicode, rounding,
  or invalid input are handled). Do not pad with cosmetic variants.
- It is fine for a candidate to raise an exception for inputs it considers invalid.

Respond with ONE JSON object and nothing else:
{"candidates": ["<python source>", ...],
 "notes": ["<one line per ambiguity you exploited>", ...],
 "suggested_args": [[<arg1>, <arg2>, ...], ...]}
"suggested_args" is up to 8 argument lists (JSON values matching the parameter types) on which your
candidates disagree — the inputs most worth asking a human about."""


def build_prompt(intent: str, spec: FunctionSpec, k: int, witnesses: list[Witness]) -> str:
    lines = [
        f"Intent: {intent}",
        f"Signature: def {spec.name}{spec.signature_str()}",
        f"Produce {k} candidates.",
    ]
    if witnesses:
        lines.append("")
        lines.append("Hard constraints — a human has already fixed these behaviors; every candidate MUST satisfy them:")
        for w in witnesses:
            args = tuple(uncanon(w.args))
            note = f"   # {w.note}" if w.note else ""
            lines.append(f"  {spec.name}{args!r} -> {describe_obs(w.expect)}{note}")
    return "\n".join(lines)


def parse_generation(text: str) -> Generation:
    """Extract the JSON object from a model reply (tolerates fences and surrounding prose)."""
    body = text.strip()
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", body, re.S)
    if m:
        body = m.group(1)
    else:
        start, end = body.find("{"), body.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ValueError("model reply contained no JSON object")
        body = body[start : end + 1]
    try:
        d = json.loads(body)
    except json.JSONDecodeError as e:
        raise ValueError(f"model reply was not valid JSON: {e}")
    if not isinstance(d, dict) or not isinstance(d.get("candidates"), list):
        raise ValueError("model reply must be an object with a 'candidates' list")
    cands = [c for c in d["candidates"] if isinstance(c, str) and c.strip()]
    notes = [n for n in d.get("notes", []) or [] if isinstance(n, str)]
    sugg = [a for a in d.get("suggested_args", []) or [] if isinstance(a, list)]
    return Generation(cands, notes, sugg)


class AnthropicClient:
    """``CandidateClient`` backed by the Anthropic SDK. Pass ``client`` to inject a stub."""

    def __init__(self, model: str | None = None, client=None, max_tokens: int = 16000):
        self.model = model or DEFAULT_MODEL
        self.max_tokens = max_tokens
        if client is None:
            try:
                import anthropic
            except ImportError:
                raise RuntimeError('mint needs the Anthropic SDK: pip install "bisim[llm]"')
            client = anthropic.Anthropic()
        self.client = client

    def complete(self, system: str, prompt: str) -> str:
        with self.client.messages.stream(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            msg = stream.get_final_message()
        if msg.stop_reason == "refusal":
            detail = getattr(getattr(msg, "stop_details", None), "explanation", None) or ""
            raise RuntimeError(f"the model refused this request {detail}".rstrip())
        return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")

    def generate(self, intent: str, spec: FunctionSpec, k: int, witnesses: list[Witness]) -> Generation:
        return parse_generation(self.complete(SYSTEM, build_prompt(intent, spec, k, witnesses)))


class ClaudeCodeClient:
    """``CandidateClient`` that shells out to a locally installed Claude Code (``claude -p``).

    Useful when no API key is configured but Claude Code is logged in. Tools are disabled and
    no session is persisted, so the call is a plain prompt → text exchange.
    """

    def __init__(self, model: str | None = None, binary: str = "claude", timeout: float = 600.0, runner=None):
        import shutil
        import subprocess

        self.model = model or DEFAULT_MODEL
        self.binary = shutil.which(binary) or binary
        self.timeout = timeout
        self._run = runner or subprocess.run

    def complete(self, system: str, prompt: str) -> str:
        import subprocess

        cmd = [
            self.binary, "-p", "--no-session-persistence", "--output-format", "text",
            "--tools", "", "--model", self.model, "--system-prompt", system, prompt,
        ]
        try:
            proc = self._run(cmd, capture_output=True, text=True, timeout=self.timeout, stdin=subprocess.DEVNULL)
        except FileNotFoundError:
            raise RuntimeError("Claude Code (`claude`) is not installed or not on PATH")
        if proc.returncode != 0:
            raise RuntimeError(f"claude -p failed (rc={proc.returncode}): {(proc.stderr or proc.stdout)[-500:]}")
        return proc.stdout

    def generate(self, intent: str, spec: FunctionSpec, k: int, witnesses: list[Witness]) -> Generation:
        return parse_generation(self.complete(SYSTEM, build_prompt(intent, spec, k, witnesses)))


def default_client(model: str | None = None, prefer: str = "auto"):
    """Pick a candidate client: the SDK when credentials are configured, else Claude Code."""
    import os
    import shutil

    have_sdk_creds = bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
                          or os.path.isdir(os.path.expanduser("~/.config/anthropic")))
    have_claude = shutil.which("claude") is not None
    if prefer == "sdk" or (prefer == "auto" and have_sdk_creds):
        return AnthropicClient(model=model)
    if prefer == "claude-code" or (prefer == "auto" and have_claude):
        return ClaudeCodeClient(model=model)
    raise RuntimeError(
        "no way to reach a model: set ANTHROPIC_API_KEY (pip install \"bisim[llm]\") or install Claude Code (`claude`)"
    )
