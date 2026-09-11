"""Sandbox child process.

Reads one JSON job from stdin and writes one JSON line per probe to stdout.
Runs with the target module imported in-process, network blocked, cwd moved to
a fresh temp dir, a memory ceiling, and a per-probe wall-clock timeout.
Nothing in here is imported by the parent.
"""
from __future__ import annotations

import contextlib
import dataclasses
import importlib.util
import io
import json
import os
import signal
import sys
import tempfile


def _block_network():
    import socket

    def blocked(*a, **k):
        raise PermissionError("bisim sandbox: network is blocked")

    socket.socket = blocked  # type: ignore[assignment]
    socket.create_connection = blocked  # type: ignore[assignment]
    socket.getaddrinfo = blocked  # type: ignore[assignment]


def _limit_memory(limit_mb: int = 512):
    try:
        import resource

        lim = limit_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (lim, lim))
    except Exception:
        pass  # best-effort; not enforced on every platform


class _Timeout(BaseException):
    """Raised by the alarm handler. BaseException so user ``except Exception`` can't swallow it."""


def _on_alarm(signum, frame):
    raise _Timeout()


def _load(module_path: str):
    d = os.path.dirname(os.path.abspath(module_path))
    if d not in sys.path:
        sys.path.insert(0, d)
    name = "_bisim_target_" + os.urandom(4).hex()
    spec = importlib.util.spec_from_file_location(name, module_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _type_str(t) -> str:
    if isinstance(t, str):
        return t
    if getattr(t, "__module__", None) == "builtins" and hasattr(t, "__name__"):
        return t.__name__
    s = str(t)
    return s.replace("typing.", "")


def _introspect(mod, names):
    res = {}
    for n in names:
        obj = getattr(mod, n, None)
        if isinstance(obj, type) and dataclasses.is_dataclass(obj):
            res[n] = [[f.name, _type_str(f.type)] for f in dataclasses.fields(obj)]
        else:
            res[n] = None
    return res


def main():
    from bisim.canon import canon, uncanon

    job = json.loads(sys.stdin.read())
    out = sys.stdout
    sys.stdout = io.StringIO()  # module-level prints must not corrupt the protocol
    try:
        mod = _load(job["module"])
    except BaseException as e:  # noqa: BLE001 - report anything, including SystemExit at import
        out.write(json.dumps({"fatal": f"import failed: {type(e).__name__}: {e}"}) + "\n")
        out.flush()
        return
    if job.get("op") == "introspect":
        out.write(json.dumps({"introspect": _introspect(mod, job["names"])}) + "\n")
        out.flush()
        return
    fn = getattr(mod, job["func"], None)
    if not callable(fn):
        out.write(json.dumps({"fatal": f"function {job['func']} not found in {job['module']}"}) + "\n")
        out.flush()
        return

    _block_network()
    _limit_memory()
    os.chdir(tempfile.mkdtemp(prefix="bisim-"))
    signal.signal(signal.SIGALRM, _on_alarm)

    def resolver(qualname: str):
        return getattr(mod, qualname.split(".")[-1])

    want_cov = bool(job.get("coverage"))
    target_file = os.path.abspath(job["module"])

    def tracer_for(hits: set):
        def trace(frame, event, arg):
            if frame.f_code.co_filename != target_file:
                return None
            if event == "line":
                hits.add(frame.f_lineno)
            return trace
        return trace

    for p in job["probes"]:
        buf = io.StringIO()
        hits: set = set()
        try:
            args = uncanon(p, resolver)
            signal.setitimer(signal.ITIMER_REAL, job["timeout"])
            try:
                if want_cov:
                    sys.settrace(tracer_for(hits))
                with contextlib.redirect_stdout(buf):
                    val = fn(*args)
            finally:
                sys.settrace(None)
                signal.setitimer(signal.ITIMER_REAL, 0)
            rec = {"ok": True, "value": canon(val)}
        except _Timeout:
            rec = {"timeout": True}
        except BaseException as e:  # noqa: BLE001 - an exception *is* the observation
            rec = {"ok": False, "exc": type(e).__name__}
        if buf.getvalue() and "timeout" not in rec:
            rec["out"] = buf.getvalue()
        if want_cov:
            rec["cov"] = sorted(hits)
        out.write(json.dumps(rec, sort_keys=True) + "\n")
        out.flush()


if __name__ == "__main__":
    main()
