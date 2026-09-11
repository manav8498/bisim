"""Local content-addressed registry of witnessed implementations."""
from __future__ import annotations

import json
from pathlib import Path

from .address import PREFIX, Manifest


def store_path(root, address: str) -> Path:
    return Path(root) / ".bisim" / "store" / address.removeprefix(PREFIX)


def push(root, manifest: Manifest, source: str, meta: dict) -> Path:
    d = store_path(root, manifest.address)
    d.mkdir(parents=True, exist_ok=True)
    (d / "manifest.json").write_text(json.dumps(manifest.to_dict(), indent=1, sort_keys=True) + "\n", encoding="utf-8")
    (d / "source.py").write_text(source.rstrip() + "\n", encoding="utf-8")
    (d / "meta.json").write_text(json.dumps(meta, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return d


def lookup(root, address: str):
    d = store_path(root, address)
    if not (d / "manifest.json").exists():
        return None
    return {
        "manifest": Manifest.from_dict(json.loads((d / "manifest.json").read_text(encoding="utf-8"))),
        "source": (d / "source.py").read_text(encoding="utf-8"),
        "meta": json.loads((d / "meta.json").read_text(encoding="utf-8")),
    }
