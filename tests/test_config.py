import os

from georeferencer.config import Settings


def test_dotenv_loads_model_and_key(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for name in ("GEO_MODEL", "OPENAI_API_KEY", "GEO_REGISTRY_PATH"):
        monkeypatch.delenv(name, raising=False)
    (tmp_path / ".env").write_text('OPENAI_API_KEY="fixture-key"\nGEO_MODEL=gpt-5.4-mini\n', encoding="utf-8")
    settings = Settings.from_env()
    assert settings.model == "gpt-5.4-mini"
    assert os.environ["OPENAI_API_KEY"] == "fixture-key"


def test_shell_environment_overrides_dotenv(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GEO_MODEL", "shell-model")
    monkeypatch.setenv("OPENAI_API_KEY", "shell-key")
    (tmp_path / ".env").write_text('OPENAI_API_KEY=file-key\nGEO_MODEL=file-model\n', encoding="utf-8")
    assert Settings.from_env().model == "shell-model"
    assert os.environ["OPENAI_API_KEY"] == "shell-key"


def test_does_not_load_parent_dotenv(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text('GEO_MODEL=parent-model\n', encoding="utf-8")
    child = tmp_path / "child"
    child.mkdir()
    monkeypatch.chdir(child)
    monkeypatch.delenv("GEO_MODEL", raising=False)
    assert Settings.from_env().model is None


def test_separate_search_and_verification_models(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GEO_WEB_SEARCH_MODEL", "gpt-6-luna")
    monkeypatch.setenv("GEO_WEB_SEARCH_REASONING_EFFORT", "low")
    monkeypatch.setenv("GEO_VERIFICATION_MODEL", "gpt-6.1-sol")
    monkeypatch.setenv("GEO_VERIFICATION_REASONING_EFFORT", "low")
    s = Settings.from_env()
    assert s.web_search_model == "gpt-6-luna" and s.web_search_reasoning_effort == "low"
    assert s.verification_model == "gpt-6.1-sol" and s.verification_reasoning_effort == "low"
