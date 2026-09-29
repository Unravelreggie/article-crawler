import hashlib
import time

import httpx
from celery import Celery
from sqlalchemy import select

from .catalog import upsert_paper
from .config import settings
from .db import SessionLocal
from .models import Document, Download, Paper, Reference
from .oa import download_pdf, oa_locations
from .references import extract_references
from .sources import resolve_reference
from .storage import get_pdf, put_pdf

celery_app = Celery("literature", broker=settings().redis_url, backend=settings().redis_url)
celery_app.conf.update(task_serializer="json", accept_content=["json"], result_serializer="json",
                       task_acks_late=True, worker_prefetch_multiplier=1)


@celery_app.task(name="parse_document")
def parse_document(document_id: str):
    with SessionLocal() as db:
        document = db.get(Document, document_id)
        if not document or document.status == "completed":
            return
        document.status = "processing"
        db.commit()
        try:
            citations = extract_references(get_pdf(document.object_key))
            if not citations:
                raise ValueError("No numbered references found")
            with httpx.Client() as client:
                for index, (text, doi) in enumerate(citations, 1):
                    ref = db.scalar(select(Reference).where(
                        Reference.document_id == document_id, Reference.ordinal == index))
                    if ref:
                        continue
                    ref = Reference(document_id=document_id, ordinal=index, raw_text=text, doi=doi)
                    db.add(ref)
                    try:
                        paper, score, method = resolve_reference(client, text)
                        if paper:
                            ref.paper_id = upsert_paper(db, paper).id
                            ref.doi = paper.doi or doi
                            ref.confidence = score
                            ref.match_source = paper.source
                            ref.status = "review" if method == "review" else "matched"
                        else:
                            ref.status = "unmatched"
                    except httpx.HTTPError:
                        ref.status = "unmatched"
                    db.commit()
                    time.sleep(0.12)  # Crossref polite-pool pacing for batch citation matching.
            document.status = "completed"
            db.commit()
        except Exception as exc:
            db.rollback()
            document = db.get(Document, document_id)
            document.status = "failed"
            document.error = str(exc)[:500]
            db.commit()
            raise


@celery_app.task(name="fetch_download")
def fetch_download(download_id: str):
    with SessionLocal() as db:
        item = db.get(Download, download_id)
        if not item or item.status == "completed":
            return
        item.status = "processing"
        db.commit()
        try:
            paper = db.get(Paper, item.paper_id)
            if not paper or not paper.doi:
                raise ValueError("A verified DOI is required for OA lookup")
            with httpx.Client() as client:
                locations = oa_locations(client, paper.doi)
                if paper.oa_pdf_url and paper.license:
                    from .oa import OALocation
                    locations.insert(0, OALocation(paper.oa_pdf_url, paper.license, paper.first_source))
                if not locations:
                    raise ValueError("No licensed open-access PDF location found")
                errors = []
                for location in locations:
                    try:
                        pdf, final_url = download_pdf(client, location.url)
                        digest = hashlib.sha256(pdf).hexdigest()
                        key = f"downloads/{paper.id}/{digest}.pdf"
                        put_pdf(key, pdf)
                        item.source_url = final_url
                        item.license = location.license
                        item.object_key = key
                        item.sha256 = digest
                        item.byte_size = len(pdf)
                        item.status = "completed"
                        db.commit()
                        return
                    except (httpx.HTTPError, ValueError, OSError) as exc:
                        errors.append(f"{location.source}: {type(exc).__name__}")
                raise ValueError("All OA locations failed: " + ", ".join(errors))
        except Exception as exc:
            db.rollback()
            item = db.get(Download, download_id)
            item.status = "failed"
            item.error = str(exc)[:500]
            db.commit()
            raise
