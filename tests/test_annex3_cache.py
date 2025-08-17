from annex4ac.annex4ac import fetch_annex3_tags


def test_annex3_offline_fallback(monkeypatch, tmp_path):
    # Simulate network failure
    def boom(*a, **k):
        raise RuntimeError("offline")
    monkeypatch.setattr("annex4ac.annex4ac._fetch_html", boom)

    cache = tmp_path / "tags.json"
    tags = fetch_annex3_tags(cache_path=str(cache), cache_days=14)
    assert "biometric_id" in tags and len(tags) >= 8
    assert cache.exists() is False
