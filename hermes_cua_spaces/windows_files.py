"""File read, write, patch, delete and move for a Windows Space, without a Unix shell.

Hermes's file tools build `cat`, `sed`, `wc` and `mv` commands. A Windows Space has none of them, so
for a Windows Space only, `install()` swaps those tools' methods for ones that work on Windows paths:
bytes travel as base64 through PowerShell, and files are written with the Cua SDK. Linux and macOS
Spaces, and every other Hermes backend, keep Hermes's own code: each replacement checks the
environment first and hands anything else straight back to the original method.

`search` and the other Unix-shell tools are not replaced here: they need Git Bash in the Space
(see `windows.py`), and say so in plain words when it is missing.
"""

from __future__ import annotations

import base64
import dataclasses
import functools
import hashlib
import logging
import ntpath
import os
import posixpath
import uuid
from typing import Any, Optional

from . import windows as win

logger = logging.getLogger(__name__)

_MAX_READ_BYTES = 25 * 1024 * 1024
_HEAD_BYTES = 4096
_BOM = "﻿"

_PS_STAT = r"""
$ErrorActionPreference = 'Stop'
$p = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('@PATH@'))
if (Test-Path -LiteralPath $p -PathType Container) { Write-Output 'dir'; exit 0 }
if (-not (Test-Path -LiteralPath $p -PathType Leaf)) { Write-Output 'missing'; exit 0 }
$item = Get-Item -LiteralPath $p
$fs = [IO.File]::Open($p, 'Open', 'Read', 'ReadWrite')
try {
  $buf = New-Object byte[] @HEAD@
  $n = $fs.Read($buf, 0, $buf.Length)
} finally { $fs.Dispose() }
$head = [Convert]::ToBase64String($buf, 0, $n)
Write-Output ('file ' + $item.Length + ' ' + $head)
"""

_PS_READ = r"""
$ErrorActionPreference = 'Stop'
$p = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('@PATH@'))
$fs = [IO.File]::Open($p, 'Open', 'Read', 'ReadWrite')
try {
  $ms = New-Object IO.MemoryStream
  $fs.CopyTo($ms)
} finally { $fs.Dispose() }
Write-Output ([Convert]::ToBase64String($ms.ToArray()))
"""

_PS_WRITE = r"""
$ErrorActionPreference = 'Stop'
$dst = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('@DST@'))
$tmp = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('@TMP@'))
$dir = Split-Path -Parent $dst
if ($dir -and -not (Test-Path -LiteralPath $dir)) { [void](New-Item -ItemType Directory -Force -Path $dir) }
Move-Item -LiteralPath $tmp -Destination $dst -Force
Write-Output ('sha256 ' + (Get-FileHash -LiteralPath $dst -Algorithm SHA256).Hash.ToLower())
"""

_PS_DELETE = r"""
$ErrorActionPreference = 'Stop'
$p = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('@PATH@'))
if (Test-Path -LiteralPath $p -PathType Container) {
  if ('@RECURSIVE@' -eq '1') { Remove-Item -LiteralPath $p -Recurse -Force }
  else { Write-Output 'is-a-directory'; exit 2 }
} elseif (Test-Path -LiteralPath $p) { Remove-Item -LiteralPath $p -Force }
Write-Output 'ok'
"""

_PS_MOVE = r"""
$ErrorActionPreference = 'Stop'
$src = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('@SRC@'))
$dst = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('@DST@'))
$dir = Split-Path -Parent $dst
if ($dir -and -not (Test-Path -LiteralPath $dir)) { [void](New-Item -ItemType Directory -Force -Path $dir) }
Move-Item -LiteralPath $src -Destination $dst -Force
Write-Output 'ok'
"""


