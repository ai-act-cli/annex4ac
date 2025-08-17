from __future__ import annotations
import re
from collections import defaultdict
from contextlib import contextmanager
from typing import Dict, Iterator, Optional

from sqlalchemy import (
    Integer,
    create_engine,
    select,
    String,
    Text,
    ForeignKey,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column
from sqlalchemy.sql import nulls_last

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


@contextmanager
def get_session(db_url: str) -> Iterator[Session]:
    """Context manager yielding a short-lived SQLAlchemy session.

    A fresh Engine is created for each invocation and disposed on exit to avoid
    lingering connections in long-running processes.
    """
    engine = create_engine(db_url, pool_pre_ping=True)
    try:
        with Session(engine) as ses:
            yield ses
    finally:
        engine.dispose()


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
        .order_by(
            nulls_last(Rule.order_index.asc()),
            func.regexp_replace(
                Rule.section_code, r"^AnnexIV\.(\d+).*$", r"\1"
            ).cast(Integer),
            Rule.section_code.asc(),
        )
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
