import logging

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .models import Download, Paper, Reference, SearchHit
from .sources import PaperRecord

logger = logging.getLogger(__name__)


def upsert_paper(db: Session, record: PaperRecord) -> Paper:
    by_doi = db.scalar(select(Paper).where(Paper.doi == record.doi)) if record.doi else None
    by_pmid = db.scalar(select(Paper).where(Paper.pmid == record.pmid)) if record.pmid else None

    if by_doi and by_pmid and by_doi.id != by_pmid.id:
        if (by_doi.pmid and by_doi.pmid != record.pmid) or (by_pmid.doi and by_pmid.doi != record.doi):
            logger.warning("Conflicting DOI/PMID for paper; preserving separate records")
        else:
            for hit in db.scalars(select(SearchHit).where(SearchHit.paper_id == by_pmid.id)).all():
                duplicate = db.scalar(select(SearchHit).where(
                    SearchHit.search_id == hit.search_id, SearchHit.paper_id == by_doi.id,
                    SearchHit.source == hit.source))
                if duplicate:
                    db.delete(hit)
                else:
                    hit.paper_id = by_doi.id
            db.execute(update(Reference).where(Reference.paper_id == by_pmid.id).values(paper_id=by_doi.id))
            db.execute(update(Download).where(Download.paper_id == by_pmid.id).values(paper_id=by_doi.id))
            for name in ("title", "authors", "year", "journal", "abstract", "landing_url",
                         "oa_pdf_url", "license", "pmcid"):
                setattr(by_doi, name, getattr(by_doi, name) or getattr(by_pmid, name))
            db.delete(by_pmid)
            db.flush()
            by_pmid = None

    paper = by_doi or by_pmid
    if paper is None:
        paper = Paper(title=record.title, first_source=record.source)
        db.add(paper)
    paper.title = paper.title or record.title
    if record.doi and not (by_pmid and by_pmid.id != paper.id):
        paper.doi = paper.doi or record.doi
    if record.pmid and not (by_doi and by_doi.id != paper.id and by_doi.pmid):
        # A conflicting identifier remains on its original row for manual reconciliation.
        paper.pmid = paper.pmid or (record.pmid if by_pmid is None or by_pmid.id == paper.id else None)
    paper.pmcid = paper.pmcid or record.pmcid
    paper.authors = paper.authors or record.authors
    paper.year = paper.year or record.year
    paper.journal = paper.journal or record.journal
    paper.abstract = paper.abstract or record.abstract
    paper.landing_url = paper.landing_url or record.landing_url
    if record.oa_pdf_url and record.license:
        paper.oa_pdf_url = paper.oa_pdf_url or record.oa_pdf_url
        paper.license = paper.license or record.license
    db.flush()
    return paper
