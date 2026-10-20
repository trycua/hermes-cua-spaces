import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hermes_cua_spaces.client import ExecResult  # noqa: E402


class LocalShellSpace:
    """A stand-in Space that runs commands in a local temp dir. Mirrors the SDK: `/bin/sh -c <line>`."""

    def __init__(self, root: Path, name: str = "hermes-test"):
        self.id = f"local:{name}"
        self.name = name
        self.root = root
        self.lines: list[str] = []

    def run(self, command: str, timeout_s: float) -> ExecResult:
        self.lines.append(command)
        try:
            p = subprocess.run(
                ["/bin/sh", "-c", command], capture_output=True, timeout=timeout_s, cwd=self.root
            )
        except subprocess.TimeoutExpired as exc:
            return ExecResult((exc.stdout or b"").decode() + "[timeout]", 124)
        return ExecResult((p.stdout + p.stderr).decode(errors="replace"), p.returncode)

    def write(self, path: str, data: bytes) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    def home(self) -> str:
        return str(self.root)


class FakeClient:
    def __init__(self, space: LocalShellSpace):
        self.space = space
        self.calls: list[tuple] = []
        self.deleted: list[str] = []

    def attach(self, space):
        self.calls.append(("attach", space))
        return self.space

    def ensure(self, name, settings):
        self.calls.append(("ensure", name, settings.image, settings.on))
        return self.space, True

    def delete(self, space_id):
        self.deleted.append(space_id)


@pytest.fixture
def space(tmp_path):
    if sys.platform.startswith("win"):
        pytest.skip("this stand-in Space runs /bin/sh; the Windows tests cover Windows")
    return LocalShellSpace(tmp_path)


@pytest.fixture
def client(space):
    return FakeClient(space)
