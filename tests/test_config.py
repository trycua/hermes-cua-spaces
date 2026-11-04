import pytest

from hermes_cua_spaces.config import CREATE, Settings, load_settings, space_name_for


def test_defaults():
    s = load_settings(None, {})
    assert s.creates and s.space == CREATE and s.image == "linux" and s.on == ""


def test_config_values():
    cfg = {"space": "build-box", "on": "local", "image": "linux:24.04", "create_timeout": 30}
    s = load_settings(lambda k, default=None: cfg.get(k, default), {})
    assert (s.space, s.on, s.image, s.create_timeout_s) == ("build-box", "local", "linux:24.04", 30)
    assert not s.creates


def test_env_wins_over_config():
    s = load_settings(lambda k, default=None: "from-config", {"HERMES_CUA_SPACE": "from-env"})
    assert s.space == "from-env"


def test_broken_config_reader_falls_back():
    def boom(*_a, **_k):
        raise RuntimeError("no config")

    assert load_settings(boom, {}).creates


def test_bad_timeout_falls_back():
    s = load_settings(lambda k, default=None: "abc" if k == "create_timeout" else default, {})
    assert s.create_timeout_s == 600


def test_validate_rejects_non_positive_timeout():
    with pytest.raises(ValueError, match="positive"):
        Settings(create_timeout_s=0).validate()


def test_space_name_is_safe_and_stable():
    assert space_name_for("default") == "hermes-default"
    assert space_name_for("Task_ID/42 x") == "hermes-task-id-42-x"
    assert space_name_for("") == "hermes-default"
    assert len(space_name_for("x" * 100)) <= len("hermes-") + 24


def test_delete_on_exit_default_and_overrides():
    assert load_settings(None, {}).delete_on_exit is True
    assert load_settings(lambda k, default=None: False if k == "delete_on_exit" else default, {}).delete_on_exit is False
    assert load_settings(None, {"HERMES_CUA_KEEP": "1"}).delete_on_exit is False


def test_unique_names_differ():
    first = space_name_for("a", unique=True)
    second = space_name_for("a", unique=True)
    assert first != second
    assert first.startswith("hermes-a-")
    assert len(first.rsplit("-", 1)[-1]) == 24
    assert len(first) <= 63
