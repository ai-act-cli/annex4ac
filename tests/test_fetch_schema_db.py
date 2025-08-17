import os
from typer.testing import CliRunner
from annex4ac.annex4ac import app


def test_fetch_schema_db(monkeypatch, tmp_path):
    runner = CliRunner()

    class DummySession:
        def __enter__(self):
            return self
        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_get_session(url):
        return DummySession()

    def fake_load_annex_iv_from_db(ses, regulation_id=None, celex_id=None):
        return {"system_overview": "stub"}

    def fake_get_schema_version_from_db(ses, regulation_id=None, celex_id=None):
        return "20240101"

    monkeypatch.setattr("annex4ac.annex4ac.get_session", fake_get_session)
    monkeypatch.setattr("annex4ac.annex4ac.load_annex_iv_from_db", fake_load_annex_iv_from_db)
    monkeypatch.setattr("annex4ac.annex4ac.get_schema_version_from_db", fake_get_schema_version_from_db)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / ".cache"))

    out_file = tmp_path / "out.yaml"
    result = runner.invoke(
        app,
        ["fetch-schema", "--db-url", "postgresql+psycopg://u:p@h/db", str(out_file)],
    )
    assert result.exit_code == 0
    assert out_file.exists()
    assert "system_overview" in out_file.read_text()
