"""The `cua` dependency must be declared the same way everywhere and stay off Android.

Hermes admits a plugin by locking its whole workspace with uv, for every platform Hermes supports,
Android included. `cua` ships wheels for macOS, Linux and Windows only, so an unmarked requirement
makes that lock fail and the plugin can never be enabled (issues #6, #8 and #9). The real lock is
reproduced in CI by scripts/check_hermes_lock.py; these tests keep the declarations right offline.
"""
import tomllib
from pathlib import Path

import pytest
import yaml
from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parents[1]


def _pyproject() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def _manifest() -> dict:
    return yaml.safe_load((ROOT / "plugin.yaml").read_text(encoding="utf-8"))


def _cua(specs: list[str]) -> Requirement:
    found = [Requirement(s) for s in specs if Requirement(s).name == "cua"]
    assert len(found) == 1, specs
    return found[0]


def test_pyproject_and_manifest_declare_the_same_dependencies():
    assert _pyproject()["project"]["dependencies"] == _manifest()["python_dependencies"]


def test_versions_agree():
    assert _pyproject()["project"]["version"] == _manifest()["version"]


@pytest.mark.parametrize("specs", [
    _pyproject()["project"]["dependencies"],
    _manifest()["python_dependencies"],
], ids=["pyproject", "plugin.yaml"])
def test_cua_is_gated_off_android_only(specs):
    marker = _cua(specs).marker
    assert marker is not None, "an unmarked `cua` breaks the Hermes admission lock on Android"
    for platform in ("linux", "darwin", "win32"):
        assert marker.evaluate({"sys_platform": platform}), platform
    assert not marker.evaluate({"sys_platform": "android"})


def test_cua_is_not_gated_by_python_version():
    # `cua` wheels are py3-none-<platform> and work on every supported Python, 3.14 included.
    marker = _cua(_pyproject()["project"]["dependencies"]).marker
    assert "python" not in str(marker)
