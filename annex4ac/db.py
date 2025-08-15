from __future__ import annotations
import re
from collections import defaultdict
from typing import Dict, Optional
from sqlalchemy import create_engine, select, String, Text, ForeignKey
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, Session
from .constants import SECTION_MAPPING, SECTION_KEYS


class Base(DeclarativeBase): ...


class Regulation(Base):
    __tablename__ = "regulations"
    id: Mapped[str] = mapped_column(primary_key=True)
    celex_id: Mapped[str] = mapped_column(String(32))
    version: Mapped[str] = mapped_column(String(32))


class Rule(Base):
    __tablename__ = "rules"
    id: Mapped[str] = mapped_column(primary_key=True)
    regulation_id: Mapped[str] = mapped_column(ForeignKey("regulations.id"))
    section_code: Mapped[str] = mapped_column(String(64))
    title: Mapped[Optional[str]] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text)
    order_index: Mapped[Optional[int]] = mapped_column(nullable=True)


def get_session(db_url: str) -> Session:
    """Return a short-lived SQLAlchemy session for CLI commands.

    We intentionally avoid a global ``sessionmaker`` factory; each CLI invocation
    acquires its own connection from the engine's pool and disposes it promptly.
    """
    engine = create_engine(db_url, pool_pre_ping=True)
    return Session(engine)


_ANNEX_RE = re.compile(r"^AnnexIV\.(\d+)", re.I)


def _annex_key_from_section_code(sc: str) -> Optional[str]:
    m = _ANNEX_RE.match(sc or "")
    if not m:
        return None
    n = int(m.group(1))
    if 1 <= n <= len(SECTION_KEYS):
        return SECTION_KEYS[n-1]
    return None


def load_annex_iv_from_db(ses: Session, celex_id: str = "32024R1689") -> Dict[str, str]:
    reg_id = ses.execute(
        select(Regulation.id).where(Regulation.celex_id == celex_id)
    ).scalar_one_or_none()
    if reg_id is None:
        raise ValueError(f"CELEX {celex_id} not found in database")

    rows = ses.execute(
        select(Rule.section_code, Rule.content)
        .where(Rule.regulation_id == reg_id, Rule.section_code.like("AnnexIV%"))
        .order_by(Rule.order_index.asc().nulls_last(), Rule.section_code.asc())
    ).all()

    buckets: dict[str, list[str]] = defaultdict(list)
    for sc, content in rows:
        key = _annex_key_from_section_code(sc)
        if key:
            buckets[key].append((content or "").strip())

    out: Dict[str, str] = {}
    for _, key in SECTION_MAPPING:
        out[key] = "\n\n".join([x for x in buckets.get(key, []) if x])
    return out


def get_schema_version_from_db(ses: Session, celex_id: str = "32024R1689") -> Optional[str]:
    return ses.execute(
        select(Regulation.version).where(Regulation.celex_id == celex_id)
    ).scalar_one_or_none()
