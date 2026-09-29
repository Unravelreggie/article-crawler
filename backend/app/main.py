import hashlib
from io import BytesIO
from pathlib import Path
from typing import Literal

import httpx
from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .catalog import upsert_paper
from .config import settings
from .db import get_db
from .jobs import fetch_download, parse_document
from .models import Document, Download, Paper, Reference, Search, SearchHit
from .sources import search as search_sources
from .storage import get_pdf, put_pdf

app = FastAPI(title="Medical literature search and reference crawler", version="0.1.0")


class SearchRequest(BaseModel):
    query: str = Field(min_length=3, max_length=500)
    sources: list[Literal["pubmed", "europepmc", "openalex", "crossref"]] = Field(default=["pubmed", "europepmc", "openalex"])
    limit_per_source: int = Field(default=20, ge=1, le=50)


class MatchRequest(BaseModel):
    paper_id: str


class DownloadRequest(BaseModel):
    paper_ids: list[str] = Field(min_length=1, max_length=100)


def paper_view(paper: Paper) -> dict:
    return {
        "id": paper.id, "title": paper.title, "doi": paper.doi, "pmid": paper.pmid,
        "pmcid": paper.pmcid, "authors": paper.authors, "year": paper.year,
        "journal": paper.journal, "abstract": paper.abstract,
        "landing_url": paper.landing_url, "first_source": paper.first_source,
        "license": paper.license,
    }


def download_view(item: Download, paper: Paper | None = None) -> dict:
    return {
        "id": item.id, "paper_id": item.paper_id, "paper_title": paper.title if paper else None,
        "status": item.status, "source_url": item.source_url, "license": item.license,
        "sha256": item.sha256, "byte_size": item.byte_size, "error": item.error,
        "created_at": item.created_at,
    }


