from unittest.mock import patch

import pymupdf
import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.catalog import upsert_paper
from app.db import Base, get_db
from app.main import app
from app.models import Document, Download, Paper, Reference, Search, SearchHit
from app.oa import download_pdf, validate_public_https
from app.references import extract_references
from app.sources import PaperRecord, citation_score, clean_doi, resolve_reference, search_pubmed


def test_reference_extraction_and_doi():
    pdf = pymupdf.open()
    page = pdf.new_page()
    page.insert_text((50, 50), "Synthetic medical article\n" + "Background " * 30 +
                     "\nReferences\n[1] Lee A. Example vaccine study. 2024. doi:10.1234/AbC.\n"
                     "[2] Chen B. Another illustrative study. 2023.")
    refs = extract_references(pdf.tobytes())
    assert len(refs) == 2
    assert refs[0][1] == "10.1234/abc"
    assert refs[1][1] is None
    assert clean_doi("https://doi.org/10.1000/XYZ.") == "10.1000/xyz"


def test_pubmed_xml_search():
    xml = b"""<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>123</PMID>
    <Article><ArticleTitle>Example vaccine effectiveness</ArticleTitle>
    <Journal><Title>Example Journal</Title><JournalIssue><PubDate><Year>2024</Year></PubDate></JournalIssue></Journal>
    <AuthorList><Author><ForeName>A</ForeName><LastName>Lee</LastName></Author></AuthorList></Article>
    </MedlineCitation><PubmedData><ArticleIdList><ArticleId IdType="doi">10.1234/EXAMPLE</ArticleId></ArticleIdList>
    </PubmedData></PubmedArticle></PubmedArticleSet>"""
    def handle(request):
        if "esearch" in str(request.url):
            return httpx.Response(200, json={"esearchresult": {"idlist": ["123"]}})
        return httpx.Response(200, content=xml)
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        papers = search_pubmed(client, "vaccine", 10)
    assert len(papers) == 1
    assert papers[0].pmid == "123"
    assert papers[0].doi == "10.1234/example"
    assert papers[0].year == 2024


def test_citation_score_rejects_wrong_year():
    paper = PaperRecord(title="Example vaccine effectiveness study", source="crossref", year=2024)
    assert citation_score("Lee. Example vaccine effectiveness study. 2024.", paper) == 1
    assert citation_score("Lee. Example vaccine effectiveness study. 2020.", paper) < 0.85


def test_pdf_download_validation():
    with pytest.raises(ValueError):
        validate_public_https("http://127.0.0.1/private.pdf")
    with pytest.raises(ValueError):
        validate_public_https("https://127.0.0.1/private.pdf")
    def handle(request):
        return httpx.Response(200, headers={"content-type": "application/pdf"}, content=b"%PDF-1.4\nsynthetic")
    with patch("app.oa.validate_public_https"):
        with httpx.Client(transport=httpx.MockTransport(handle)) as client:
            data, url = download_pdf(client, "https://example.org/test.pdf")
    assert data.startswith(b"%PDF")
    assert url == "https://example.org/test.pdf"


def test_search_persists_deduplicated_papers():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    def override_db():
        with Session(engine) as db:
            yield db
    app.dependency_overrides[get_db] = override_db
    try:
        with patch("app.main.search_sources", return_value=(
            [PaperRecord(title="Example vaccine study", source="pubmed", doi="10.1234/example", pmid="123"),
             PaperRecord(title="Example vaccine study", source="europepmc", doi="10.1234/example", pmid="123")], {})):
            result = TestClient(app).post("/api/searches", json={"query": "vaccine", "sources": ["pubmed", "europepmc"]})
        assert result.status_code == 200
        assert len(result.json()["papers"]) == 1
        saved = TestClient(app).get(f"/api/searches/{result.json()['id']}")
        assert len(saved.json()["papers"]) == 1
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def test_title_only_match_requires_review():
    candidates = [
        PaperRecord(title="Influenza vaccine", source="crossref", doi="10.1234/short"),
        PaperRecord(title="Influenza vaccine effectiveness in older adults", source="crossref", doi="10.1234/full"),
    ]
    with patch("app.sources.search_crossref", return_value=candidates):
        with httpx.Client() as client:
            paper, score, method = resolve_reference(client, "Influenza vaccine effectiveness in older adults. 2024.")
    assert paper is not None and score >= 0.85
    assert method == "review"
    assert clean_doi("doi:10.1234/test(2024).") == "10.1234/test(2024)"


def test_identifier_reconciliation_preserves_dependents():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        doi_paper = upsert_paper(db, PaperRecord(title="Example", source="crossref", doi="10.1234/example"))
        pmid_paper = upsert_paper(db, PaperRecord(title="Example", source="pubmed", pmid="123"))
        run = Search(query="Example", sources=["pubmed"])
        doc = Document(filename="synthetic.pdf", object_key="uploads/example", sha256="0" * 64)
        db.add_all([run, doc])
        db.flush()
        db.add_all([
            SearchHit(search_id=run.id, paper_id=pmid_paper.id, source="pubmed", rank=1),
            Reference(document_id=doc.id, ordinal=1, raw_text="Example", paper_id=pmid_paper.id),
            Download(paper_id=pmid_paper.id),
        ])
        db.flush()
        merged = upsert_paper(db, PaperRecord(title="Example", source="europepmc",
                                              doi="10.1234/example", pmid="123"))
        assert merged.id == doi_paper.id
        assert merged.pmid == "123"
        assert len(db.scalars(select(Paper)).all()) == 1
        assert db.scalar(select(SearchHit)).paper_id == merged.id
        assert db.scalar(select(Reference)).paper_id == merged.id
        assert db.scalar(select(Download)).paper_id == merged.id
    engine.dispose()


def test_reference_limit_is_explicit():
    pdf = pymupdf.open()
    for start in range(1, 502, 40):
        page = pdf.new_page()
        lines = ["References"] if start == 1 else []
        lines += [f"[{i}] Example synthetic reference with enough text. 2024."
                  for i in range(start, min(start + 40, 502))]
        page.insert_text((50, 50), "\n".join(lines), fontsize=10)
    with pytest.raises(ValueError, match="More than 500"):
        extract_references(pdf.tobytes())
