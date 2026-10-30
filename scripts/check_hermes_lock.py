"""Reproduce the Hermes plugin admission lock for this plugin.

Hermes (>= 0.22.5) admits a plugin by adding it as a member of its own uv workspace and running
`uv lock` with the uv version it pins. Hermes resolves every platform it supports, Android
included, so a dependency with no wheel for one of those platforms makes the whole lock fail and the
plugin can never be enabled. This script builds the same workspace from a Hermes checkout and runs
the same `uv lock`, so that failure shows up here before users hit it.

Usage: check_hermes_lock.py HERMES_CHECKOUT [--uv PATH] [--python 3.14]

Without --uv it installs the exact uv version named in the checkout's pm/lock.json into a
temporary virtual environment (using the `uv` on PATH to do so).
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
IGNORE = shutil.ignore_patterns(".git", "__pycache__", ".venv", ".pytest_cache", "dist", "*.pyc")


def pinned_uv_version(hermes: Path) -> str:
    lock = json.loads((hermes / "pm" / "lock.json").read_text(encoding="utf-8"))
    url = next(iter(lock["packages"]["uv"]["artifacts"].values()))["url"]
    return url.split("/download/")[1].split("/")[0]


def install_uv(version: str, scratch: Path) -> Path:
    venv = scratch / "uv-venv"
    subprocess.run(["uv", "venv", "-q", str(venv)], check=True)
    subprocess.run(["uv", "pip", "install", "-q", "--python", str(venv), f"uv=={version}"], check=True)
    return venv / ("Scripts/uv.exe" if sys.platform == "win32" else "bin/uv")


def build_workspace(hermes: Path, root: Path) -> None:
    ignore = shutil.ignore_patterns(".git", "node_modules", "__pycache__", "web", "ui-tui", "apps", "website",
                                    "tests", "tests-js", "*.pyc")
    shutil.copytree(hermes, root, ignore=ignore, symlinks=True)
    member = root / "plugin-sources" / "cua-spaces"
    shutil.copytree(PLUGIN_ROOT, member, ignore=IGNORE)
    text = (root / "pyproject.toml").read_text(encoding="utf-8-sig")
    # Hermes checks release age when it locks its own dependencies and drops the cutoff for plugins.
    text = re.sub(r"(?m)^exclude-newer(-package)?\s*=.*\n", "", text)
    assert "[tool.uv.workspace]" not in text
    text += '\n[tool.uv.workspace]\nmembers = ["plugin-sources/cua-spaces"]\n'
    (root / "pyproject.toml").write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("hermes", type=Path)
    parser.add_argument("--uv", type=Path)
    parser.add_argument("--python", default="3.14")
    args = parser.parse_args()
    hermes = args.hermes.resolve()
    with tempfile.TemporaryDirectory() as tmp:
        scratch = Path(tmp)
        version = pinned_uv_version(hermes)
        uv = args.uv or install_uv(version, scratch)
        print(f"Hermes pins uv {version}; using {uv}", flush=True)
        root = scratch / "workspace"
        build_workspace(hermes, root)
        result = subprocess.run([str(uv), "lock", "--python", args.python], cwd=root)
        if result.returncode != 0:
            print("\nHermes would refuse to admit this plugin: its dependencies do not resolve on "
                  "every platform Hermes locks for (Android included).", file=sys.stderr)
            return 1
        lock = tomllib.loads((root / "uv.lock").read_text(encoding="utf-8"))
        names = {p["name"] for p in lock["package"]}
        if "cua" not in names:
            print("uv.lock has no `cua` package", file=sys.stderr)
            return 1
        print("uv lock succeeded; cua is locked for the supported platforms.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
