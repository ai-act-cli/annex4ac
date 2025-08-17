from __future__ import annotations
import re
from collections import defaultdict
from contextlib import contextmanager
from functools import lru_cache
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


@lru_cache(maxsize=1)
def _engine(db_url: str):
    """Cached Engine factory to avoid reconnecting on every call."""
    return create_engine(db_url, pool_pre_ping=True)


@contextmanager
def get_session(db_url: str) -> Iterator[Session]:
    """Yield a short-lived SQLAlchemy session bound to a cached Engine."""
    eng = _engine(db_url)
    with Session(eng) as ses:
        yield ses


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
            Rule.order_index.asc().nulls_last(),
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


def get_expected_top_counts(ses: Session, celex_id: str = "32024R1689") -> Dict[str, int]:
    """Return expected number of top-level subpoints per section key.

    Counts children of the form ``AnnexIV.N.<letter>`` in the ``rules`` table
    and maps them to the corresponding section key (``SECTION_KEYS[N-1]``).
    """
    reg_id = ses.execute(
        select(Regulation.id).where(Regulation.celex_id == celex_id)
    ).scalar_one_or_none()
    if not reg_id:
        raise ValueError(f"CELEX {celex_id} not found")

    rows = ses.execute(
        select(Rule.section_code)
        .where(Rule.regulation_id == reg_id, Rule.section_code.like("AnnexIV.%"))
    ).scalars().all()

    counts: dict[str, int] = defaultdict(int)
    for sc in rows:
        m = re.match(r"^AnnexIV\.(\d+)\.([a-z])$", sc, re.I)
        if not m:
            continue
        n = int(m.group(1))
        if 1 <= n <= len(SECTION_KEYS):
            counts[SECTION_KEYS[n - 1]] += 1
    return dict(counts)

def get_schema_version_from_db(ses: Session, celex_id: str = "32024R1689") -> Optional[str]:
    return ses.execute(
        select(Regulation.version).where(Regulation.celex_id == celex_id)
    ).scalar_one_or_none()
