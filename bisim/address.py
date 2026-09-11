"""The behavioral address: a Merkle root over (probe, observation) pairs."""
from __future__ import annotations

import platform
from dataclasses import asdict, dataclass

from .canon import canon, dumps, is_opaque, sha256_hex
from .extract import FunctionSpec
from .probes import PROBEGEN_VERSION, Probe, sig_hash  # re-exported

__all__ = [
    "FORMAT", "PREFIX", "sig_hash", "obs_hash", "merkle_root", "compute_address",
    "ProbeRecord", "Manifest", "build_manifest", "build_manifest_for", "coverage_summary",
]

FORMAT = "bisim-manifest/1"
PREFIX = "bsm1:"


def obs_hash(record: dict) -> str:
    return sha256_hex(dumps(record))


def merkle_root(leaves: list[str]) -> str:
    """Order-independent Merkle root (leaves are sorted first)."""
    level = sorted(leaves)
    if not level:
        return sha256_hex("")
    while len(level) > 1:
        if len(level) % 2:
            level.append(level[-1])
        level = [sha256_hex(level[i] + level[i + 1]) for i in range(0, len(level), 2)]
    return level[0]


def compute_address(sig: str, root: str, probegen: str = PROBEGEN_VERSION) -> str:
    return PREFIX + sha256_hex("bisim/1" + "|" + probegen + "|" + sig + "|" + root)


@dataclass
class ProbeRecord:
    id: str
    kind: str
    args: list  # canonical form
    obs: dict
    obs_hash: str
    opaque: bool = False


@dataclass
class Manifest:
    format: str
    probegen: str
    runtime: dict
    function: dict
    sig_hash: str
    probes: list[ProbeRecord]
    root: str
    address: str
    coverage: dict | None = None  # diagnostic only — never part of the address

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "Manifest":
        d = dict(d)
        d["probes"] = [ProbeRecord(**p) for p in d["probes"]]
        d.setdefault("coverage", None)
        return Manifest(**d)

    def short(self) -> str:
        return self.address[: len(PREFIX) + 12]

    def by_id(self) -> dict[str, ProbeRecord]:
        return {p.id: p for p in self.probes}

    def counts(self) -> dict[str, int]:
        out = {"type": 0, "witness": 0, "suggested": 0}
        for p in self.probes:
            out[p.kind] = out.get(p.kind, 0) + 1
        return out


def coverage_summary(spec, cov: list[set[int]]) -> dict | None:
    """Line coverage of the target body by the probe set. ``None`` if the spec has no line info.
    ``spec`` may be anything with a ``lines`` attribute, or a plain list of lines."""
    lines = spec if isinstance(spec, list) else getattr(spec, "lines", [])
    if not lines:
        return None
    hit_all: set[int] = set()
    for c in cov:
        hit_all |= c
    executable = list(lines)
    hit = sorted(l for l in executable if l in hit_all)
    missed = sorted(l for l in executable if l not in hit_all)
    pct = round(100.0 * len(hit) / len(executable), 1) if executable else 100.0
    return {"executable": executable, "hit": hit, "missed": missed, "pct": pct}


def _obs_opaque(o: dict) -> bool:
    if "value" in o and is_opaque(o["value"]):
        return True
    if "state" in o and is_opaque(o["state"]):
        return True
    return any(is_opaque(st.get("value", [])) for st in o.get("steps", []))


def build_manifest_for(target: dict, sig: str, probes: list[Probe], observations: list[dict], coverage: dict | None = None) -> Manifest:
    """``target`` describes what was probed (kind, name, params …); ``sig`` is its interface hash."""
    if len(probes) != len(observations):
        raise ValueError("probes and observations differ in length")
    recs = []
    for p, o in zip(probes, observations):
        recs.append(ProbeRecord(p.id, p.kind, canon(list(p.args)), o, obs_hash(o), _obs_opaque(o)))
    leaves = [sha256_hex(r.id + r.obs_hash) for r in recs]
    root = merkle_root(leaves)
    return Manifest(FORMAT, PROBEGEN_VERSION, {"python": platform.python_version()}, target, sig, recs, root,
                    compute_address(sig, root), coverage)


def build_manifest(spec: FunctionSpec, probes: list[Probe], observations: list[dict], coverage: dict | None = None) -> Manifest:
    target = {"kind": "function", "name": spec.name, "params": [list(p) for p in spec.params], "returns": spec.returns}
    return build_manifest_for(target, sig_hash(spec), probes, observations, coverage)
