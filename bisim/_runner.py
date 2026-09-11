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


class _Effects:
    """Records side effects during one probe. Network and subprocess are recorded *and* blocked."""

    def __init__(self):
        self.items: list[list[str]] = []
        self.cwd = os.getcwd()

    def reset(self, cwd: str):
        self.items = []
        self.cwd = os.path.realpath(cwd)

    def rel(self, path) -> str:
        try:
            p = os.path.realpath(os.fspath(path))
        except TypeError:
            return repr(path)
        if p == self.cwd or p.startswith(self.cwd + os.sep):
            return os.path.relpath(p, self.cwd)
        return p

    def add(self, *fields):
        self.items.append([str(f) for f in fields])

    def snapshot_fs(self):
        """Content hashes of every file left in the scratch cwd, sorted by path."""
        import hashlib

        out = []
        for dirpath, _dirs, files in os.walk(self.cwd):
            for f in files:
                full = os.path.join(dirpath, f)
                try:
                    with _orig_open(full, "rb") as fh:
                        digest = hashlib.sha256(fh.read()).hexdigest()
                except OSError:
                    continue
                out.append(["fs", os.path.relpath(full, self.cwd).replace(os.sep, "/"), digest])
        return sorted(out)


_orig_open = open
EFFECTS = _Effects()


def _install_effect_recorders():
    """Patch the process so file, env, network, and subprocess use is observed."""
    import builtins
    import io
    import socket
    import subprocess

    def rec_open(file, mode="r", *a, **k):
        if isinstance(file, (str, bytes, os.PathLike)):
            EFFECTS.add("open", EFFECTS.rel(file).replace(os.sep, "/"), mode)
        return _orig_open(file, mode, *a, **k)

    builtins.open = rec_open  # type: ignore[assignment]
    io.open = rec_open  # type: ignore[assignment]

    class RecordingEnv(dict):
        def __init__(self, base):
            super().__init__(base)

        def __getitem__(self, k):
            EFFECTS.add("env", k)
            return super().__getitem__(k)

        def get(self, k, default=None):
            EFFECTS.add("env", k)
            return super().get(k, default)

        def __contains__(self, k):
            EFFECTS.add("env", k)
            return super().__contains__(k)

    os.environ = RecordingEnv(os.environ)  # type: ignore[assignment]
    os.getenv = lambda key, default=None: os.environ.get(key, default)  # type: ignore[assignment]

    def blocked_socket(*a, **k):
        EFFECTS.add("net", "socket")
        raise PermissionError("bisim sandbox: network is blocked")

    def blocked_connect(address=None, *a, **k):
        host, port = (address if isinstance(address, tuple) and len(address) == 2 else ("?", "?"))
        EFFECTS.add("net", host, port)
        raise PermissionError("bisim sandbox: network is blocked")

    def blocked_getaddrinfo(host=None, *a, **k):
        EFFECTS.add("net", host, "?")
        raise PermissionError("bisim sandbox: network is blocked")

    socket.socket = blocked_socket  # type: ignore[assignment]
    socket.create_connection = blocked_connect  # type: ignore[assignment]
    socket.getaddrinfo = blocked_getaddrinfo  # type: ignore[assignment]

    def blocked_popen(self, args, *a, **k):
        argv0 = args[0] if isinstance(args, (list, tuple)) and args else str(args).split()[0] if args else "?"
        EFFECTS.add("proc", os.path.basename(str(argv0)))
        raise PermissionError("bisim sandbox: subprocesses are blocked")

    subprocess.Popen.__init__ = blocked_popen  # type: ignore[assignment]

    def blocked_system(cmd):
        EFFECTS.add("proc", str(cmd).split()[0] if cmd else "?")
        raise PermissionError("bisim sandbox: subprocesses are blocked")

    os.system = blocked_system  # type: ignore[assignment]


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


