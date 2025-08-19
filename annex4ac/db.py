from __future__ import annotations
import re
from collections import defaultdict
from contextlib import contextmanager
from functools import lru_cache
from datetime import datetime
from typing import Dict, Iterator, Optional, List, Tuple

from sqlalchemy import (
    Integer,
    create_engine,
    select,
    String,
    Text,
    ForeignKey,
    func,
    case,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from .constants import SECTION_MAPPING, SECTION_KEYS


class Base(DeclarativeBase): ...


class Regulation(Base):
    __tablename__ = "regulations"
    id: Mapped[str] = mapped_column(primary_key=True)
    celex_id: Mapped[Optional[str]] = mapped_column(String(32))
    version: Mapped[Optional[str]] = mapped_column(String(32))
    last_updated: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    effective_date: Mapped[Optional[datetime]] = mapped_column(nullable=True)


class Rule(Base):
    __tablename__ = "rules"
    id: Mapped[str] = mapped_column(primary_key=True)
    regulation_id: Mapped[str] = mapped_column(ForeignKey("regulations.id"))
    section_code: Mapped[str] = mapped_column(String(64))
    title: Mapped[Optional[str]] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text)
    order_index: Mapped[Optional[int]] = mapped_column(nullable=True)
    last_modified: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    effective_date: Mapped[Optional[datetime]] = mapped_column(nullable=True)


class RegSourceLog(Base):
    __tablename__ = "reg_source_log"
    id: Mapped[str] = mapped_column(primary_key=True)
    regulation_id: Mapped[str] = mapped_column(ForeignKey("regulations.id"))
    source_name: Mapped[Optional[str]] = mapped_column(String(64))
    created_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)


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


def get_latest_regulation_id_with_annex(ses: Session) -> str:
    """Return regulation_id of the freshest Annex IV snapshot available."""
    regs = (
        ses.execute(
            select(Rule.regulation_id)
            .where(Rule.section_code.like("AnnexIV%"))
            .group_by(Rule.regulation_id)
        )
        .scalars()
        .all()
    )
    if not regs:
        raise ValueError("No AnnexIV rules found in DB")

    candidates: List[Tuple[str, Dict]] = []
    for rid in regs:
        try:
            max_rule_ts = ses.execute(
                select(func.max(Rule.last_modified)).where(Rule.regulation_id == rid)
            ).scalar()
        except Exception:
            ses.rollback()
            max_rule_ts = None

        try:
            max_rule_eff = ses.execute(
                select(func.max(Rule.effective_date)).where(Rule.regulation_id == rid)
            ).scalar()
        except Exception:
            ses.rollback()
            max_rule_eff = None

        try:
            reg = ses.execute(
                select(Regulation.version, Regulation.last_updated, Regulation.effective_date)
                .where(Regulation.id == rid)
            ).one_or_none()
            reg_version, reg_last, reg_eff = reg if reg else (None, None, None)
        except Exception:
            ses.rollback()
            reg_version = reg_last = reg_eff = None

        try:
            src_prio = case(
                (RegSourceLog.source_name == "celex_consolidated", 3),
                else_=case(
                    (RegSourceLog.source_name == "ai_act_original", 2),
                    else_=case(
                        (RegSourceLog.source_name == "ai_act_html", 1),
                        else_=0,
                    ),
                ),
            )
            rsl = (
                ses.execute(
                    select(
                        func.max(RegSourceLog.created_at),
                        func.max(src_prio),
                    ).where(RegSourceLog.regulation_id == rid)
                ).first()
            )
            rsl_last = rsl[0] if rsl else None
            rsl_prio = rsl[1] if rsl else 0
        except Exception:
            ses.rollback()
            rsl_last, rsl_prio = None, 0

        candidates.append(
            (
                rid,
                {
                    "rsl_prio": rsl_prio or 0,
                    "rsl_last": rsl_last,
                    "reg_last": reg_last,
                    "reg_eff": reg_eff,
                    "max_rule": max_rule_ts or max_rule_eff,
                    "reg_ver": reg_version,
                },
            )
        )

    def _ver_as_dt(v: Optional[str]) -> datetime:
        if not v:
            return datetime.min
        s = str(v).replace(".", "").replace("-", "")
        try:
            return datetime.strptime(s[:8], "%Y%m%d")
        except Exception:
            return datetime.min

    candidates.sort(
        key=lambda x: (
            x[1]["rsl_prio"],
            x[1]["rsl_last"] or datetime.min,
            x[1]["reg_last"] or datetime.min,
            x[1]["reg_eff"] or datetime.min,
            x[1]["max_rule"] or datetime.min,
            _ver_as_dt(x[1]["reg_ver"]),
        ),
        reverse=True,
    )
    return candidates[0][0]


def load_annex_iv_from_db(
    ses: Session, regulation_id: Optional[str] = None, celex_id: Optional[str] = None
) -> Dict[str, str]:
    if regulation_id is None:
        if celex_id:
            regulation_id = ses.execute(
                select(Regulation.id).where(Regulation.celex_id == celex_id)
            ).scalar_one_or_none()
            if regulation_id is None:
                raise ValueError(f"CELEX {celex_id} not found in database")
        else:
            regulation_id = get_latest_regulation_id_with_annex(ses)
    try:
        rows = ses.execute(
            select(Rule.section_code, Rule.content, Rule.order_index)
            .where(
                Rule.regulation_id == regulation_id,
                Rule.section_code.like("AnnexIV.%")
            )
            .order_by(
                Rule.order_index.asc().nulls_last(),
                func.regexp_replace(
                    Rule.section_code, r"^AnnexIV\.(\d+).*$", r"\1",
                ).cast(Integer),
                Rule.section_code.asc(),
            )
        ).all()
    except Exception as exc:
        ses.rollback()
        raise RuntimeError("Failed to load Annex IV from DB") from exc

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


def get_expected_top_counts(
    ses: Session, regulation_id: Optional[str] = None, celex_id: Optional[str] = None
) -> Dict[str, int]:
    """Return expected number of top-level subpoints per section key."""
    if regulation_id is None:
        if celex_id:
            regulation_id = ses.execute(
                select(Regulation.id).where(Regulation.celex_id == celex_id)
            ).scalar_one_or_none()
            if not regulation_id:
                raise ValueError(f"CELEX {celex_id} not found")
        else:
            regulation_id = get_latest_regulation_id_with_annex(ses)
    try:
        rows = ses.execute(
            select(Rule.section_code)
            .where(Rule.regulation_id == regulation_id, Rule.section_code.like("AnnexIV.%"))
        ).scalars().all()
    except Exception as exc:
        ses.rollback()
        raise RuntimeError("Failed to load Annex IV section codes") from exc

    counts: dict[str, int] = defaultdict(int)
    for sc in rows:
        m = re.match(r"^AnnexIV\.(\d+)\.([a-z])$", sc, re.I)
        if not m:
            continue
        n = int(m.group(1))
        if 1 <= n <= len(SECTION_KEYS):
            counts[SECTION_KEYS[n - 1]] += 1
    return dict(counts)


def get_schema_version_from_db(
    ses: Session, regulation_id: Optional[str] = None, celex_id: Optional[str] = None
) -> Optional[str]:
    if regulation_id is None:
        if celex_id:
            regulation_id = ses.execute(
                select(Regulation.id).where(Regulation.celex_id == celex_id)
            ).scalar_one_or_none()
            if regulation_id is None:
                return None
        else:
            regulation_id = get_latest_regulation_id_with_annex(ses)
    return ses.execute(
        select(Regulation.version).where(Regulation.id == regulation_id)
    ).scalar_one_or_none()