@app.get("/api/health")
def health(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {"status": "ok"}


@app.get("/api/searches")
def list_searches(db: Session = Depends(get_db)):
    runs = db.scalars(select(Search).order_by(Search.created_at.desc()).limit(30)).all()
    return {"searches": [{"id": run.id, "query": run.query, "created_at": run.created_at}
                         for run in runs]}


@app.post("/api/searches")
def create_search(body: SearchRequest, db: Session = Depends(get_db)):
    query = body.query.strip()
    sources = list(dict.fromkeys(body.sources))
    if not query or not sources:
        raise HTTPException(422, "Query and at least one source are required")
    with httpx.Client() as client:
        records, errors = search_sources(client, query, sources, body.limit_per_source)
    if len(errors) == len(sources):
        raise HTTPException(502, {"message": "All literature sources failed", "sources": errors})
    run = Search(query=query, sources=sources)
    db.add(run)
    db.flush()
    seen = set()
    for rank, record in enumerate(records, 1):
        paper = upsert_paper(db, record)
        key = (paper.id, record.source)
        if key not in seen:
            db.add(SearchHit(search_id=run.id, paper_id=paper.id, source=record.source, rank=rank))
            seen.add(key)
    db.commit()
    return {"id": run.id, "query": query, "sources": sources, "errors": errors,
            "papers": [paper_view(p) for p in db.scalars(
                select(Paper).join(SearchHit).where(SearchHit.search_id == run.id)
                .order_by(SearchHit.rank)).unique()]}


@app.get("/api/searches/{search_id}")
def get_search(search_id: str, db: Session = Depends(get_db)):
    run = db.get(Search, search_id)
    if not run:
        raise HTTPException(404, "Search not found")
    papers = db.scalars(select(Paper).join(SearchHit).where(SearchHit.search_id == search_id)
                        .order_by(SearchHit.rank)).unique()
    return {"id": run.id, "query": run.query, "sources": run.sources,
            "created_at": run.created_at, "papers": [paper_view(p) for p in papers]}


@app.get("/api/documents")
def list_documents(db: Session = Depends(get_db)):
    docs = db.scalars(select(Document).order_by(Document.created_at.desc()).limit(30)).all()
    return {"documents": [{"id": doc.id, "filename": doc.filename,
                           "status": doc.status, "created_at": doc.created_at}
                          for doc in docs]}


@app.post("/api/documents", status_code=202)
async def upload_document(file: UploadFile = File(...), db: Session = Depends(get_db)):
    filename = Path(file.filename or "document.pdf").name[:255]
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(422, "PDF file required")
    data = await file.read(settings().max_upload_mb * 1024 * 1024 + 1)
    if len(data) > settings().max_upload_mb * 1024 * 1024:
        raise HTTPException(413, "PDF too large")
    if not data.startswith(b"%PDF"):
        raise HTTPException(422, "Invalid PDF")
    digest = hashlib.sha256(data).hexdigest()
    document = Document(filename=filename, object_key="pending", sha256=digest)
    db.add(document)
    db.flush()
    document.object_key = f"uploads/{document.id}/{digest}.pdf"
    put_pdf(document.object_key, data)
    db.commit()
    try:
        parse_document.delay(document.id)
    except Exception as exc:
        document.status = "failed"
        document.error = f"Queue unavailable: {type(exc).__name__}"
        db.commit()
        raise HTTPException(503, "Worker queue unavailable") from exc
    return {"id": document.id, "status": document.status, "sha256": digest}


@app.get("/api/documents/{document_id}")
def get_document(document_id: str, db: Session = Depends(get_db)):
    doc = db.get(Document, document_id)
    if not doc:
        raise HTTPException(404, "Document not found")
    refs = db.scalars(select(Reference).where(Reference.document_id == document_id)
                      .order_by(Reference.ordinal)).all()
    return {"id": doc.id, "filename": doc.filename, "status": doc.status, "error": doc.error,
            "sha256": doc.sha256, "references": [
                {"id": ref.id, "ordinal": ref.ordinal, "raw_text": ref.raw_text,
                 "doi": ref.doi, "status": ref.status, "confidence": ref.confidence,
                 "match_source": ref.match_source,
                 "paper": paper_view(db.get(Paper, ref.paper_id)) if ref.paper_id else None}
                for ref in refs]}


@app.post("/api/references/{reference_id}/match")
def match_reference(reference_id: str, body: MatchRequest, db: Session = Depends(get_db)):
    ref = db.get(Reference, reference_id)
    paper = db.get(Paper, body.paper_id)
    if not ref or not paper:
        raise HTTPException(404, "Reference or paper not found")
    if not paper.doi:
        raise HTTPException(422, "Matched paper needs a verified DOI")
    ref.paper_id = paper.id
    ref.doi = paper.doi
    ref.status = "matched"
    ref.match_source = "manual"
    ref.confidence = 1.0
    db.commit()
    return {"id": ref.id, "paper": paper_view(paper), "status": ref.status}


@app.post("/api/downloads", status_code=202)
def create_downloads(body: DownloadRequest, db: Session = Depends(get_db)):
    ids = list(dict.fromkeys(body.paper_ids))
    papers = db.scalars(select(Paper).where(Paper.id.in_(ids))).all()
    if len(papers) != len(ids):
        raise HTTPException(404, "One or more papers not found")
    if any(not p.doi for p in papers):
        raise HTTPException(422, "Every paper needs a verified DOI")
    jobs = []
    new_jobs = []
    for paper in papers:
        existing = db.scalar(select(Download).where(
            Download.paper_id == paper.id, Download.status.in_(["queued", "processing", "completed"]))
            .order_by(Download.created_at.desc()))
        if existing:
            jobs.append(existing)
        else:
            job = Download(paper_id=paper.id)
            db.add(job)
            db.flush()
            jobs.append(job)
            new_jobs.append(job)
    db.commit()
    for job in new_jobs:
        try:
            fetch_download.delay(job.id)
        except Exception as exc:
            job.status = "failed"
            job.error = f"Queue unavailable: {type(exc).__name__}"
            db.commit()
    return {"downloads": [download_view(job, db.get(Paper, job.paper_id)) for job in jobs]}


@app.get("/api/downloads")
def list_downloads(db: Session = Depends(get_db)):
    jobs = db.scalars(select(Download).order_by(Download.created_at.desc()).limit(100)).all()
    return {"downloads": [download_view(job, db.get(Paper, job.paper_id)) for job in jobs]}


@app.get("/api/downloads/{download_id}/file")
def download_file(download_id: str, db: Session = Depends(get_db)):
    job = db.get(Download, download_id)
    if not job or job.status != "completed" or not job.object_key:
        raise HTTPException(404, "Downloaded PDF not found")
    return StreamingResponse(BytesIO(get_pdf(job.object_key)), media_type="application/pdf",
                             headers={"Content-Disposition": f'attachment; filename="{job.paper_id}.pdf"'})
