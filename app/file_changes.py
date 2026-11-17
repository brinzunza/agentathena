"""
Which files a reply created or changed, and what changed in them.

The agent writes code in its reply and then saves it (save_code) or runs it (run_python). Rather than show
all that code as it is written, the browser says it is building and, at the end, lists the files that
were touched, each with a diff to look at. This module works out that list.

A `FileChanges` lives for one reply. Each tool reports what a file held before and after it ran; for the same file
touched twice, the first "before" and the last "after" are kept, so the diff is what the whole reply did to it.

Only text is diffed. A file that isn't text, or is too big to compare, is still listed, without a diff.
Code run by run_python is watched through the chat's workspace folder, and through any place in the user's home folder
that the code names as plain text (see paths_written_in). A path the code only builds while it runs can't be seen.
"""

import difflib
import os
import re
from typing import Dict, List, Optional, Tuple

# What is stored with a reply, so a long edit doesn't make the saved chat huge
MAX_DIFF_CHARS = 30_000
MAX_TOTAL_DIFF_CHARS = 150_000
MAX_FILES = 30

# Limits on reading a workspace to compare it before and after code runs
MAX_SNAPSHOT_FILES = 300
MAX_SNAPSHOT_FILE_BYTES = 200_000
MAX_SNAPSHOT_TOTAL_BYTES = 4_000_000
MAX_WALKED = 3000          # files looked at in total, so watching a big folder stays quick
_SKIPPED_FOLDERS = {"__pycache__", "node_modules", "venv", ".venv", "env"}

CREATED, MODIFIED, DELETED = "created", "modified", "deleted"

# Stand-ins for a file's text when there is no text to compare
BINARY = object()
TOO_LARGE = object()

Snapshot = Dict[str, Tuple[object, Tuple[int, int]]]    # path -> (text or a stand-in, (size, modified time))


def read_text(path: str):
    """A file's text, or BINARY / TOO_LARGE when it has none worth comparing, or None when it isn't there."""
    try:
        size = os.path.getsize(path)
        if size > MAX_SNAPSHOT_FILE_BYTES:
            return TOO_LARGE
        with open(path, "rb") as f:
            raw = f.read()
    except OSError:
        return None
    if b"\0" in raw:
        return BINARY
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return BINARY


def _add_folder(found: Snapshot, folder: str, budget: list) -> None:
    walked = 0
    for here, names, files in os.walk(folder):
        names[:] = sorted(name for name in names if not name.startswith(".") and name not in _SKIPPED_FOLDERS)
        for name in sorted(files):
            walked += 1
            if walked > MAX_WALKED or len(found) >= MAX_SNAPSHOT_FILES:
                return
            if name.startswith((".", "_athena_")):
                continue
            _add_file(found, os.path.join(here, name), budget)


def _add_file(found: Snapshot, path: str, budget: list) -> None:
    try:
        stat = os.stat(path)
    except OSError:
        return
    text = read_text(path) if budget[0] > 0 else TOO_LARGE
    if isinstance(text, str):
        budget[0] -= stat.st_size
    found[path] = (text, (stat.st_size, stat.st_mtime_ns))


def snapshot_workspace(workspace: str) -> Snapshot:
    """What every ordinary file in a folder holds right now, to compare against after code has run."""
    found: Snapshot = {}
    _add_folder(found, workspace, [MAX_SNAPSHOT_TOTAL_BYTES])
    return found


_QUOTED_PATH = re.compile(r"""(["'])((?:~|/)[^"'\n]*)\1""")


def paths_written_in(code: str, home: str) -> List[str]:
    """
    The places in the user's home folder that a piece of code names as plain text, such as "/Users/me/Desktop/app.py".
    run_python runs with its own folder as home, so code that saves to the real Desktop has to spell the path out.
    A path built from parts at run time can't be seen, so the list can miss files; it never invents them.
    """
    found: List[str] = []
    for _, raw in _QUOTED_PATH.findall(code or ""):
        path = os.path.normpath(home + raw[1:] if raw.startswith("~") else raw)
        try:
            inside = os.path.commonpath([path, home]) == home and path != home
        except ValueError:
            inside = False
        relative = os.path.relpath(path, home).split(os.sep) if inside else []
        if inside and len(relative) >= 2 and not any(part.startswith(".") for part in relative) \
                and relative[0] != "Library" and path not in found:
            found.append(path)
    return found[:20]