class WindowsFiles:
    """File operations on one Windows Space's environment."""

    def __init__(self, env: Any) -> None:
        self._env = env

    # -- paths --
    def resolve(self, path: str) -> str:
        """Turn what the model gave into a full Windows path: expand `~`, anchor a relative path at the
        terminal's working folder, and tidy `.` and `..`."""
        info = self._env.windows_info
        # `ntpath` for a real Windows Space. A Space whose profile folder is not a Windows path (only
        # the plugin's own tests, on Linux or macOS) uses `posixpath`.
        flavor = ntpath if win.looks_like_windows_path(info.home) else posixpath
        path = path.strip()
        if path == "~" or path.startswith(("~/", "~\\")):
            path = info.home + path[1:]
        if not (win.looks_like_windows_path(path) or (flavor is posixpath and path.startswith("/"))):
            path = flavor.join(self._env.cwd or info.home, path)
        return flavor.normpath(path)

    # -- primitives (each is one PowerShell call) --
    def _ps(self, template: str, **fields: str) -> tuple[str, int]:
        script = template
        for key, value in fields.items():
            script = script.replace(f"@{key}@", value)
        result = self._env.run_powershell(script, timeout=120)
        return result.output.strip(), result.exit_code

    def stat(self, path: str) -> tuple[str, int, bytes]:
        """("missing" | "dir" | "file", size, first bytes)."""
        out, code = self._ps(_PS_STAT, PATH=win.b64(path), HEAD=str(_HEAD_BYTES))
        if code != 0:
            raise OSError(out or "could not look at the file")
        if out == "missing" or out == "dir":
            return out, 0, b""
        _, size, head = (out.split(" ", 2) + ["", ""])[:3]
        return "file", int(size or 0), base64.b64decode(head or "")

    def read_bytes(self, path: str) -> bytes:
        out, code = self._ps(_PS_READ, PATH=win.b64(path))
        if code != 0:
            raise OSError(out or "could not read the file")
        return base64.b64decode("".join(out.split()))

    def write_bytes(self, path: str, data: bytes) -> str:
        """Write `data` to `path`, creating folders. Returns the SHA-256 of what landed on disk."""
        tmp = f"{self._env.windows_info.temp}/hermes-cua-file-{uuid.uuid4().hex[:16]}.tmp"
        self._env._call(lambda sp: sp.write(tmp, data))
        out, code = self._ps(_PS_WRITE, DST=win.b64(path), TMP=win.b64(tmp))
        if code != 0 or not out.startswith("sha256 "):
            raise OSError(out or "could not write the file")
        return out.split(" ", 1)[1].strip()

    def delete(self, path: str, recursive: bool) -> None:
        out, code = self._ps(_PS_DELETE, PATH=win.b64(path), RECURSIVE="1" if recursive else "0")
        if code == 2:
            raise OSError(f"{path} is a folder")
        if code != 0:
            raise OSError(out or "could not delete")

    def move(self, src: str, dst: str) -> None:
        out, code = self._ps(_PS_MOVE, SRC=win.b64(src), DST=win.b64(dst))
        if code != 0:
            raise OSError(out or "could not move")


# -- the replacement tool methods (they receive Hermes's ShellFileOperations as `fo`) --


def _fo_module():
    from tools import file_operations

    return file_operations


def _make(cls: Any, **fields: Any) -> Any:
    """Build one of Hermes's result objects with the fields this Hermes version has."""
    names = {f.name for f in dataclasses.fields(cls)} if dataclasses.is_dataclass(cls) else set()
    return cls(**{k: v for k, v in fields.items() if not names or k in names})


def _strip_bom(text: str) -> tuple[str, bool]:
    return (text[1:], True) if text.startswith(_BOM) else (text, False)


def _is_image(fo: Any, path: str) -> bool:
    try:
        return bool(fo._is_image(path))
    except Exception:
        return os.path.splitext(path)[1].lower() in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico")


def _probably_binary(fo: Any, path: str, head: bytes) -> bool:
    mod = _fo_module()
    if os.path.splitext(path)[1].lower() in getattr(mod, "BINARY_EXTENSIONS", ()):
        return True
    check = getattr(fo, "_is_likely_binary_bytes", None)
    return bool(check(head)) if check else (b"\x00" in head)


def _read_text_file(files: WindowsFiles, fo: Any, path: str):
    """Common start of every read: (resolved path, size, head, error result or None)."""
    mod = _fo_module()
    rpath = files.resolve(path)
    try:
        kind, size, head = files.stat(rpath)
    except OSError as exc:
        return rpath, 0, b"", _make(mod.ReadResult, error=f"Could not read {rpath}: {exc}")
    if kind == "missing":
        return rpath, 0, b"", _make(mod.ReadResult, error=f"File not found: {rpath}", not_found=True)
    if kind == "dir":
        return rpath, 0, b"", _make(mod.ReadResult, error=f"{rpath} is a folder, not a file. List it with the terminal tool: Get-ChildItem.")
    if size > _MAX_READ_BYTES:
        return rpath, size, head, _make(
            mod.ReadResult,
            file_size=size,
            error=f"{rpath} is too large to read this way ({size:,} bytes). Use the terminal tool, for example Get-Content -TotalCount 200.",
        )
    return rpath, size, head, None


