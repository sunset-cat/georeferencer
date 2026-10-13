import asyncio
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from georeferencer.api import create_app
from georeferencer.config import Settings
from georeferencer.download import download_document, public_target
from georeferencer.extraction import FixtureExtractor
from georeferencer.library import LibraryStore, decode_document
from georeferencer.pipeline import Pipeline

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def app(tmp_path):
    settings = Settings(log_dir=tmp_path / "logs", library_dir=tmp_path / "library",
                        registry_path=FIXTURES / "demo.registry.json")
    return create_app(Pipeline(settings, FixtureExtractor(FIXTURES / "demo.extraction.json")))


def test_library_upload_persistence_provenance_history_and_downloads(tmp_path):
    application = app(tmp_path)
    with TestClient(application) as client:
        assert client.get("/").status_code == 200
        assert "Document library" in client.get("/").text
        assert client.get("/assets/app.js").status_code == 200
        assert client.get("/favicon.ico").status_code == 200
        assert client.get("/.well-known/appspecific/com.chrome.devtools.json").status_code == 204
        response = client.post("/library/upload", files={"file": ("demo.txt", (FIXTURES / "demo.txt").read_bytes(), "text/plain")})
        assert response.status_code == 202
        identifier = response.json()["id"]
        document = client.get(f"/library/{identifier}").json()
        assert document["status"] == "partial"
        assert document["entity_count"] == 4
        assert document["original_available"]
        result = document["result"]
        assert result["entities"][0]["geometry_provenance"]["origin"] == "document"
        assert result["entities"][2]["geometry_provenance"]["origin"] == "external"
        assert result["entities"][2]["geometry_provenance"]["source_record_id"] == "P-123"
        features = client.get(f"/library/{identifier}/geojson").json()["features"]
        assert features[0]["properties"]["geometry_provenance"]["origin"] == "document"
        assert client.get(f"/library/{identifier}/original").content == (FIXTURES / "demo.txt").read_bytes()
        events = client.get(f"/library/{identifier}/events").json()
        assert any(e["event"] == "source.request" for e in events)
        assert client.post(f"/library/{identifier}/retry").status_code == 202
        assert len(client.get(f"/library/{identifier}/runs").json()) == 2
    with TestClient(app(tmp_path)) as client:
        assert client.get("/library").json()[0]["id"] == identifier
        assert len(client.get(f"/library/{identifier}/runs").json()) == 2


def test_demo_works_without_model_or_registry_configuration(tmp_path):
    settings = Settings(log_dir=tmp_path / "logs", library_dir=tmp_path / "library")
    from georeferencer.extraction import OpenAIExtractor
    with TestClient(create_app(Pipeline(settings, OpenAIExtractor(settings)))) as client:
        identifier = client.post("/library/demo").json()["id"]
        document = client.get(f"/library/{identifier}").json()
        assert document["feature_count"] == 2
        assert document["result"]["usage"]["model_calls"] == 0
        client.post(f"/library/{identifier}/retry")
        assert client.get(f"/library/{identifier}").json()["feature_count"] == 2


def test_excluded_document_is_hidden_but_original_and_history_are_retained(tmp_path):
    with TestClient(app(tmp_path)) as client:
        identifier = client.post("/library/demo").json()["id"]
        store = client.app.state.library.store
        store.exclude(identifier, "Financial document; outside representative sample")
        assert identifier not in {d["id"] for d in client.get("/library").json()}
        assert client.get(f"/library/{identifier}/original").status_code == 200
        assert len(client.get(f"/library/{identifier}/runs").json()) == 1
        assert store.get(identifier)["excluded_from_library"] == 1
    with TestClient(app(tmp_path)) as client:
        assert identifier not in {d["id"] for d in client.get("/library").json()}


def test_import_failures_are_stored_and_retry_is_guarded(tmp_path):
    with TestClient(app(tmp_path)) as client:
        identifier = client.post("/library/upload", files={"file": ("bad.bin", b"\x00\xff")}).json()["id"]
        document = client.get(f"/library/{identifier}").json()
        assert document["status"] == "failed"
        assert document["result"]["issues"][0]["code"] == "document_import_error"
        pending = client.app.state.library.store.create("pending.txt", content=b"pending")
        assert client.post(f"/library/{pending}/retry").status_code == 409
        assert client.get("/library/does-not-exist").status_code == 404
        assert client.post("/library/upload", files={"file": ("empty.txt", b"")}).status_code == 413
        assert client.post("/library/url", json={"url": "file:///etc/passwd"}).status_code == 422


def test_url_import_downloads_and_persists_original(tmp_path, monkeypatch):
    async def download(url, max_bytes, timeout):
        return (FIXTURES / "demo.txt").read_bytes(), "text/plain", url
    monkeypatch.setattr("georeferencer.library.download_document", download)
    with TestClient(app(tmp_path)) as client:
        response = client.post("/library/url", json={"url": "https://documents.example/demo.txt"})
        identifier = response.json()["id"]
        document = client.get(f"/library/{identifier}").json()
        assert document["source_url"] == "https://documents.example/demo.txt"
        assert document["feature_count"] == 2
        assert client.get(f"/library/{identifier}/original").content == (FIXTURES / "demo.txt").read_bytes()


