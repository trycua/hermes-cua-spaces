"""The Hermes terminal environment that runs every command inside a Cua Space."""

from __future__ import annotations

import logging
import os
import shlex
import threading
import uuid
from typing import Any, Callable, Optional

from tools.environments.base import BaseEnvironment

from . import windows as win
from .client import ExecResult, SpaceHandle, SpacesClient
from .config import Settings, space_name_for
from .errors import CuaSpacesError, UnsupportedSpaceError

logger = logging.getLogger(__name__)

# Hermes passes its generic container default ("/root") when the user set no working folder.
_GENERIC_CWDS = ("", "/root", "~")


class _WindowsScript(str):
    """What `_wrap_command` hands `_run_bash` on a Windows Space: the command and where it runs.

    `kind` is "ps" for the terminal tool (PowerShell) and "sh" for the Unix shell commands Hermes
    builds for its other tools (Git Bash). The text itself is only a readable tag.
    """

    kind: str
    command: str
    cwd: str

    def __new__(cls, kind: str, command: str, cwd: str) -> "_WindowsScript":
        obj = super().__new__(cls, f"# hermes-cua {kind} script for a Windows Space")
        obj.kind, obj.command, obj.cwd = kind, command, cwd
        return obj


class _SpaceProcess:
    """A process-like handle for one command running in a Space.

    Hermes reads `stdout` until it closes and polls `returncode`, so we run the blocking Cua call on a
    thread, write its output into a pipe and close the pipe when the command ends.
    """

    def __init__(self, work: Callable[[], tuple[str, int]], cancel: Callable[[], None]) -> None:
        self._cancel = cancel
        self._done = threading.Event()
        self._returncode: Optional[int] = None
        read_fd, write_fd = os.pipe()
        self._writer = os.fdopen(write_fd, "wb")
        self.stdout = os.fdopen(read_fd, "r", encoding="utf-8-sig", errors="replace")
        threading.Thread(target=self._run, args=(work,), name="hermes-cua-command", daemon=True).start()

    def _run(self, work: Callable[[], tuple[str, int]]) -> None:
        try:
            output, code = work()
            self._returncode = code
            self._writer.write(output.encode("utf-8", errors="replace"))
        except Exception:
            logger.debug("command in Space failed", exc_info=True)
            if self._returncode is None:
                self._returncode = 1
        finally:
            try:
                self._writer.close()
            except OSError:
                pass
            self._done.set()

    @property
    def returncode(self) -> Optional[int]:
        return self._returncode

    def poll(self) -> Optional[int]:
        return self._returncode if self._done.is_set() else None

    def kill(self) -> None:
        try:
            self._cancel()
        except Exception:
            logger.debug("cancel failed", exc_info=True)

    def wait(self, timeout: Optional[float] = None) -> Optional[int]:
        self._done.wait(timeout=timeout)
        return self._returncode