def read_file(files: WindowsFiles, fo: Any, path: str, offset: int = 1, limit: int = 2000):
    mod = _fo_module()
    rpath, size, head, error = _read_text_file(files, fo, path)
    if error is not None:
        return error
    normalize = getattr(mod, "normalize_read_pagination", None)
    if normalize:
        offset, limit = normalize(offset, limit)
    if _is_image(fo, rpath):
        return _make(
            mod.ReadResult,
            is_image=True,
            is_binary=True,
            file_size=size,
            hint="Image file detected. Use vision_analyze with this file path to inspect the image contents.",
        )
    if _probably_binary(fo, rpath, head):
        describe = getattr(mod, "describe_binary_file", None)
        return _make(
            mod.ReadResult,
            is_binary=True,
            file_size=size,
            error=describe(head, size) if describe else f"{rpath} is a binary file.",
        )
    if size == 0:
        return _make(mod.ReadResult, content="", total_lines=0, file_size=0, hint="File is empty (0 bytes).")
    try:
        data = files.read_bytes(rpath)
    except OSError as exc:
        return _make(mod.ReadResult, error=f"Failed to read file: {exc}")
    text, _ = _strip_bom(data.decode("utf-8", "replace"))
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    total = len(lines)
    end = offset + limit - 1
    page = lines[offset - 1 : end]
    truncated = total > end
    hint = f"Use offset={end + 1} to continue reading (showing {offset}-{end} of {total} lines)" if truncated else None
    if offset > total > 0:
        return _make(
            mod.ReadResult,
            content="",
            total_lines=total,
            file_size=size,
            hint=f"Note: offset {offset} is beyond the end of the file ({total} lines total). Retry with offset <= {total}.",
        )
    return _make(
        mod.ReadResult,
        content=fo._add_line_numbers("\n".join(page), offset),
        total_lines=total,
        file_size=size,
        truncated=truncated,
        hint=hint,
    )


def read_file_raw(files: WindowsFiles, fo: Any, path: str):
    mod = _fo_module()
    rpath, size, head, error = _read_text_file(files, fo, path)
    if error is not None:
        return error
    if _is_image(fo, rpath):
        return _make(mod.ReadResult, is_image=True, is_binary=True, file_size=size)
    if _probably_binary(fo, rpath, head):
        return _make(mod.ReadResult, is_binary=True, file_size=size, error=f"{rpath} is a binary file.")
    try:
        data = files.read_bytes(rpath)
    except OSError as exc:
        return _make(mod.ReadResult, error=f"Failed to read file: {exc}")
    content, _ = _strip_bom(data.decode("utf-8", "surrogateescape"))
    result = _make(mod.ReadResult, content=content, file_size=size)
    _set_sha(result, data)
    return result


def read_file_bytes(files: WindowsFiles, fo: Any, path: str, max_bytes: Optional[int] = None):
    mod = _fo_module()
    rpath, size, _head, error = _read_text_file(files, fo, path)
    if error is not None:
        return error
    if max_bytes is not None and size > max_bytes:
        return _make(mod.ReadResult, file_size=size, error=f"File is too large ({size:,} bytes, limit is {max_bytes:,})")
    try:
        data = files.read_bytes(rpath)
    except OSError as exc:
        return _make(mod.ReadResult, error=f"Failed to read binary file: {exc}")
    return _make(mod.ReadResult, base64_content=base64.b64encode(data).decode("ascii"), file_size=size, is_binary=True)


def _set_sha(result: Any, data: bytes) -> None:
    try:
        result._content_sha256 = hashlib.sha256(data).hexdigest()
    except Exception:
        pass


def write_file(files: WindowsFiles, fo: Any, path: str, content: str, pre_content: Optional[str] = None):
    mod = _fo_module()
    rpath = files.resolve(path)
    try:
        encoded = content.encode("utf-8", "surrogateescape")
    except UnicodeEncodeError:
        return _make(mod.WriteResult, error="The content has characters that cannot be saved as UTF-8 text.")
    # Keep a file's line endings and byte-order mark the way Hermes does: read_file hides both.
    try:
        kind, _size, head = files.stat(rpath)
    except OSError as exc:
        return _make(mod.WriteResult, error=f"Failed to write file: {exc}")
    if kind == "dir":
        return _make(mod.WriteResult, error=f"{rpath} is a folder, not a file.")
    if kind == "file":
        if b"\r\n" in head and "\r\n" not in content:
            content = content.replace("\r\n", "\n").replace("\n", "\r\n")
        if head.startswith(b"\xef\xbb\xbf") and not content.startswith(_BOM):
            content = _BOM + content
        encoded = content.encode("utf-8", "surrogateescape")
    try:
        digest = files.write_bytes(rpath, encoded)
    except OSError as exc:
        return _make(mod.WriteResult, error=f"Failed to write file: {exc}")
    verified = digest == hashlib.sha256(encoded).hexdigest()
    if not verified:
        return _make(mod.WriteResult, error=f"Wrote {rpath}, but what is on disk differs from what was sent. Try again.")
    result = _make(mod.WriteResult, bytes_written=len(encoded), dirs_created=True, verified=True)
    _set_sha(result, encoded)
    return result


