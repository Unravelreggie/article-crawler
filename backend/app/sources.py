"""Structured literature APIs. HTML scraping is deliberately outside discovery."""
import re
import time
import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from urllib.parse import quote

import httpx

from .config import settings

DOI_RE = re.compile(r"10\.\d{4,9}/[^\s<>]+", re.I)
SOURCES = {"pubmed", "europepmc", "openalex", "crossref"}
HEADERS = {"User-Agent": "article-crawler/0.1 (medical literature research)"}


@dataclass
class PaperRecord:
    title: str
    source: str
    doi: str | None = None
    pmid: str | None = None
    pmcid: str | None = None
    authors: list[str] = field(default_factory=list)
    year: int | None = None
    journal: str | None = None
    abstract: str | None = None
    landing_url: str | None = None
    oa_pdf_url: str | None = None
    license: str | None = None


def clean_doi(value: str | None) -> str | None:
    if not value:
        return None
    value = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", value.strip(), flags=re.I)
    match = DOI_RE.search(value)
    if not match:
        return None
    doi = match.group(0).rstrip(".,;")
    for closing, opening in ((")", "("), ("]", "["), ("}", "{")):
        while doi.endswith(closing) and doi.count(closing) > doi.count(opening):
            doi = doi[:-1]
    return doi.lower()


def year_of(value) -> int | None:
    match = re.search(r"(?:19|20)\d{2}", str(value or ""))
    return int(match.group()) if match else None


def _get(client: httpx.Client, url: str, params: dict | None = None) -> httpx.Response:
    for attempt in range(3):
        response = client.get(url, params=params, headers=HEADERS, timeout=20)
        if response.status_code not in {429, 502, 503, 504} or attempt == 2:
            response.raise_for_status()
            return response
        retry_after = response.headers.get("Retry-After", "")
        delay = min(float(retry_after), 5) if retry_after.isdigit() else 0.5 * (attempt + 1)
        time.sleep(delay)
    raise AssertionError("unreachable")

def search_pubmed(client: httpx.Client, query: str, limit: int) -> list[PaperRecord]:
    cfg = settings()
    params = {"db": "pubmed", "term": query, "retmode": "json", "retmax": limit, "sort": "relevance"}
    if cfg.contact_email:
        params["email"] = cfg.contact_email
    if cfg.ncbi_api_key:
        params["api_key"] = cfg.ncbi_api_key
    data = _get(client, "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi", params).json()
    ids = data.get("esearchresult", {}).get("idlist", [])
    if not ids:
        return []
    params = {"db": "pubmed", "id": ",".join(ids), "retmode": "xml"}
    if cfg.contact_email:
        params["email"] = cfg.contact_email
    if cfg.ncbi_api_key:
        params["api_key"] = cfg.ncbi_api_key
    root = ET.fromstring(_get(client, "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi", params).content)
    papers = []
    for item in root.findall(".//PubmedArticle"):
        citation = item.find("./MedlineCitation")
        if citation is None:
            continue
        title_node = citation.find("./Article/ArticleTitle")
        title = "".join(title_node.itertext()).strip() if title_node is not None else ""
        if not title:
            continue
        ids_map = {x.get("IdType"): (x.text or "") for x in item.findall("./PubmedData/ArticleIdList/ArticleId")}
        pmid = ids_map.get("pubmed") or citation.findtext("./PMID")
        authors = []
        for author in citation.findall("./Article/AuthorList/Author"):
            name = " ".join(filter(None, [author.findtext("ForeName"), author.findtext("LastName")]))
            if name:
                authors.append(name)
        abstract = " ".join("".join(x.itertext()).strip() for x in citation.findall("./Article/Abstract/AbstractText"))
        journal = citation.findtext("./Article/Journal/Title")
        date_text = citation.findtext("./Article/Journal/JournalIssue/PubDate/Year") or citation.findtext("./Article/Journal/JournalIssue/PubDate/MedlineDate")
        papers.append(PaperRecord(
            title=title, source="pubmed", doi=clean_doi(ids_map.get("doi")), pmid=pmid,
            pmcid=ids_map.get("pmc"), authors=authors, year=year_of(date_text),
            journal=journal, abstract=abstract or None,
            landing_url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else None,
        ))
    return papers


