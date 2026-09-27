import keyring
import pytest


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Point the data folder at a temp dir and clear secret env vars."""
    home = tmp_path / "home"
    monkeypatch.setenv("MUSIC_AGENT_HOME", str(home))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    return home


@pytest.fixture(autouse=True)
def fake_keyring(monkeypatch):
    """Replace the macOS Keychain with an in-memory dict."""
    secrets: dict[tuple[str, str], str] = {}
    monkeypatch.setattr(keyring, "get_password", lambda service, name: secrets.get((service, name)))
    monkeypatch.setattr(
        keyring, "set_password", lambda service, name, value: secrets.__setitem__((service, name), value)
    )
    return secrets