def patch_replace(files: WindowsFiles, fo: Any, path: str, old_string: str, new_string: str, replace_all: bool = False):
    mod = _fo_module()
    rpath = files.resolve(path)
    try:
        kind, _size, _head = files.stat(rpath)
        if kind != "file":
            return _make(mod.PatchResult, error=f"File not found: {rpath}" if kind == "missing" else f"{rpath} is a folder.")
        data = files.read_bytes(rpath)
    except OSError as exc:
        return _make(mod.PatchResult, error=f"Failed to read file: {exc}")
    raw = data.decode("utf-8", "surrogateescape")
    content, _ = _strip_bom(raw)

    from tools import fuzzy_match

    notes: list = []
    try:
        new_content, count, strategy, error = fuzzy_match.fuzzy_find_and_replace(
            content, old_string, new_string, replace_all, correction_notes=notes
        )
    except TypeError:  # an older Hermes without correction notes
        new_content, count, strategy, error = fuzzy_match.fuzzy_find_and_replace(
            content, old_string, new_string, replace_all
        )
    if error or count == 0:
        return _make(mod.PatchResult, error=error or f"Could not find match for old_string in {rpath}")
    if "\r\n" in content:
        new_content = new_content.replace("\r\n", "\n").replace("\n", "\r\n")
    written = fo.write_file(rpath, new_content, pre_content=raw) if _accepts_pre_content(fo) else fo.write_file(rpath, new_content)
    if getattr(written, "error", None):
        return _make(mod.PatchResult, error=f"Failed to write changes: {written.error}")
    return _make(
        mod.PatchResult,
        success=True,
        diff=fo._unified_diff(content, new_content, rpath),
        files_modified=[rpath],
        note="\n".join(str(n) for n in notes if n) or None,
    )


def _accepts_pre_content(fo: Any) -> bool:
    import inspect

    try:
        return "pre_content" in inspect.signature(fo.write_file).parameters
    except (TypeError, ValueError):
        return False


def delete_file(files: WindowsFiles, fo: Any, path: str):
    return _delete(files, path, recursive=False)


def delete_path(files: WindowsFiles, fo: Any, path: str, recursive: bool = False):
    return _delete(files, path, recursive=recursive)


def _delete(files: WindowsFiles, path: str, recursive: bool):
    mod = _fo_module()
    rpath = files.resolve(path)
    try:
        files.delete(rpath, recursive)
    except OSError as exc:
        return _make(mod.WriteResult, error=f"Failed to delete {rpath}: {exc}")
    return _make(mod.WriteResult)


def move_file(files: WindowsFiles, fo: Any, src: str, dst: str):
    mod = _fo_module()
    rsrc, rdst = files.resolve(src), files.resolve(dst)
    try:
        files.move(rsrc, rdst)
    except OSError as exc:
        return _make(mod.WriteResult, error=f"Failed to move {rsrc} -> {rdst}: {exc}")
    return _make(mod.WriteResult)


def search(files: WindowsFiles, fo: Any, *args: Any, **kwargs: Any):
    """Search runs `rg`/`grep`/`find`. Use them through Git Bash if the Space has it, else say so."""
    if files._env.windows_info.bash:
        return fo._hermes_cua_original_search(*args, **kwargs)
    return _make(_fo_module().SearchResult, error=win.NO_BASH_MESSAGE)


_REPLACEMENTS = {
    "read_file": read_file,
    "read_file_raw": read_file_raw,
    "read_file_bytes": read_file_bytes,
    "write_file": write_file,
    "patch_replace": patch_replace,
    "delete_file": delete_file,
    "delete_path": delete_path,
    "move_file": move_file,
}


def _files_for(fo: Any) -> Optional[WindowsFiles]:
    env = getattr(fo, "env", None)
    try:
        return env.windows_files if getattr(env, "is_windows", False) else None
    except Exception:
        return None


def _wrap(cls: Any, name: str, native: Any) -> None:
    original = getattr(cls, name, None)
    if original is None or getattr(original, "_hermes_cua_wrapped", False):
        return

    @functools.wraps(original)
    def method(self, *args: Any, **kwargs: Any):
        files = _files_for(self)
        if files is None:
            return original(self, *args, **kwargs)
        if name == "search":
            self._hermes_cua_original_search = functools.partial(original, self)
        return native(files, self, *args, **kwargs)

    method._hermes_cua_wrapped = True  # type: ignore[attr-defined]
    setattr(cls, name, method)


def install() -> bool:
    """Teach Hermes's file tools about Windows Spaces. Safe to call more than once; returns False when
    this Hermes has no file tools to change."""
    try:
        from tools.file_operations import ShellFileOperations
    except Exception:
        logger.debug("Hermes file tools not found; Windows file support not installed", exc_info=True)
        return False
    for name, native in {**_REPLACEMENTS, "search": search}.items():
        _wrap(ShellFileOperations, name, native)
    return True