def _state(obj):
    """Observable state: dataclass fields, or public (non-underscore) attributes. Private attributes are
    implementation detail — two classes that differ only in how they name their internals must not
    differ in behavior."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return obj
    d = getattr(obj, "__dict__", None)
    if d is not None:
        return {k: v for k, v in d.items() if not k.startswith("_")}
    slots = getattr(type(obj), "__slots__", ())
    return {s: getattr(obj, s) for s in slots if hasattr(obj, s) and not s.startswith("_")}


def _run_sequence(cls, args, canon):
    """args = (init_args, ((method, margs), ...)). Exceptions are observations; later calls still run,
    because callers do keep using an object after catching an exception."""
    init_args, calls = args
    try:
        obj = cls(*init_args)
    except _Timeout:
        raise
    except BaseException as e:  # noqa: BLE001
        return {"ok": False, "exc": type(e).__name__, "at": "init"}
    steps = []
    for m, margs in calls:
        try:
            steps.append({"ok": True, "value": canon(getattr(obj, m)(*margs))})
        except _Timeout:
            raise
        except BaseException as e:  # noqa: BLE001
            steps.append({"ok": False, "exc": type(e).__name__})
    return {"ok": True, "steps": steps, "state": canon(_state(obj))}


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
    cls = None
    if job.get("class"):
        cls = getattr(mod, job["class"], None)
        if not isinstance(cls, type):
            out.write(json.dumps({"fatal": f"class {job['class']} not found in {job['module']}"}) + "\n")
            out.flush()
            return
        fn = None
    else:
        fn = getattr(mod, job["func"], None)
        if not callable(fn):
            out.write(json.dumps({"fatal": f"function {job['func']} not found in {job['module']}"}) + "\n")
            out.flush()
            return

    _install_effect_recorders()
    _limit_memory()
    signal.signal(signal.SIGALRM, _on_alarm)

    def resolver(qualname: str):
        return getattr(mod, qualname.split(".")[-1])

    want_cov = bool(job.get("coverage"))
    target_file = os.path.abspath(job["module"])

    def tracer_for(hits: set, arcs: set):
        prev: dict = {}

        def trace(frame, event, arg):
            if frame.f_code.co_filename != target_file:
                return None
            if event == "call":
                prev[frame] = None
            elif event == "line":
                hits.add(frame.f_lineno)
                arcs.add((prev.get(frame), frame.f_lineno))
                prev[frame] = frame.f_lineno
            elif event == "return":
                arcs.add((prev.get(frame), -1))
                prev.pop(frame, None)
            return trace
        return trace

    for p in job["probes"]:
        buf = io.StringIO()
        hits: set = set()
        arcs: set = set()
        cwd = tempfile.mkdtemp(prefix="bisim-")  # fresh scratch directory per probe: no state leaks between probes
        os.chdir(cwd)
        EFFECTS.reset(cwd)
        try:
            args = uncanon(p, resolver)
            signal.setitimer(signal.ITIMER_REAL, job["timeout"])
            try:
                if want_cov:
                    sys.settrace(tracer_for(hits, arcs))
                with contextlib.redirect_stdout(buf):
                    if cls is None:
                        rec = {"ok": True, "value": canon(fn(*args))}
                    else:
                        rec = _run_sequence(cls, args, canon)
            finally:
                sys.settrace(None)
                signal.setitimer(signal.ITIMER_REAL, 0)
        except _Timeout:
            rec = {"timeout": True}
        except BaseException as e:  # noqa: BLE001 - an exception *is* the observation
            rec = {"ok": False, "exc": type(e).__name__}
        if buf.getvalue() and "timeout" not in rec:
            rec["out"] = buf.getvalue()
        effects = EFFECTS.items + EFFECTS.snapshot_fs()
        if effects and "timeout" not in rec:
            rec["effects"] = effects
        if want_cov:
            rec["cov"] = sorted(hits)
            rec["arcs"] = sorted([a if a is not None else -2, b] for a, b in arcs)
        out.write(json.dumps(rec, sort_keys=True) + "\n")
        out.flush()


if __name__ == "__main__":
    main()
