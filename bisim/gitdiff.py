"""Pair functions between a git base revision and the working tree."""
from __future__ import annotations

import os
import subprocess
import tempfile
from dataclasses import dataclass

from .extract import extract_functions


class NotARepo(Exception):
    pass


def git(root, *args) -> str:
    try:
        r = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True)
    except FileNotFoundError:
        raise NotARepo("git is not installed")
    except subprocess.CalledProcessError as e:
        raise NotARepo((e.stderr or e.stdout).strip() or "git command failed")
    return r.stdout


def ensure_repo(root) -> None:
    try:
        git(root, "rev-parse", "--is-inside-work-tree")
    except NotARepo:
        raise NotARepo(f"{root} is not inside a git repository")


def changed_python_files(root, base: str = "HEAD") -> list[str]:
    """Relative paths of .py files modified, added, or untracked vs ``base``."""
    ensure_repo(root)
    out = git(root, "diff", "--name-only", "--diff-filter=AM", base, "--", "*.py")
    untracked = git(root, "ls-files", "--others", "--exclude-standard", "--", "*.py")
    seen, files = set(), []
    for line in (out + untracked).splitlines():
        line = line.strip()
        if line and line not in seen and os.path.exists(os.path.join(root, line)):
            seen.add(line)
            files.append(line)
    return files


def file_at_revision(root, rel: str, base: str = "HEAD") -> str | None:
    try:
        return git(root, "show", f"{base}:{rel}")
    except NotARepo:
        return None


@dataclass
class Pair:
    rel: str
    name: str
    old_path: str | None  # temp file holding the base version, or None if added
    new_path: str | None  # working-tree file, or None if removed


def function_pairs(root, base: str = "HEAD") -> list[Pair]:
    root = str(root)
    pairs: list[Pair] = []
    tmpdir = tempfile.mkdtemp(prefix="bisim-base-")
    for rel in changed_python_files(root, base):
        new_path = os.path.join(root, rel)
        old_src = file_at_revision(root, rel, base)
        old_path = None
        old_names: dict[str, bool] = {}
        if old_src is not None:
            old_path = os.path.join(tmpdir, rel.replace(os.sep, "__"))
            with open(old_path, "w", encoding="utf-8") as fh:
                fh.write(old_src)
            old_names = {s.name: True for s in _safe_extract(old_path)}
        new_names = {s.name: True for s in _safe_extract(new_path)}
        for name in new_names:
            pairs.append(Pair(rel, name, old_path if name in old_names else None, new_path))
        for name in old_names:
            if name not in new_names:
                pairs.append(Pair(rel, name, old_path, None))
    return pairs


def _safe_extract(path: str):
    try:
        return extract_functions(path)
    except SyntaxError:
        return []
