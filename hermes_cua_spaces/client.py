"""The thin layer between the plugin and the Cua SDK.

Everything the rest of the plugin needs from Cua is the small `SpacesClient` / `SpaceHandle` pair
below, so tests can swap in a fake and the real SDK stays in one file.

Sign-in: the SDK reads the session that `cua auth login` saved. The plugin never reads, stores or
prints a token.
"""

from __future__ import annotations

import inspect
import logging
from dataclasses import dataclass
from typing import Any, Optional, Protocol

from ._loop import run_sync
from .config import Settings
from .errors import (
    CuaSpacesError,
    SpaceCreateError,
    SpaceNotFoundError,
    explain,
)

logger = logging.getLogger(__name__)

# Seconds of slack on top of a command's own timeout for the round trip to the Space.
_RPC_SLACK_S = 15.0


@dataclass(frozen=True)
class ExecResult:
    output: str
    exit_code: int


class SpaceHandle(Protocol):
    id: str
    name: str
    os: str  # "linux", "macos" or "windows" ("" when the Space did not say)

    def run(self, command: str, timeout_s: float) -> ExecResult: ...

    def write(self, path: str, data: bytes) -> None: ...

    def home(self) -> str: ...


class SpacesClient(Protocol):
    def attach(self, space: str) -> SpaceHandle:
        """Connect to an existing Space by name or id, starting it if it is stopped."""

    def ensure(self, name: str, settings: Settings) -> tuple[SpaceHandle, bool]:
        """Reuse the Space called `name` if it exists, otherwise create it. Returns (handle, created)."""

    def delete(self, space_id: str) -> None: ...


def _exec_result(res: Any) -> ExecResult:
    """Map the SDK's bash result to (merged output, exit code) the way Hermes expects."""
    parts = [res.stdout or ""]
    if res.stderr:
        parts.append(res.stderr)
    if res.error:
        parts.append(f"[Space error: {res.error}]\n")
    output = "".join(parts)
    if res.timed_out:
        return ExecResult(output, 124)
    if res.exit_code is not None:
        return ExecResult(output, int(res.exit_code))
    if res.signal:
        return ExecResult(output, 137 if "KILL" in str(res.signal) else 143)
    return ExecResult(output, 1)


class _SdkSpace:
    def __init__(self, space: Any, info: Any):
        self._space = space
        self.id: str = info.id
        self.name: str = info.name
        self.os: str = (getattr(info, "os", "") or "").lower()

    def run(self, command: str, timeout_s: float) -> ExecResult:
        timeout_ms = max(1, int(timeout_s * 1000))
        try:
            res = run_sync(self._space.bash(command, timeout_ms), timeout_s + _RPC_SLACK_S)
        except TimeoutError:
            return ExecResult(f"[Command timed out after {int(timeout_s)}s]\n", 124)
        except Exception as exc:
            raise explain(exc, space=self.name) from exc
        return _exec_result(res)

    def write(self, path: str, data: bytes) -> None:
        try:
            run_sync(self._space.write(path, data), 120)
        except Exception as exc:
            raise explain(exc, space=self.name) from exc

    def home(self) -> str:
        try:
            return str(run_sync(self._space.home(), 60))
        except Exception as exc:
            raise explain(exc, space=self.name) from exc


class SdkSpacesClient:
    """Talks to Cua through the `cua` Python package."""

    def __init__(self) -> None:
        try:
            import cua
        except ImportError as exc:
            raise CuaSpacesError(
                "The `cua` Python package is not installed. Run `hermes pm repair`, "
                "or `pip install 'cua>=0.5.0,<0.6'` in the Hermes environment."
            ) from exc
        self._cua = cua
        # Same topology as the `cua` command: the running `cua daemon` (so Spaces and your cua.ai
        # sign-in are shared with the CLI and the app), else a runtime inside this process.
        self._runtime = cua.Cua.auto(cua.CuaConfig(**self._session_kwargs(cua)))
        self._spaces = self._runtime.spaces()

    @staticmethod
    def _session_kwargs(cua: Any) -> dict[str, bool]:
        """Ask the SDK to use the `cua auth login` session. The flag was renamed between releases."""
        params = inspect.signature(cua.CuaConfig.__init__).parameters
        for flag in ("account_from_session", "fleet_from_session"):
            if flag in params:
                return {flag: True}
        return {}

    def _handle(self, info: Any) -> SpaceHandle:
        try:
            space = run_sync(self._spaces.space(info.id), 60)
        except Exception as exc:
            raise explain(exc, space=info.name) from exc
        return _SdkSpace(space, info)

    def _resolve(self, space: str) -> Any:
        try:
            return run_sync(self._spaces.resolve(space), 60)
        except Exception as exc:
            raise explain(exc, space=space) from exc

    def _ensure_running(self, info: Any) -> Any:
        state = (getattr(info, "power_state", "") or getattr(info, "power", "") or "").lower()
        if state in ("stopped", "suspended", "off", "paused"):
            logger.info("Starting Space %s (it was %s)", info.name, state)
            try:
                run_sync(self._spaces.start(info.id), 600)
            except Exception as exc:
                raise explain(exc, space=info.name) from exc
            info = self._resolve(info.id)
        return info

    def attach(self, space: str) -> SpaceHandle:
        info = self._ensure_running(self._resolve(space))
        return self._handle(info)

    def ensure(self, name: str, settings: Settings) -> tuple[SpaceHandle, bool]:
        try:
            return self.attach(name), False
        except SpaceNotFoundError:
            pass
        options = self._cua.SpaceCreateOptions(
            image=settings.image or None,
            on=settings.on or None,
            name=name,
            wait=True,
            timeout_ms=settings.create_timeout_s * 1000,
        )
        logger.info("Creating Space %s (image=%s, on=%s)", name, settings.image, settings.on or "default")
        try:
            result = run_sync(self._spaces.create(options), settings.create_timeout_s + 60)
        except Exception as exc:
            raise explain(exc, space=name) from exc
        if result.space is None:
            raise SpaceCreateError(
                f"The Space '{name}' is still being created. Wait for it to finish "
                "(`cua spaces ls`), then start Hermes again."
            )
        return self._handle(result.space), True

    def delete(self, space_id: str) -> None:
        try:
            run_sync(self._spaces.delete(space_id), 300)
        except Exception as exc:
            raise explain(exc, space=space_id) from exc
