from typing import Literal, Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration loaded from environment variables."""

    model_config = SettingsConfigDict(env_prefix="ANNEX4AC_")

    db_url: Optional[str] = None            # postgresql+psycopg://...
    celex_id: Optional[str] = None          # optional CELEX override
    source_preference: Literal["db_only", "web_only", "db_then_web"] = "db_then_web"

