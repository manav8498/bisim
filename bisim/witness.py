"""The witness ledger: human-approved inputs and expected observations."""
from __future__ import annotations

import datetime
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .canon import canon, dumps, uncanon
from .probes import Probe

FORMAT = "bisim-witness/1"


@dataclass
class Witness:
    args: list  # canonical form of the argument list
    expect: dict  # observation record
    note: str = ""
    source: str = "manual"  # "manual" | "mint"
    at: str = ""


@dataclass
class Ledger:
    function: str
    signature: str = ""
    witnesses: list[Witness] = field(default_factory=list)
    suggested: list[list] = field(default_factory=list)  # canonical arg lists, model-proposed, not approved
    excluded: list[dict] = field(default_factory=list)  # {"args": canonical, "reason": str}


def ledger_path(root, module_path: str, fn: str) -> Path:
    root = Path(root).resolve()
    mp = Path(module_path).resolve()
    try:
        rel = mp.relative_to(root)
    except ValueError:
        rel = Path("_external") / mp.name
    return root / ".bisim" / "witness" / rel.with_suffix("") / f"{fn}.json"


def load_ledger(root, module_path: str, fn: str, spec=None) -> Ledger:
    p = ledger_path(root, module_path, fn)
    if not p.exists():
        return Ledger(fn, spec.signature_str() if spec else "")
    d = json.loads(p.read_text(encoding="utf-8"))
    return Ledger(
        d["function"],
        d.get("signature", ""),
        [Witness(**w) for w in d.get("witnesses", [])],
        d.get("suggested", []),
        d.get("excluded", []),
    )


def save_ledger(root, module_path: str, fn: str, ledger: Ledger) -> Path:
    p = ledger_path(root, module_path, fn)
    p.parent.mkdir(parents=True, exist_ok=True)
    d = {
        "format": FORMAT,
        "function": ledger.function,
        "signature": ledger.signature,
        "witnesses": [asdict(w) for w in ledger.witnesses],
        "suggested": ledger.suggested,
        "excluded": ledger.excluded,
    }
    p.write_text(json.dumps(d, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    return p


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def add_witness(ledger: Ledger, args, expect: dict, note: str = "", source: str = "manual") -> Witness:
    """``args`` may be raw Python values or an already-canonical ``["l", ...]``."""
    c = args if (isinstance(args, list) and args and args[0] == "l" and len(args) == 2) else canon(list(args))
    key = dumps(c)
    ledger.witnesses = [w for w in ledger.witnesses if dumps(w.args) != key]
    w = Witness(c, expect, note, source, _now())
    ledger.witnesses.append(w)
    return w


def ledger_probes(ledger: Ledger, resolver=None) -> list[Probe]:
    """Witness probes first, then suggested; excluded inputs removed; duplicates collapsed."""
    excluded = {dumps(e["args"]) for e in ledger.excluded}
    seen: set[str] = set()
    out: list[Probe] = []
    for w in ledger.witnesses:
        k = dumps(w.args)
        if k in excluded or k in seen:
            continue
        seen.add(k)
        out.append(Probe(tuple(uncanon(w.args, resolver)), "witness"))
    for s in ledger.suggested:
        k = dumps(s)
        if k in excluded or k in seen:
            continue
        seen.add(k)
        out.append(Probe(tuple(uncanon(s, resolver)), "suggested"))
    return out
