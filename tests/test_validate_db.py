import json
from typer.testing import CliRunner
from annex4ac.annex4ac import app


def test_validate_db_sarif(monkeypatch, tmp_path):
    runner = CliRunner()

    class DummySession:
        def __enter__(self):
            return self
        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_get_session(url):
        return DummySession()

    def fake_load_annex_iv_from_db(ses, celex_id):
        return {"system_overview": "stub"}

    monkeypatch.setattr("annex4ac.annex4ac.get_session", fake_get_session)
    monkeypatch.setattr("annex4ac.annex4ac.load_annex_iv_from_db", fake_load_annex_iv_from_db)
    monkeypatch.setattr("annex4ac.annex4ac._validate_payload", lambda payload: ([], []))

    yml = tmp_path / "in.yaml"
    yml.write_text("system_overview: ''\n")
    sarif = tmp_path / "out.sarif"

    result = runner.invoke(
        app,
        [
            "validate",
            str(yml),
            "--use-db",
            "--db-url",
            "postgresql+psycopg://u:p@h/db",
            "--sarif",
            str(sarif),
        ],
    )

    assert result.exit_code == 1
    data = json.loads(sarif.read_text())
    assert data["runs"][0]["results"][0]["ruleId"] == "system_overview_required"


def test_validate_db_subpoints(monkeypatch, tmp_path):
    runner = CliRunner()

    class DummySession:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_get_session(url):
        return DummySession()

    def fake_load_annex_iv_from_db(ses, celex_id):
        # DB expects two top-level subpoints and three nested items in first
        return {"system_overview": "(a) foo\n  - x\n  - y\n  - z\n(b) bar"}

    monkeypatch.setattr("annex4ac.annex4ac.get_session", fake_get_session)
    monkeypatch.setattr("annex4ac.annex4ac.load_annex_iv_from_db", fake_load_annex_iv_from_db)
    monkeypatch.setattr("annex4ac.annex4ac._validate_payload", lambda payload: ([], []))

    yml = tmp_path / "in.yaml"
    # User supplies only one bullet -> insufficient
    yml.write_text("system_overview: |\n  - foo\n")

    result = runner.invoke(
        app,
        [
            "validate",
            str(yml),
            "--use-db",
            "--db-url",
            "postgresql+psycopg://u:p@h/db",
        ],
    )

    assert result.exit_code == 1
    assert "system_overview_subpoints_insufficient" in result.output


def test_validate_db_counts_ok(monkeypatch, tmp_path):
    runner = CliRunner()

    class DummySession:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr("annex4ac.annex4ac.get_session", lambda url: DummySession())
    monkeypatch.setattr(
        "annex4ac.annex4ac.load_annex_iv_from_db",
        lambda s, celex_id: {"system_overview": "(a) foo\n  - x\n  - y\n(b) bar"},
    )
    monkeypatch.setattr("annex4ac.annex4ac._validate_payload", lambda p: ([], []))
    class DummyModel:
        last_updated = "2024-01-01"
    monkeypatch.setattr("annex4ac.annex4ac.AnnexIVSchema", lambda **p: DummyModel())

    yml = tmp_path / "in.yaml"
    yml.write_text("system_overview: |\n  - foo\n    - x\n    - y\n  - bar\n")

    result = runner.invoke(
        app,
        [
            "validate",
            str(yml),
            "--use-db",
            "--db-url",
            "postgresql+psycopg://u:p@h/db",
        ],
    )

    assert result.exit_code == 0
