"""Test isolation: point all user-data paths at a per-test temp directory so the
suite never reads or writes the real ~/.euron-agent (permissions, sessions,
schedules, plugins, skills, memory)."""
import pytest


@pytest.fixture(autouse=True)
def _isolate_user_dir(tmp_path_factory, monkeypatch):
    base = tmp_path_factory.mktemp("euronhome")

    import euron_agent.commands as commands
    import euron_agent.memory as memory
    import euron_agent.permissions as permissions
    import euron_agent.plugins as plugins
    import euron_agent.schedules as schedules
    import euron_agent.sessions as sessions
    import euron_agent.settings as settings
    import euron_agent.skills as skills

    monkeypatch.setattr(settings, "SETTINGS_DIR", base, raising=False)
    monkeypatch.setattr(settings, "SETTINGS_FILE", base / "config.json", raising=False)
    monkeypatch.setattr(permissions, "PERMISSIONS_FILE", base / "permissions.json", raising=False)
    monkeypatch.setattr(sessions, "SESSIONS_DIR", base / "sessions", raising=False)
    monkeypatch.setattr(schedules, "SCHEDULES_FILE", base / "schedules.json", raising=False)
    monkeypatch.setattr(plugins, "PLUGINS_DIR", base / "plugins", raising=False)
    monkeypatch.setattr(memory, "USER_FILE", base / "AGENTS.md", raising=False)
    # modules that imported SETTINGS_DIR by name keep their own binding
    for mod in (skills, commands):
        monkeypatch.setattr(mod, "SETTINGS_DIR", base, raising=False)
    yield
