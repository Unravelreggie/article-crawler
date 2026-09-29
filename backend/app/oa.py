"""Resolve licensed open-access PDFs and verify downloaded bytes."""
import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import quote, urljoin, urlparse

import httpx

from .config import settings
from .sources import _get, clean_doi

MAX_PDF_BYTES = 50 * 1024 * 1024


@dataclass(frozen=True)
class OALocation:
    url: str
    license: str
    source: str


def oa_locations(client: httpx.Client, doi: str) -> list[OALocation]:
    doi = clean_doi(doi)
    if not doi:
        return []
    found: list[OALocation] = []
    try:
        data = _get(client, "https://www.ebi.ac.uk/europepmc/webservices/rest/search",
                    {"query": f"DOI:{doi}", "format": "json", "resultType": "core", "pageSize": 5}).json()
        for item in data.get("resultList", {}).get("result", []):
            if clean_doi(item.get("doi")) != doi or item.get("isOpenAccess") != "Y":
                continue
            license_id = item.get("license")
            links = (item.get("fullTextUrlList") or {}).get("fullTextUrl") or []
            for link in links:
                if link.get("documentStyle", "").lower() == "pdf" and link.get("availability", "").lower() == "open access" and license_id:
                    found.append(OALocation(link["url"], license_id, "europepmc"))
    except (httpx.HTTPError, ValueError, KeyError):
        pass
    if settings().contact_email:
        try:
            data = _get(client, f"https://api.unpaywall.org/v2/{quote(doi, safe='')}",
                        {"email": settings().contact_email}).json()
            loc = data.get("best_oa_location") or {}
            if data.get("is_oa") and loc.get("url_for_pdf") and loc.get("license"):
                found.append(OALocation(loc["url_for_pdf"], loc["license"], "unpaywall"))
        except (httpx.HTTPError, ValueError, KeyError):
            pass
    try:
        params = {"filter": f"doi:{doi}", "per_page": 1}
        if settings().openalex_api_key:
            params["api_key"] = settings().openalex_api_key
        data = _get(client, "https://api.openalex.org/works", params).json()
        for item in data.get("results", []):
            if clean_doi(item.get("doi")) != doi:
                continue
            loc = item.get("best_oa_location") or {}
            if loc.get("pdf_url") and loc.get("license"):
                found.append(OALocation(loc["pdf_url"], loc["license"], "openalex"))
    except (httpx.HTTPError, ValueError, KeyError):
        pass
    return list(dict.fromkeys(found))


def validate_public_https(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Only public HTTPS PDF URLs are allowed")
    try:
        addresses = socket.getaddrinfo(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ValueError("Cannot resolve PDF host") from exc
    # Some local TUN proxies map public hosts into RFC 2544 benchmark addresses.
    fake_dns = ipaddress.ip_network("198.18.0.0/15")
    allowed = lambda address: address.is_global or (settings().allow_fake_ip_dns and address in fake_dns)
    if not addresses or any(not allowed(ipaddress.ip_address(address[4][0])) for address in addresses):
        raise ValueError("PDF host resolves to a non-public address")


def download_pdf(client: httpx.Client, url: str) -> tuple[bytes, str]:
    current = url
    for _ in range(6):
        validate_public_https(current)
        with client.stream("GET", current, follow_redirects=False, timeout=45, headers={"Accept": "application/pdf"}) as response:
            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("location")
                if not location:
                    raise ValueError("Redirect without Location")
                current = urljoin(current, location)
                continue
            response.raise_for_status()
            content_type = response.headers.get("content-type", "").split(";")[0].lower()
            if content_type and content_type not in {"application/pdf", "application/octet-stream", "application/x-pdf"}:
                raise ValueError(f"Unexpected content type: {content_type}")
            data = bytearray()
            for chunk in response.iter_bytes():
                data.extend(chunk)
                if len(data) > MAX_PDF_BYTES:
                    raise ValueError("PDF exceeds 50 MB")
            if b"%PDF" not in data[:1024]:
                raise ValueError("Response is not a PDF")
            return bytes(data), str(response.url)
    raise ValueError("Too many redirects")