def test_private_url_import_fails_visibly(tmp_path):
    with TestClient(app(tmp_path)) as client:
        response = client.post("/library/url", json={"url": "http://127.0.0.1/private"})
        document = client.get(f"/library/{response.json()['id']}").json()
        assert document["status"] == "failed"
        assert "private" in document["result"]["issues"][0]["message"]
        assert not document["original_available"]


def test_download_connection_error_is_actionable(tmp_path, monkeypatch):
    from georeferencer.download import DownloadConnectionError
    async def download(*args):
        raise DownloadConnectionError("Cannot connect: server outbound-network access blocked")
    monkeypatch.setattr("georeferencer.library.download_document", download)
    with TestClient(app(tmp_path)) as client:
        identifier = client.post("/library/url", json={"url": "https://documents.example/test"}).json()["id"]
        result = client.get(f"/library/{identifier}").json()["result"]
        issue = result["issues"][0]
        assert issue["code"] == "download_connection_error"
        assert issue["stage"] == "download"
        assert issue["retryable"]


def test_capabilities_do_not_expose_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "secret-do-not-return")
    with TestClient(app(tmp_path)) as client:
        response = client.get("/capabilities")
        assert response.json()["api_key_configured"]
        assert "secret-do-not-return" not in response.text


def test_pdf_url_returning_html_is_reported_before_pdf_parsing():
    with pytest.raises(ValueError, match="Expected a PDF.*HTML"):
        decode_document(b'<!DOCTYPE html><html>Please wait while your request is being verified</html>',
                        "https://survey.example/report.pdf", "text/html")


def test_failed_download_parse_preserves_original_for_retry(tmp_path, monkeypatch):
    calls = []
    async def download(url, max_bytes, timeout):
        calls.append(url)
        return b"\x00\xff", "application/octet-stream", url
    monkeypatch.setattr("georeferencer.library.download_document", download)
    with TestClient(app(tmp_path)) as client:
        identifier = client.post("/library/url", json={"url": "https://documents.example/bad.bin"}).json()["id"]
        assert client.get(f"/library/{identifier}").json()["status"] == "failed"
        assert client.get(f"/library/{identifier}/original").content == b"\x00\xff"
        client.post(f"/library/{identifier}/retry")
        assert len(calls) == 1


def test_restart_marks_incomplete_jobs_interrupted(tmp_path):
    store = LibraryStore(tmp_path)
    identifier = store.create("pending.txt", content=b"pending")
    store.recover()
    assert store.get(identifier)["status"] == "interrupted"
    assert store.claim_retry(identifier)
    assert not store.claim_retry(identifier)


def test_pdf_text_support_and_scanned_failure():
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
    from io import BytesIO
    writer = PdfWriter()
    page = writer.add_blank_page(width=300, height=300)
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 20 200 Td (Example Ridge in Canada) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(stream)
    output = BytesIO()
    writer.write(output)
    text, display_format, pipeline_format = decode_document(output.getvalue(), "document.pdf")
    assert "Example Ridge" in text
    assert (display_format, pipeline_format) == ("pdf", "text")
    for _ in range(150):
        writer.add_blank_page(width=300, height=300)
    large = BytesIO()
    writer.write(large)
    with pytest.raises(ValueError, match="150-page"):
        decode_document(large.getvalue(), "large.pdf")
    assert "Example Ridge" in decode_document(large.getvalue(), "large.pdf", max_pdf_pages=1000)[0]
    blank = PdfWriter()
    blank.add_blank_page(width=100, height=100)
    output = BytesIO()
    blank.write(output)
    with pytest.raises(ValueError, match="OCR"):
        decode_document(output.getvalue(), "scanned.pdf")


def test_download_pins_checked_address_and_limits_size(monkeypatch):
    import socket
    monkeypatch.setattr("georeferencer.download.socket.getaddrinfo", lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))])
    client_type = httpx.AsyncClient
    def handler(request):
        assert request.url.host == "93.184.216.34"
        assert request.headers["host"] == "documents.example"
        assert request.extensions["sni_hostname"] == "documents.example"
        return httpx.Response(200, content=b"example", headers={"content-type": "text/plain"})
    monkeypatch.setattr("georeferencer.download.httpx.AsyncClient", lambda **kwargs: client_type(transport=httpx.MockTransport(handler), **kwargs))
    assert asyncio.run(download_document("https://documents.example/a.txt", 20, 5))[0] == b"example"
    with pytest.raises(ValueError, match="size limit"):
        asyncio.run(download_document("https://documents.example/a.txt", 3, 5))


def test_redirects_are_revalidated(monkeypatch):
    import socket
    def resolve(host, *args, **kwargs):
        ip = "127.0.0.1" if host == "localhost" else "93.184.216.34"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443))]
    monkeypatch.setattr("georeferencer.download.socket.getaddrinfo", resolve)
    client_type = httpx.AsyncClient
    def handler(request):
        return httpx.Response(302, headers={"location": "http://localhost/private"})
    monkeypatch.setattr("georeferencer.download.httpx.AsyncClient", lambda **kwargs: client_type(transport=httpx.MockTransport(handler), **kwargs))
    with pytest.raises(ValueError, match="private"):
        asyncio.run(download_document("https://documents.example/a", 20, 5))
