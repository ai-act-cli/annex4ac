from __future__ import annotations
import os
import json
import os
from datetime import datetime, timedelta
import requests
from bs4 import BeautifulSoup
from importlib.resources import files
from platformdirs import user_cache_dir
from typing import Optional, Set


def slugify(text: str) -> str:
    """Normalize Annex III tag strings."""
    return (
        text.lower()
        .replace(" ", "_")
        .replace("-", "_")
        .replace("–", "_")
        .replace("—", "_")
        .replace("/", "_")
        .replace(".", "")
        .replace(",", "")
        .replace(":", "")
        .replace(";", "")
        .replace("'", "")
        .replace('"', "")
        .strip()
    )


def _fetch_html(url: str) -> str:
    r = requests.get(url, timeout=20)
    if r.status_code != 200:
        raise requests.HTTPError(f"{url} -> HTTP {r.status_code}")
    return r.text


def fetch_annex3_tags(cache_path: Optional[str] = None, cache_days: int = 14) -> Set[str]:
    """Return a set of Annex III high-risk tags with caching and packaged fallback."""
    cache_dir = os.path.dirname(cache_path) if cache_path else user_cache_dir("annex4ac")
    os.makedirs(cache_dir, exist_ok=True)
    cache_file = cache_path or os.path.join(cache_dir, "high_risk_tags.json")

    if os.path.exists(cache_file):
        mtime = datetime.fromtimestamp(os.path.getmtime(cache_file))
        if datetime.now() - mtime < timedelta(days=cache_days):
            with open(cache_file, "r", encoding="utf-8") as f:
                return set(json.load(f))

    try:
        html = _fetch_html("https://artificialintelligenceact.eu/annex/3/")
        soup = BeautifulSoup(html, "html.parser")
        tags = sorted({slugify(li.text) for li in soup.select("ol > li")})
        if tags:
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(tags, f, ensure_ascii=False, indent=2)
            return set(tags)
        raise RuntimeError("empty tag list")
    except Exception:
        data = (
            files("annex4ac")
            .joinpath("resources/high_risk_tags.default.json")
            .read_text(encoding="utf-8")
        )
        return set(json.loads(data))