class CuaSpaceEnvironment(BaseEnvironment):
    """Runs each Hermes command as `bash -c` inside a Cua Space.

    Terminal, file and code tools all go through `execute()`, so they all land in the Space.

    On a Windows Space the terminal tool runs in PowerShell, file read/write/patch run natively,
    and Hermes's Unix-shell tools run in Git Bash when the Space has it. See `windows.py`.
    """

    # Input for a command is staged as a file in the Space (see _run_bash), not embedded in the command.
    _stdin_mode = "payload"
    _snapshot_timeout = 60

    def __init__(
        self,
        *,
        cwd: str,
        timeout: int,
        task_id: str = "default",
        settings: Settings,
        client: SpacesClient,
        persistent: bool = True,
        **_unused: Any,
    ) -> None:
        self._settings = settings
        self._client = client
        self._persistent = persistent
        self._lock = threading.Lock()
        self._closed = False

        if settings.creates:
            # Spaces we will delete get a unique name, so parallel Hermes runs never share one.
            deleting = settings.delete_on_exit or not persistent
            self._space, created = client.ensure(space_name_for(task_id, unique=deleting), settings)
            self._delete_on_cleanup = deleting
            logger.info("Cua Space %s %s", self._space.name, "created" if created else "reused")
        else:
            self._space = client.attach(settings.space)
            self._delete_on_cleanup = False  # a Space you named is yours: never delete it

        self._win: Optional[win.WindowsInfo] = None
        self._thread_state = threading.local()
        if getattr(self._space, "os", "") == "windows":
            self._win = self._probe_windows()
            self._windows_files = None
            if cwd in _GENERIC_CWDS or not win.looks_like_windows_path(cwd):
                cwd = self._win.home  # the user profile folder
        elif cwd in _GENERIC_CWDS:
            cwd = self._space.home()
        super().__init__(cwd=cwd, timeout=timeout)
        if self._win is None:
            self.init_session()

    # -- Windows Spaces --
    @property
    def is_windows(self) -> bool:
        return self._win is not None

    @property
    def windows_info(self) -> Optional[win.WindowsInfo]:
        return self._win

    @property
    def windows_files(self):
        """Native file read/write for a Windows Space (None on Linux and macOS)."""
        if self._win is None:
            return None
        if self._windows_files is None:
            from .windows_files import WindowsFiles

            self._windows_files = WindowsFiles(self)
        return self._windows_files

    def get_temp_dir(self) -> str:
        win_info = getattr(self, "_win", None)
        return win_info.temp if win_info is not None else super().get_temp_dir()

    def _probe_windows(self) -> win.WindowsInfo:
        try:
            result = self._call(lambda sp: sp.run(win.probe_line(), 90))
        except CuaSpacesError:
            raise
        info = win.parse_probe(result.output) if result.exit_code == 0 else None
        if info is None:
            raise UnsupportedSpaceError(
                f"Could not start PowerShell in the Windows Space '{self._space.name}'. Hermes runs its "
                "commands there with PowerShell. Check that the Space is running (`cua spaces ls`) and "
                "that `powershell.exe` works in it."
            )
        logger.info(
            "Windows Space %s: shell=%s, git bash=%s", self._space.name, info.shell, info.bash or "none"
        )
        return info

    def run_powershell(self, script: str, timeout: int = 120) -> ExecResult:
        """Run a PowerShell script in the Windows Space and return its output and exit code.

        For the plugin's own file operations. The script should print only ASCII (for example base64).
        """
        info = self._win
        assert info is not None
        path = win.script_path(info, uuid.uuid4().hex[:16], "ps1")
        text = (
            f"try {{\n{script}\n}} finally {{\n"
            "  Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue\n}\n"
        )
        self._call(lambda sp: sp.write(path, text.encode("utf-8-sig")))
        return self._call(lambda sp: sp.run(win.powershell_line(info, path), timeout))

    def execute(self, command: str, cwd: str = "", *, bounded_capture: bool = False, **kwargs: Any) -> dict:
        if self._win is not None:
            # Only the foreground terminal tool sets bounded_capture. It is the one caller that sends
            # a command a person or model wrote for this machine, so it goes to PowerShell. The rest
            # are Unix shell commands Hermes built itself. Hermes's bash-only rewrite of `A && B &` must
            # not touch PowerShell.
            self._thread_state.foreground = bool(bounded_capture)
            if bounded_capture:
                kwargs["rewrite_compound_background"] = False
        return super().execute(command, cwd, bounded_capture=bounded_capture, **kwargs)

    def _wrap_command(self, command: str, cwd: str) -> str:
        if self._win is None:
            return super()._wrap_command(command, cwd)
        kind = "ps" if getattr(self._thread_state, "foreground", False) else "sh"
        return _WindowsScript(kind, command, cwd)

    # -- identity (used in messages and by the README's "watch it live" step) --
    @property
    def space_name(self) -> str:
        return self._space.name

    @property
    def space_id(self) -> str:
        return self._space.id

    # -- running commands --
    def _run_bash(
        self, cmd_string: str, *, login: bool = False, timeout: int = 120, stdin_data: Optional[str] = None
    ) -> _SpaceProcess:
        if isinstance(cmd_string, _WindowsScript):
            return self._run_windows(cmd_string, timeout=timeout, stdin_data=stdin_data)
        run_id = uuid.uuid4().hex[:16]
        script = f": hermes-cua-run-{run_id}\n{cmd_string}"
        staged: Optional[str] = None
        if stdin_data is not None:
            staged = self._staged_stdin_path()
        shell = "bash -l -c" if login else "bash -c"

        def exec_fn() -> tuple[str, int]:
            body = script
            if staged is not None:
                self._call(lambda sp: sp.write(staged, (stdin_data or "").encode("utf-8", "surrogateescape")))
                body = f": hermes-cua-run-{run_id}\n" + self._redirect_stdin_from_file(cmd_string, staged)
            line = f"exec {shell} {shlex.quote(body)} 2>&1"
            try:
                result: ExecResult = self._call(lambda sp: sp.run(line, timeout))
            except CuaSpacesError as exc:
                return (f"[Cua Space error: {exc}]\n", 1)
            return (result.output, result.exit_code)

        def cancel() -> None:
            # Best effort: stop the command's process tree. The Space has no per-command cancel.
            pattern = shlex.quote(f"[h]ermes-cua-run-{run_id}")
            kill = (
                f"for p in $(pgrep -f {pattern}); do pkill -TERM -P $p; kill -TERM $p; done; "
                f"sleep 1; for p in $(pgrep -f {pattern}); do pkill -KILL -P $p; kill -KILL $p; done; true"
            )
            try:
                self._space.run(kill, 20)
            except Exception:
                logger.debug("could not stop command %s in the Space", run_id, exc_info=True)

        return _SpaceProcess(exec_fn, cancel)

    def _run_windows(self, script: _WindowsScript, *, timeout: int, stdin_data: Optional[str]) -> _SpaceProcess:
        info = self._win
        assert info is not None
        run_id = uuid.uuid4().hex[:16]

        def stage(path: str, text: str, *, bom: bool = False) -> None:
            data = text.encode("utf-8-sig" if bom else "utf-8", "surrogateescape")
            self._call(lambda sp: sp.write(path, data))

        def exec_fn() -> tuple[str, int]:
            try:
                if script.kind == "ps":
                    staged_in = ""
                    if stdin_data is not None:
                        staged_in = self._staged_stdin_path()
                        stage(staged_in, stdin_data)
                    ps_path = win.script_path(info, run_id, "ps1")
                    stage(ps_path, win.build_powershell_script(script.command, script.cwd, self._cwd_marker, staged_in), bom=True)
                    line = win.powershell_line(info, ps_path)
                else:
                    if not info.bash:
                        return (win.NO_BASH_MESSAGE + "\n", 127)
                    command = script.command
                    if stdin_data is not None:
                        staged_in = self._staged_stdin_path()
                        stage(staged_in, stdin_data)
                        command = self._redirect_stdin_from_file(command, staged_in)
                    sh_path = win.script_path(info, run_id, "sh")
                    ps_path = win.script_path(info, run_id, "ps1")
                    stage(sh_path, win.build_bash_script(command, script.cwd, self._cwd_marker))
                    stage(ps_path, win.build_bash_launcher(info, sh_path), bom=True)
                    line = win.powershell_line(info, ps_path)
                result: ExecResult = self._call(lambda sp: sp.run(line, timeout))
            except CuaSpacesError as exc:
                return (f"[Cua Space error: {exc}]\n", 1)
            return (result.output, result.exit_code)

        def cancel() -> None:
            try:
                self._space.run(win.cancel_line(info, run_id), 30)
            except Exception:
                logger.debug("could not stop command %s in the Space", run_id, exc_info=True)

        return _SpaceProcess(exec_fn, cancel)

    def _call(self, fn):
        """Run `fn(space)`; if the connection dropped, reconnect once and retry."""
        try:
            return fn(self._space)
        except CuaSpacesError:
            with self._lock:
                try:
                    self._space = self._client.attach(self._space.id)
                except CuaSpacesError:
                    raise
            return fn(self._space)

    # -- cleanup --
    def cleanup(self) -> None:
        with self._lock:
            if self._closed or getattr(self, "_space", None) is None:
                return
            self._closed = True
            if not self._delete_on_cleanup:
                logger.info("Leaving Cua Space %s running", self._space.name)
                return
            try:
                self._client.delete(self._space.id)
                logger.info("Deleted Cua Space %s", self._space.name)
            except Exception as exc:
                self._closed = False  # let a later cleanup try again
                logger.warning(
                    "Could not delete Cua Space %s: %s. Delete it with `cua spaces delete %s`.",
                    self._space.name, exc, self._space.name,
                )
