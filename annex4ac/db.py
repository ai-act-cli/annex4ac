from __future__ import annotations
import re
from collections import defaultdict
from contextlib import contextmanager
from typing import Dict, Iterator, Optional, List, Tuple

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
_SUBPOINT_RE = re.compile(r"\([a-z]\)", re.I)
_SUBPOINT_LINE_RE = re.compile(r"^\s*\(([a-z])\)\s+", re.I)
_CHILD_CODE_RE = re.compile(r"^AnnexIV\.\d+\.([a-z])", re.I)


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
        select(Rule.section_code, Rule.content, Rule.order_index)
        .where(Rule.regulation_id == reg_id, Rule.section_code.like("AnnexIV%"))
        .order_by(
            nulls_last(Rule.order_index.asc()),
            func.regexp_replace(
                Rule.section_code, r"^AnnexIV\.(\d+).*$", r"\1",
            ).cast(Integer),
            Rule.section_code.asc(),
        )
    ).all()

    buckets: dict[str, List[Tuple[str, str, Optional[int]]]] = defaultdict(list)
    for sc, content, idx in rows:
        key = _annex_key_from_section_code(sc)
        if key:
            buckets[key].append((sc, (content or "").strip(), idx))

    out: Dict[str, str] = {}
    for i, (_, key) in enumerate(SECTION_MAPPING, start=1):
        parts = buckets.get(key, [])
        if not parts:
            out[key] = ""
            continue

        parent_code = f"AnnexIV.{i}"
        parent_text = None
        children: List[Tuple[str, str, Optional[int]]] = []
        for sc, content, idx in parts:
            if sc.lower() == parent_code.lower():
                parent_text = content
            else:
                children.append((sc, content, idx))

        if parent_text and _SUBPOINT_RE.search(parent_text):
            out[key] = parent_text.strip()
            continue

        lines: List[str] = []
        if parent_text and parent_text.strip():
            lines.append(parent_text.strip())

        def _child_sort(t: Tuple[str, str, Optional[int]]):
            sc, _c, idx = t
            m = _CHILD_CODE_RE.match(sc)
            letter = m.group(1) if m else ""
            return (idx is None, idx if idx is not None else 0, letter)

        for sc, content, idx in sorted(children, key=_child_sort):
            m = _CHILD_CODE_RE.match(sc)
            letter = m.group(1) if m else ""
            clean = _SUBPOINT_LINE_RE.sub("", content).strip()
            prefix = f"({letter}) " if letter else ""
            lines.append(f"{prefix}{clean}")

        out[key] = "\n\n".join([ln for ln in lines if ln])
    return out

def get_schema_version_from_db(ses: Session, celex_id: str = "32024R1689") -> Optional[str]:
    return ses.execute(
        select(Regulation.version).where(Regulation.celex_id == celex_id)
    ).scalar_one_or_none()
