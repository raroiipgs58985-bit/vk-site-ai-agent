from config import Settings


def test_invalid_empty_settings_report_errors(monkeypatch) -> None:
    monkeypatch.delenv("SITE_BASE_URL", raising=False)
    monkeypatch.delenv("AGENT_SECRET", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    settings = Settings.from_env()
    errors = settings.validate()
    assert len(errors) >= 3