def search_europepmc(client: httpx.Client, query: str, limit: int) -> list[PaperRecord]:
    data = _get(client, "https://www.ebi.ac.uk/europepmc/webservices/rest/search",
                {"query": query, "format": "json", "resultType": "core", "pageSize": limit}).json()
    papers = []
    for item in data.get("resultList", {}).get("result", []):
        urls = item.get("fullTextUrlList") or {}
        links = urls.get("fullTextUrl") or []
        pdf = next((x.get("url") for x in links if x.get("documentStyle", "").lower() == "pdf"
                    and x.get("availability", "").lower() == "open access"), None)
        papers.append(PaperRecord(
            title=item.get("title", ""), source="europepmc", doi=clean_doi(item.get("doi")),
            pmid=item.get("pmid"), pmcid=item.get("pmcid"),
            authors=[x.strip() for x in (item.get("authorString") or "").split(",") if x.strip()],
            year=year_of(item.get("pubYear")), journal=item.get("journalTitle"),
            abstract=item.get("abstractText"),
            landing_url=f"https://europepmc.org/article/{item.get('source', 'MED')}/{item.get('id')}",
            oa_pdf_url=pdf, license=item.get("license"),
        ))
    return [p for p in papers if p.title]


def search_openalex(client: httpx.Client, query: str, limit: int) -> list[PaperRecord]:
    params = {"search": query, "per_page": limit}
    if settings().openalex_api_key:
        params["api_key"] = settings().openalex_api_key
    data = _get(client, "https://api.openalex.org/works", params).json()
    papers = []
    for item in data.get("results", []):
        ids = item.get("ids") or {}
        loc = item.get("best_oa_location") or {}
        papers.append(PaperRecord(
            title=item.get("display_name", ""), source="openalex",
            doi=clean_doi(item.get("doi")), pmid=(ids.get("pmid") or "").rsplit("/", 1)[-1] or None,
            pmcid=(ids.get("pmcid") or "").rsplit("/", 1)[-1] or None,
            authors=[x.get("author", {}).get("display_name", "") for x in item.get("authorships", []) if x.get("author")],
            year=year_of(item.get("publication_year")),
            journal=((item.get("primary_location") or {}).get("source") or {}).get("display_name"),
            landing_url=item.get("id"), oa_pdf_url=loc.get("pdf_url"),
            license=loc.get("license"),
        ))
    return [p for p in papers if p.title]


def _crossref_record(item: dict) -> PaperRecord:
    title = (item.get("title") or [""])[0]
    issued = (item.get("published") or item.get("issued") or {}).get("date-parts") or []
    return PaperRecord(
        title=title, source="crossref", doi=clean_doi(item.get("DOI")),
        authors=[" ".join(filter(None, [a.get("given"), a.get("family")])) for a in item.get("author", [])],
        year=year_of(issued[0][0] if issued and issued[0] else None),
        journal=(item.get("container-title") or [None])[0],
        landing_url=item.get("URL"),
    )


def search_crossref(client: httpx.Client, query: str, limit: int) -> list[PaperRecord]:
    params = {"query.bibliographic": query, "rows": limit}
    if settings().contact_email:
        params["mailto"] = settings().contact_email
    items = _get(client, "https://api.crossref.org/works", params).json().get("message", {}).get("items", [])
    return [p for p in map(_crossref_record, items) if p.title]


def crossref_by_doi(client: httpx.Client, doi: str) -> PaperRecord | None:
    try:
        item = _get(client, f"https://api.crossref.org/works/{quote(doi, safe='')}").json()["message"]
        return _crossref_record(item)
    except (httpx.HTTPError, KeyError, ValueError):
        return None


def search(client: httpx.Client, query: str, sources: list[str], limit: int) -> tuple[list[PaperRecord], dict[str, str]]:
    functions = {"pubmed": search_pubmed, "europepmc": search_europepmc,
                 "openalex": search_openalex, "crossref": search_crossref}
    results, errors = [], {}
    for source in sources:
        try:
            results.extend(functions[source](client, query, limit))
        except (httpx.HTTPError, ET.ParseError, ValueError, KeyError) as exc:
            errors[source] = type(exc).__name__
    return results, errors


def _terms(value: str) -> set[str]:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return {x for x in re.findall(r"[a-z0-9]{3,}|[\u4e00-\u9fff]+", normalized)
            if x not in {"the", "and", "for", "with", "from", "study", "article"}}


def citation_score(citation: str, paper: PaperRecord) -> float:
    title_terms = _terms(paper.title)
    if not title_terms:
        return 0.0
    coverage = len(title_terms & _terms(citation)) / len(title_terms)
    citation_year = year_of(citation)
    if citation_year and paper.year and citation_year != paper.year:
        coverage *= 0.7
    return round(coverage, 3)


def resolve_reference(client: httpx.Client, citation: str) -> tuple[PaperRecord | None, float, str]:
    doi = clean_doi(citation)
    if doi:
        paper = crossref_by_doi(client, doi)
        return (paper, 1.0, "doi") if paper else (None, 0.0, "doi_unresolved")
    candidates = search_crossref(client, citation[:300], 3)
    scored = sorted(((citation_score(citation, p), p) for p in candidates), key=lambda x: x[0], reverse=True)
    # Title-only matches require a human check, even when every candidate title word appears.
    return (scored[0][1], scored[0][0], "review") if scored and scored[0][0] >= 0.55 else (None, 0.0, "unmatched")
