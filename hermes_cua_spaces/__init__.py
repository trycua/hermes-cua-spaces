"""Hermes terminal backend that runs commands inside a Cua Space.

    hermes plugins install https://github.com/trycua/hermes-cua-spaces
    hermes config set terminal.backend cua

Sign in once with `cua auth login` (only needed for Spaces on your cua.ai account; a local Space
needs no sign-in).
"""

from __future__ import annotations

import importlib.util
import shutil
import threading
from typing import Any, Dict, List, Optional, Tuple

from agent.terminal_env_provider import TerminalEnvironmentProvider

from . import windows, windows_files
from .config import Settings, load_settings
from .errors import CuaSpacesError

__version__ = "0.2.0"

_client_lock = threading.Lock()
_client: Any = None


def _shared_client() -> Any:
    """One SDK runtime for the whole Hermes process, made on first use."""
    global _client
    with _client_lock:
        if _client is None:
            from .client import SdkSpacesClient

            _client = SdkSpacesClient()
        return _client


class CuaSpacesProvider(TerminalEnvironmentProvider):
    name = "cua"
    display_name = "Cua Spaces"
    is_remote = True
    is_container = True
    # With `terminal.container_persistent: false` each Hermes session gets its own Space,
    # so several Hermes runs in parallel never share one machine by accident.
    session_isolated_when_nonpersistent = True

    def __init__(self, get_config: Any = None, client: Any = None) -> None:
        self._get_config = get_config
        self._client_override = client
        self._windows_seen = False

    # -- what Hermes shows and decides on --
    @property
    def description(self) -> str:
        return "Run terminal, file and code tools inside a Cua Space, an isolated machine you can watch live."

    @property
    def env_description(self) -> str:
        if self._windows_seen:
            return f"a Cua Space (an isolated Windows machine). {windows.TERMINAL_NOTE}"
        return "a Cua Space (an isolated Linux, macOS or Windows machine)"

    @property
    def skip_container_guards(self) -> bool:
        # A Space we create is a disposable sandbox, so Hermes can skip risky-command prompts.
        # A Space you point us at is your own machine: keep the prompts.
        try:
            return load_settings(self._get_config).creates
        except Exception:
            return False

    @property
    def strip_env_keys(self) -> frozenset:
        return frozenset({"CUA_API_KEY", "CUA_DAEMON_TOKEN"})

    def is_available(self) -> bool:
        return importlib.util.find_spec("cua") is not None

    def probe(self) -> Tuple[str, str]:
        if not self.is_available():
            return ("needs_setup", "The cua Python package is not installed. Run `hermes pm repair`.")
        return ("ready", "")

    def setup_instructions(self) -> List[str]:
        return [
            "hermes config set terminal.backend cua",
            "Optional: cua auth login   (only for Spaces on your cua.ai account)",
            "Optional: hermes config set plugins.entries.cua-spaces.settings.space my-space",
            "Watch the Space live: open the Cua Spaces app, or run `cua spaces ls`.",
        ]

    def doctor_checks(self) -> List[Tuple[bool, str, str]]:
        sdk = self.is_available()
        cli = shutil.which("cua") is not None
        return [
            (sdk, "Cua Python SDK", "(installed)" if sdk else "(missing: run `hermes pm repair`)"),
            (cli, "cua command", "(found)" if cli else "(not on PATH: optional, install from https://cua.ai)"),
        ]

    def create_environment(
        self,
        *,
        cwd: str,
        timeout: int,
        task_id: str = "default",
        image: Optional[str] = None,
        container_config: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ):
        from .environment import CuaSpaceEnvironment

        settings: Settings = load_settings(self._get_config)
        persistent = bool((container_config or {}).get("container_persistent", True))
        client = self._client_override or _shared_client()
        env = CuaSpaceEnvironment(
            cwd=cwd, timeout=timeout, task_id=task_id, settings=settings, client=client, persistent=persistent
        )
        if env.is_windows:
            self._windows_seen = True
            windows_files.install()
        return env


def register(ctx) -> None:
    """Plugin entry point."""
    get_config = getattr(ctx, "get_config", None)
    windows_files.install()
    ctx.register_terminal_environment_provider(CuaSpacesProvider(get_config=get_config))


__all__ = ["CuaSpacesProvider", "CuaSpacesError", "register", "__version__"]
