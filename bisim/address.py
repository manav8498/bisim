"""The behavioral address: a Merkle root over (probe, observation) pairs."""
from __future__ import annotations

import platform
from dataclasses import asdict, dataclass

from .canon import canon, dumps, is_opaque, sha256_hex
from .extract import FunctionSpec
from .probes import PROBEGEN_VERSION, Probe, sig_hash  # re-exported

__all__ = [
    "FORMAT", "PREFIX", "sig_hash", "obs_hash", "merkle_root", "compute_address",
    "ProbeRecord", "Manifest", "build_manifest",
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


def coverage_summary(spec: FunctionSpec, cov: list[set[int]]) -> dict | None:
    """Line coverage of the function body by the probe set. ``None`` if the spec has no line info."""
    if not spec.lines:
        return None
    hit_all: set[int] = set()
    for c in cov:
        hit_all |= c
    executable = list(spec.lines)
    hit = sorted(l for l in executable if l in hit_all)
    missed = sorted(l for l in executable if l not in hit_all)
    pct = round(100.0 * len(hit) / len(executable), 1) if executable else 100.0
    return {"executable": executable, "hit": hit, "missed": missed, "pct": pct}


def build_manifest(spec: FunctionSpec, probes: list[Probe], observations: list[dict], coverage: dict | None = None) -> Manifest:
    if len(probes) != len(observations):
        raise ValueError("probes and observations differ in length")
    recs = []
    for p, o in zip(probes, observations):
        recs.append(ProbeRecord(p.id, p.kind, canon(list(p.args)), o, obs_hash(o), is_opaque(o.get("value", []))))
    leaves = [sha256_hex(r.id + r.obs_hash) for r in recs]
    sh = sig_hash(spec)
    root = merkle_root(leaves)
    return Manifest(
        FORMAT,
        PROBEGEN_VERSION,
        {"python": platform.python_version()},
        {"name": spec.name, "params": [list(p) for p in spec.params], "returns": spec.returns},
        sh,
        recs,
        root,
        compute_address(sh, root),
        coverage,
    )