def snapshot_paths(paths: List[str]) -> Snapshot:
    """The same, for particular files and folders (whichever of them exist right now)."""
    found: Snapshot = {}
    budget = [MAX_SNAPSHOT_TOTAL_BYTES]
    for path in paths:
        if os.path.isdir(path):
            _add_folder(found, path, budget)
        elif os.path.isfile(path):
            _add_file(found, path, budget)
    return found


def _line_count(text: str) -> int:
    return len(text.splitlines())


def diff_of(before: Optional[str], after: Optional[str]) -> Tuple[str, int, int]:
    """The changed lines between two texts as a unified diff without its file header, and how many were added and removed."""
    old = (before or "").splitlines()
    new = (after or "").splitlines()
    lines = list(difflib.unified_diff(old, new, lineterm="", n=3))[2:]       # the first two are the file names
    added = sum(1 for line in lines if line.startswith("+"))
    removed = sum(1 for line in lines if line.startswith("-"))
    return "\n".join(lines), added, removed


def label_for(path: str, workspace: str, home: str) -> str:
    """How a path is shown: inside the chat's workspace by its relative name, inside the home folder as ~/..."""
    def inside(folder):
        try:
            return os.path.commonpath([os.path.realpath(path), os.path.realpath(folder)]) == os.path.realpath(folder)
        except ValueError:
            return False

    if workspace and inside(workspace):
        return os.path.relpath(os.path.realpath(path), os.path.realpath(workspace))
    if home and inside(home):
        return "~/" + os.path.relpath(os.path.realpath(path), os.path.realpath(home))
    return path


class FileChanges:
    """The files one reply has changed so far."""

    def __init__(self):
        self._files: Dict[str, dict] = {}       # path -> {label, before, after}; the order they were first touched
        self._version = 0
        self._reported = 0

    def record(self, path: str, label: str, before, after) -> None:
        """A file went from `before` to `after` (text, a stand-in, or None where it didn't exist / no longer does)."""
        entry = self._files.get(path)
        if entry is None:
            self._files[path] = {"label": label, "before": before, "after": after}
        else:
            entry["after"] = after
        self._version += 1

    def record_workspace(self, workspace: str, home: str, before: Snapshot, after: Snapshot) -> None:
        """Whatever differs between two snapshots of the workspace."""
        for path in sorted(set(before) | set(after)):
            was, now = before.get(path), after.get(path)
            if was and now and was[1] == now[1]:
                continue            # not touched
            self.record(path, label_for(path, workspace, home), was[0] if was else None, now[0] if now else None)

    def summary(self) -> List[dict]:
        """The list shown to the user: one entry per file that really ended up different from where it started."""
        shown: List[dict] = []
        spent = 0
        for entry in self._files.values():
            before, after = entry["before"], entry["after"]
            if before == after and isinstance(before, str):
                continue            # changed and changed back
            if before is None and after is None:
                continue
            status = CREATED if before is None else DELETED if after is None else MODIFIED
            item = {"path": entry["label"], "status": status}
            comparable = all(side is None or isinstance(side, str) for side in (before, after))
            if comparable:
                diff, added, removed = diff_of(before, after)
                if not diff and status == MODIFIED:
                    continue
                item["added"], item["removed"] = added, removed
                if len(diff) > MAX_DIFF_CHARS or spent + len(diff) > MAX_TOTAL_DIFF_CHARS:
                    item["note"] = "too long to show here"
                else:
                    item["diff"] = diff
                    spent += len(diff)
            else:
                item["note"] = "a file with no text to compare" if BINARY in (before, after) else "too large to compare"
            shown.append(item)
            if len(shown) >= MAX_FILES:
                break
        return shown

    def take(self) -> Optional[List[dict]]:
        """The list again if anything was recorded since it was last taken, else None."""
        if self._version == self._reported:
            return None
        self._reported = self._version
        return self.summary()
