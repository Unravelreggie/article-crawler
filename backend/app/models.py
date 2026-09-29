from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def uid() -> str:
    return str(uuid4())


def now() -> datetime:
    return datetime.now(timezone.utc)


class Search(Base):
    __tablename__ = "searches"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    query: Mapped[str] = mapped_column(Text)
    sources: Mapped[list] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Paper(Base):
    __tablename__ = "papers"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    doi: Mapped[str | None] = mapped_column(String(255), unique=True)
    pmid: Mapped[str | None] = mapped_column(String(32), unique=True)
    pmcid: Mapped[str | None] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(Text)
    authors: Mapped[list] = mapped_column(JSON, default=list)
    year: Mapped[int | None] = mapped_column(Integer)
    journal: Mapped[str | None] = mapped_column(Text)
    abstract: Mapped[str | None] = mapped_column(Text)
    landing_url: Mapped[str | None] = mapped_column(Text)
    oa_pdf_url: Mapped[str | None] = mapped_column(Text)
    license: Mapped[str | None] = mapped_column(String(255))
    first_source: Mapped[str] = mapped_column(String(40))
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class SearchHit(Base):
    __tablename__ = "search_hits"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    search_id: Mapped[str] = mapped_column(ForeignKey("searches.id", ondelete="CASCADE"))
    paper_id: Mapped[str] = mapped_column(ForeignKey("papers.id"))
    source: Mapped[str] = mapped_column(String(40))
    rank: Mapped[int] = mapped_column(Integer)
    __table_args__ = (UniqueConstraint("search_id", "paper_id", "source"),)


class Document(Base):
    __tablename__ = "documents"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    filename: Mapped[str] = mapped_column(String(255))
    object_key: Mapped[str] = mapped_column(String(255))
    sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24), default="queued")
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Reference(Base):
    __tablename__ = "references"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    ordinal: Mapped[int] = mapped_column(Integer)
    raw_text: Mapped[str] = mapped_column(Text)
    doi: Mapped[str | None] = mapped_column(String(255))
    paper_id: Mapped[str | None] = mapped_column(ForeignKey("papers.id"))
    match_source: Mapped[str | None] = mapped_column(String(40))
    confidence: Mapped[float | None] = mapped_column()
    status: Mapped[str] = mapped_column(String(24), default="unmatched")
    __table_args__ = (UniqueConstraint("document_id", "ordinal"),)


class Download(Base):
    __tablename__ = "downloads"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    paper_id: Mapped[str] = mapped_column(ForeignKey("papers.id"))
    status: Mapped[str] = mapped_column(String(24), default="queued")
    source_url: Mapped[str | None] = mapped_column(Text)
    license: Mapped[str | None] = mapped_column(String(255))
    object_key: Mapped[str | None] = mapped_column(String(255))
    sha256: Mapped[str | None] = mapped_column(String(64))
    byte_size: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
