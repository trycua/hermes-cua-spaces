"""SdkSpacesClient against a fake `cua` module (no network, no real Space)."""
import asyncio
import sys
import types

import pytest

from hermes_cua_spaces.config import Settings
from hermes_cua_spaces.errors import NotSignedInError, SpaceCreateError


class Info:
    def __init__(self, name, os="linux", power="running"):
        self.id, self.name, self.os, self.power_state, self.power = f"local:{name}", name, os, power, power


class FakeBash:
    def __init__(self, **kw):
        base = dict(stdout="", stderr="", exit_code=0, signal=None, timed_out=False, error=None)
        base.update(kw)
        self.__dict__.update(base)


class FakeSpace:
    def __init__(self, result):
        self.result, self.commands, self.writes = result, [], []

    async def bash(self, command, timeout_ms):
        self.commands.append((command, timeout_ms))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result

    async def write(self, path, data):
        self.writes.append((path, data))

    async def home(self):
        return "/home/cua"


class FakeSpaces:
    def __init__(self):
        self.infos, self.spaces, self.created, self.deleted, self.started = {}, {}, [], [], []

    async def resolve(self, name):
        if name in self.infos:
            return self.infos[name]
        for info in self.infos.values():
            if info.id == name:
                return info
        raise type("NotFound", (Exception,), {})(f"not found: {name}")

    async def space(self, sid):
        return self.spaces.setdefault(sid, FakeSpace(FakeBash(stdout="hi\n")))

    async def create(self, options):
        info = Info(options.name)
        self.created.append(options)
        self.infos[options.name] = info
        return types.SimpleNamespace(space=info, pending_id=None, reused=False)

    async def start(self, sid):
        self.started.append(sid)

    async def delete(self, sid):
        self.deleted.append(sid)
        return "deleted"


@pytest.fixture
def sdk(monkeypatch):
    spaces = FakeSpaces()
    mod = types.ModuleType("cua")
    runtime = types.SimpleNamespace(spaces=lambda: spaces)
    mod.CuaConfig = type("CuaConfig", (), {"__init__": lambda self, *, fleet_from_session=False: None})
    mod.Cua = types.SimpleNamespace(auto=lambda config: runtime)
    mod.SpaceCreateOptions = lambda **kw: types.SimpleNamespace(**kw)
    monkeypatch.setitem(sys.modules, "cua", mod)
    from hermes_cua_spaces.client import SdkSpacesClient

    return SdkSpacesClient(), spaces


def test_uses_cua_login_session_flag(sdk):
    client, _ = sdk
    assert client._session_kwargs(sys.modules["cua"]) == {"fleet_from_session": True}


def test_ensure_creates_once_then_reuses(sdk):
    client, spaces = sdk
    s = Settings(image="linux", on="local")
    h, created = client.ensure("hermes-a", s)
    assert created and h.name == "hermes-a"
    assert (spaces.created[0].image, spaces.created[0].on, spaces.created[0].wait) == ("linux", "local", True)
    h2, created2 = client.ensure("hermes-a", s)
    assert not created2 and len(spaces.created) == 1


def test_default_location_passes_none(sdk):
    client, spaces = sdk
    client.ensure("hermes-b", Settings())
    assert spaces.created[0].on is None


def test_attach_missing_space_is_plain(sdk):
    client, _ = sdk
    with pytest.raises(Exception) as err:
        client.attach("ghost")
    assert "cua spaces ls" in str(err.value)


def test_attach_starts_stopped_space(sdk):
    client, spaces = sdk
    spaces.infos["old"] = Info("old", power="stopped")
    client.attach("old")
    assert spaces.started == ["local:old"]


def test_windows_space_is_attached(sdk):
    client, spaces = sdk
    spaces.infos["win"] = Info("win", os="windows")
    handle = client.attach("win")
    assert handle.os == "windows"


def test_linux_space_reports_its_os(sdk):
    client, spaces = sdk
    spaces.infos["lin"] = Info("lin")
    assert client.attach("lin").os == "linux"


def test_run_merges_streams_and_maps_codes(sdk):
    client, spaces = sdk
    spaces.infos["x"] = Info("x")
    h = client.attach("x")
    sp = spaces.spaces["local:x"]
    sp.result = FakeBash(stdout="out\n", stderr="err\n", exit_code=3)
    r = h.run("cmd", 5)
    assert (r.output, r.exit_code) == ("out\nerr\n", 3)
    assert sp.commands[-1] == ("cmd", 5000)
    sp.result = FakeBash(exit_code=None, timed_out=True)
    assert h.run("cmd", 1).exit_code == 124
    sp.result = FakeBash(exit_code=None, signal="KILL")
    assert h.run("cmd", 1).exit_code == 137


def test_run_error_is_explained(sdk):
    client, spaces = sdk
    spaces.infos["x"] = Info("x")
    h = client.attach("x")
    spaces.spaces["local:x"].result = type("Unauthenticated", (Exception,), {})("nope")
    with pytest.raises(NotSignedInError):
        h.run("cmd", 1)


def test_create_without_space_result_is_plain(sdk):
    client, spaces = sdk

    async def pending(options):
        return types.SimpleNamespace(space=None, pending_id="p1", reused=False)

    spaces.create = pending
    with pytest.raises(SpaceCreateError, match="still being created"):
        client.ensure("hermes-slow", Settings())


def test_delete(sdk):
    client, spaces = sdk
    client.delete("local:x")
    assert spaces.deleted == ["local:x"]
