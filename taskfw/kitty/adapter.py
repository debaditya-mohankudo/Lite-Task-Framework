"""Thin adapter over `kitten @`. Every kitty remote-control call lives here.

Pinned to kitty 0.49.2. The socket is an explicit input to every call: there is no
ambient default, so this works from a daemon or a non-kitty shell. Argv is always a
list (never a shell string), so cwd/env/var values with spaces, '=' or quotes are safe.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Callable, Mapping, Sequence

PINNED_VERSION = "0.49.2"

Runner = Callable[[Sequence[str]], "subprocess.CompletedProcess[str]"]


class KittyError(Exception):
    """A kitten call failed."""


class KittyUnreachable(KittyError):
    """The remote-control socket could not be reached (kitty down or wrong socket)."""


class KittyVersionMismatch(KittyError):
    """Installed kitten major.minor differs from the pinned version."""


@dataclass(frozen=True)
class Window:
    id: int
    tab_id: int
    os_window_id: int
    pid: int | None
    cwd: str
    title: str
    env: Mapping[str, str] = field(default_factory=dict)
    user_vars: Mapping[str, str] = field(default_factory=dict)
    is_self: bool = False


KITTEN_TIMEOUT_S = 20


def _run(argv: Sequence[str]) -> "subprocess.CompletedProcess[str]":
    """A kitten that is missing, unrunnable or wedged is the same to callers as an unreachable
    kitty: a typed KittyUnreachable, never FileNotFoundError or a hang."""
    try:
        return subprocess.run(list(argv), capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, timeout=KITTEN_TIMEOUT_S)
    except subprocess.TimeoutExpired as e:
        raise KittyUnreachable(f"kitten did not answer within {KITTEN_TIMEOUT_S}s") from e
    except OSError as e:
        raise KittyUnreachable(f"cannot run kitten ({argv[0]}): {e}") from e


def parse_ls(raw: str | list) -> list[Window]:
    """Parse `kitten @ ls` JSON tolerantly: absent/null fields get defaults."""
    data = json.loads(raw) if isinstance(raw, str) else raw
    out: list[Window] = []
    for osw in data or []:
        for tab in osw.get("tabs") or []:
            for w in tab.get("windows") or []:
                out.append(
                    Window(
                        id=w["id"],
                        tab_id=tab["id"],
                        os_window_id=osw["id"],
                        pid=w.get("pid"),
                        cwd=w.get("cwd") or "",
                        title=w.get("title") or "",
                        env=dict(w.get("env") or {}),
                        user_vars=dict(w.get("user_vars") or {}),
                        is_self=bool(w.get("is_self")),
                    )
                )
    return out


def normalize_socket(s: str) -> str:
    """A bare path becomes a `unix:` address; `unix:`/`tcp:` addresses pass through."""
    return s if s.startswith(("unix:", "tcp:")) else f"unix:{s}"


class KittyAdapter:
    def __init__(self, socket: str, kitten: str | None = None, runner: Runner | None = None,
                 check_version: bool = True):
        """`socket` is a path or `unix:` address. `kitten` is resolved to an absolute
        path at construction (kitty's own PATH may be stripped)."""
        self.socket = normalize_socket(socket)
        self.kitten = kitten or shutil.which("kitten") or "/opt/homebrew/bin/kitten"
        self._runner = runner or _run
        if check_version:
            self.check_version()

    # -- plumbing -------------------------------------------------------------
    def _at(self, *args: str) -> "subprocess.CompletedProcess[str]":
        return self._runner([self.kitten, "@", "--to", self.socket, *args])

    def _raise(self, p: "subprocess.CompletedProcess[str]") -> None:
        err = (p.stderr or "").strip()
        if "Failed to connect" in err or "no such file or directory" in err.lower():
            raise KittyUnreachable(err)
        raise KittyError(err or f"kitten exited {p.returncode}")

    def check_version(self) -> str:
        p = self._runner([self.kitten, "--version"])
        if p.returncode != 0:
            raise KittyError((p.stderr or "").strip() or "kitten --version failed")
        parts = p.stdout.split()
        version = parts[1] if len(parts) > 1 else ""
        if version.split(".")[:2] != PINNED_VERSION.split(".")[:2]:
            raise KittyVersionMismatch(f"kitten {version!r} != pinned {PINNED_VERSION}")
        return version

    # -- operations -----------------------------------------------------------
    def ls(self, match: str | None = None) -> list[Window]:
        """All windows, or those matching a kitty match expression. An empty match is
        []; an unreachable socket raises KittyUnreachable."""
        p = self._at("ls", *(["--match", match] if match else []))
        if p.returncode != 0:
            if "No matching windows" in (p.stderr or ""):
                return []
            self._raise(p)
        return parse_ls(p.stdout)

    def launch(self, *, cwd: str, cmd: Sequence[str] = (), env: Mapping[str, str] | None = None,
               user_vars: Mapping[str, str] | None = None, title: str | None = None,
               tab: bool = True, watcher: str | None = None, location: str | None = None,
               next_to: str | None = None, keep_focus: bool = False, hold: bool = False) -> int:
        """Launch a new tab (or window) and return the new WINDOW id (not a tab id).

        `location` (e.g. "hsplit"), `next_to` (a match expression such as "id:7"), `keep_focus`
        and `hold` (keep the window after its command exits) only make sense for windows."""
        args = ["launch", f"--type={'tab' if tab else 'window'}", f"--cwd={cwd}"]
        if title:
            args.append(f"--tab-title={title}" if tab else f"--title={title}")
        for k, v in (env or {}).items():
            args += ["--env", f"{k}={v}"]
        for k, v in (user_vars or {}).items():
            args += ["--var", f"{k}={v}"]
        if watcher:
            args.append(f"--watcher={watcher}")
        if location:
            args.append(f"--location={location}")
        if next_to:
            args.append(f"--next-to={next_to}")
        if keep_focus:
            args.append("--keep-focus")
        if hold:
            args.append("--hold")
        if cmd:
            args += ["--", *cmd]
        p = self._at(*args)
        if p.returncode != 0:
            self._raise(p)
        try:
            return int(p.stdout.strip())
        except ValueError as e:
            raise KittyError(f"unexpected launch output: {p.stdout!r}") from e

    def set_user_vars(self, window_id: int, user_vars: Mapping[str, str]) -> None:
        if not user_vars:
            return
        p = self._at("set-user-vars", "--match", f"id:{window_id}",
                     *[f"{k}={v}" for k, v in user_vars.items()])
        if p.returncode != 0:
            self._raise(p)

    def find_by_var(self, name: str, value: str) -> list[Window]:
        return self.ls(match=f"var:{name}={value}")
