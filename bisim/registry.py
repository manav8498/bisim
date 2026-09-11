"""A shared registry of witnessed implementations, addressed by behavior.

Server: ``bisim serve --dir DIR --port N`` — stdlib ``http.server``, JSON over HTTP, no auth (put it
behind whatever your team already uses). Objects are stored exactly like the local store
(``<dir>/<address>/{manifest.json,source.py,meta.json}``) plus an index by interface hash so an agent
can ask "what implementations of this signature have been witnessed?" before generating a new one.

Client: ``RemoteStore(url)`` with ``lookup``, ``push``, ``by_sig``, ``health``.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .address import PREFIX, Manifest

_ADDR = re.compile(r"^bsm1:[0-9a-f]{64}$")
_HEX = re.compile(r"^[0-9a-f]{64}$")


# --- storage shared by server and local mirror ------------------------------

def _obj_dir(root: Path, address: str) -> Path:
    return root / address.removeprefix(PREFIX)


def store_object(root: Path, manifest: dict, source: str, meta: dict) -> None:
    d = _obj_dir(root, manifest["address"])
    d.mkdir(parents=True, exist_ok=True)
    (d / "manifest.json").write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    (d / "source.py").write_text(source.rstrip() + "\n", encoding="utf-8")
    (d / "meta.json").write_text(json.dumps(meta, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    idx_path = root / "by_sig.json"
    idx = json.loads(idx_path.read_text()) if idx_path.exists() else {}
    entries = idx.setdefault(manifest["sig_hash"], [])
    if manifest["address"] not in entries:
        entries.append(manifest["address"])
    idx_path.write_text(json.dumps(idx, indent=1, sort_keys=True) + "\n", encoding="utf-8")


def load_object(root: Path, address: str) -> dict | None:
    d = _obj_dir(root, address)
    if not (d / "manifest.json").exists():
        return None
    return {
        "manifest": json.loads((d / "manifest.json").read_text(encoding="utf-8")),
        "source": (d / "source.py").read_text(encoding="utf-8"),
        "meta": json.loads((d / "meta.json").read_text(encoding="utf-8")),
    }


def addresses_for_sig(root: Path, sig: str) -> list[str]:
    idx_path = root / "by_sig.json"
    if not idx_path.exists():
        return []
    return list(json.loads(idx_path.read_text()).get(sig, []))


# --- server -------------------------------------------------------------------

def make_server(host: str, port: int, store_dir: str) -> ThreadingHTTPServer:
    root = Path(store_dir)
    root.mkdir(parents=True, exist_ok=True)

    class Handler(BaseHTTPRequestHandler):
        server_version = "bisim-registry/1"

        def log_message(self, fmt, *args):  # quiet
            pass

        def _json(self, code: int, obj) -> None:
            body = json.dumps(obj, sort_keys=True).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/health":
                return self._json(200, {"ok": True, "objects": sum(1 for p in root.iterdir() if p.is_dir())})
            m = re.match(r"^/addr/(bsm1:[0-9a-f]{64})$", self.path)
            if m:
                obj = load_object(root, m.group(1))
                return self._json(200, obj) if obj else self._json(404, {"error": "not found"})
            m = re.match(r"^/sig/([0-9a-f]{64})$", self.path)
            if m:
                out = []
                for a in addresses_for_sig(root, m.group(1)):
                    obj = load_object(root, a)
                    if obj:
                        out.append({"address": a, "meta": obj["meta"], "target": obj["manifest"].get("function"),
                                    "witness_count": sum(1 for p in obj["manifest"]["probes"] if p["kind"] == "witness")})
                return self._json(200, {"implementations": out})
            return self._json(404, {"error": "unknown route"})

        def do_PUT(self):
            m = re.match(r"^/addr/(bsm1:[0-9a-f]{64})$", self.path)
            if not m:
                return self._json(404, {"error": "unknown route"})
            n = int(self.headers.get("Content-Length", "0"))
            try:
                obj = json.loads(self.rfile.read(n).decode("utf-8"))
                manifest, source, meta = obj["manifest"], obj["source"], obj.get("meta", {})
                if manifest.get("address") != m.group(1) or not isinstance(source, str):
                    return self._json(400, {"error": "address mismatch or bad body"})
            except (ValueError, KeyError, TypeError) as e:
                return self._json(400, {"error": f"bad body: {e}"})
            store_object(root, manifest, source, meta)
            return self._json(200, {"ok": True, "address": manifest["address"]})

    return ThreadingHTTPServer((host, port), Handler)


# --- client -------------------------------------------------------------------

class RegistryError(Exception):
    pass


class RemoteStore:
    def __init__(self, url: str, timeout: float = 10.0):
        self.url = url.rstrip("/")
        self.timeout = timeout

    def _req(self, method: str, path: str, body=None):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(self.url + path, data=data, method=method, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            raise RegistryError(f"registry {method} {path}: HTTP {e.code}")
        except (urllib.error.URLError, OSError) as e:
            raise RegistryError(f"registry unreachable at {self.url}: {e}")

    def health(self) -> bool:
        try:
            return bool((self._req("GET", "/health") or {}).get("ok"))
        except RegistryError:
            return False

    def lookup(self, address: str) -> dict | None:
        if not _ADDR.match(address):
            raise RegistryError(f"not an address: {address}")
        obj = self._req("GET", f"/addr/{address}")
        if obj is None:
            return None
        return {"manifest": Manifest.from_dict(obj["manifest"]), "source": obj["source"], "meta": obj["meta"]}

    def push(self, manifest: Manifest, source: str, meta: dict) -> None:
        self._req("PUT", f"/addr/{manifest.address}", {"manifest": manifest.to_dict(), "source": source, "meta": meta})

    def by_sig(self, sig: str) -> list[dict]:
        if not _HEX.match(sig):
            raise RegistryError(f"not an interface hash: {sig}")
        return list((self._req("GET", f"/sig/{sig}") or {}).get("implementations", []))


# --- config -------------------------------------------------------------------

def load_config(root) -> dict:
    p = Path(root) / ".bisim" / "config.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except ValueError:
        return {}


def registry_for(root, override: str | None = None) -> RemoteStore | None:
    url = override or os.environ.get("BISIM_REGISTRY") or load_config(root).get("registry")
    return RemoteStore(url) if url else None
