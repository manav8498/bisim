import json
import socket
import threading

import pytest

from bisim.cli import main
from bisim.core import hash_target
from bisim.registry import RemoteStore, make_server


@pytest.fixture
def server(tmp_path):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    store_dir = tmp_path / "remote"
    srv = make_server("127.0.0.1", port, str(store_dir))
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{port}"
    srv.shutdown()


def test_push_lookup_by_sig(server, tmp_path):
    m = tmp_path / "m.py"
    m.write_text("def f(x: int) -> int:\n    return x + 1\n")
    r = hash_target(str(m), "f", root=tmp_path)
    rs = RemoteStore(server)
    assert rs.health()
    assert rs.lookup(r.manifest.address) is None
    rs.push(r.manifest, r.target.source(), {"name": "f", "intent": "increment"})
    got = rs.lookup(r.manifest.address)
    assert got["source"].startswith("def f") and got["meta"]["intent"] == "increment" and got["manifest"].address == r.manifest.address
    hits = rs.by_sig(r.manifest.sig_hash)
    assert len(hits) == 1 and hits[0]["address"] == r.manifest.address and hits[0]["meta"]["name"] == "f"
    # a second implementation with the same interface
    m2 = tmp_path / "m2.py"
    m2.write_text("def f(x: int) -> int:\n    return x + 2\n")
    r2 = hash_target(str(m2), "f", root=tmp_path)
    rs.push(r2.manifest, r2.target.source(), {"name": "f"})
    assert len(rs.by_sig(r.manifest.sig_hash)) == 2
    assert rs.by_sig("0" * 64) == []


def test_cli_push_lookup_with_config(server, tmp_path, capsys):
    (tmp_path / ".bisim").mkdir()
    (tmp_path / ".bisim" / "config.json").write_text(json.dumps({"registry": server}))
    m = tmp_path / "m.py"
    m.write_text("def g(s: str) -> str:\n    return s.upper()\n")
    code = main(["hash", f"{m}:g", "--push", "--json", "--root", str(tmp_path)])
    addr = json.loads(capsys.readouterr().out)["address"]
    assert code == 0
    # lookup from a different project root with the same registry finds it remotely
    other = tmp_path / "other"
    (other / ".bisim").mkdir(parents=True)
    (other / ".bisim" / "config.json").write_text(json.dumps({"registry": server}))
    code = main(["lookup", addr, "--root", str(other)])
    out = capsys.readouterr().out
    assert code == 0 and "def g" in out and "remote" in out
    code = main(["lookup", "--sig", "def h(s: str) -> str", "--root", str(other)])
    out = capsys.readouterr().out
    assert code == 0 and addr[:17] in out
    code = main(["lookup", "--sig", "def h(s: bytes) -> str", "--root", str(other)])
    assert code == 4


def test_mint_reuses_registry_candidates(server, tmp_path):
    from bisim.extract import parse_signature
    from bisim.mint import Generation, OracleAnswerer, mint

    MEAN = "def median(xs: list[float]) -> float:\n    if not xs:\n        raise ValueError('empty')\n    s = sorted(xs); n = len(s)\n    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2\n"
    LOWER = "def median(xs: list[float]) -> float:\n    if not xs:\n        raise ValueError('empty')\n    s = sorted(xs)\n    return s[(len(s) - 1) // 2]\n"
    # someone already witnessed and pushed the right implementation
    m = tmp_path / "ref.py"
    m.write_text(MEAN)
    r = hash_target(str(m), "median", root=tmp_path)
    RemoteStore(server).push(r.manifest, MEAN, {"name": "median", "intent": "median of a list"})

    class WeakModel:
        def __init__(self):
            self.calls = 0

        def generate(self, *a):
            self.calls += 1
            return Generation([LOWER], [], [[[1.0, 2.0, 3.0, 4.0]]])

    spec = parse_signature("def median(xs: list[float]) -> float")
    out = tmp_path / "median.py"
    res = mint("median", spec, WeakModel(), OracleAnswerer(MEAN, "median"), tmp_path, str(out), registry=RemoteStore(server))
    assert out.read_text() == MEAN and res.reused >= 1
