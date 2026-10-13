import asyncio
import json

import httpx
import pytest

from georeferencer.source_documents import SourceDocumentCache


def test_concurrent_downloads_and_text_reused_after_restart(tmp_path):
    downloads, budget_calls = [], []
    async def download(url, max_bytes, timeout, **kwargs):
        downloads.append(url)
        await asyncio.sleep(.01)
        return b"Pike Zone in Saskatchewan", "text/plain", url + "/final"
    async def run():
        cache = SourceDocumentCache(tmp_path)
        documents = await asyncio.gather(*(cache.get("https://survey.example/report", 100, 5,
            downloader=download, before_download=lambda: budget_calls.append(1)) for _ in range(2)))
        assert [d.cache_hit for d in documents] == [False, True]
        text, hit = await cache.parse(documents[0], 1000)
        assert not hit and text[0] == "Pike Zone in Saskatchewan"
        restarted = SourceDocumentCache(tmp_path)
        doc = await restarted.get("https://survey.example/report", 100, 5, downloader=download)
        assert doc.cache_hit and doc.final_url.endswith("/final")
        assert (await restarted.parse(doc, 1000)) == (text, True)
        metadata = json.loads((doc.path.parent / "metadata.json").read_text())
        assert metadata["sha256"] == doc.sha256 and metadata["bytes"] == len(doc.data)
    asyncio.run(run())
    assert len(downloads) == len(budget_calls) == 1


def test_failed_download_not_cached_and_corrupt_snapshot_refetched(tmp_path):
    calls = []
    async def download(url, *args, **kwargs):
        calls.append(url)
        if len(calls) == 1:
            raise httpx.ReadTimeout("interrupted")
        return b"source text", "text/plain", url
    async def run():
        cache = SourceDocumentCache(tmp_path)
        with pytest.raises(httpx.ReadTimeout):
            await cache.get("https://survey.example/report", 100, 5, downloader=download)
        assert not list(tmp_path.rglob("metadata.json"))
        doc = await cache.get("https://survey.example/report", 100, 5, downloader=download)
        doc.path.write_bytes(b"corrupt")
        restored = await cache.get("https://survey.example/report", 100, 5, downloader=download)
        assert not restored.cache_hit and restored.data == b"source text"
        with pytest.raises(ValueError, match="size limit"):
            await cache.get("https://survey.example/report", 3, 5, downloader=download)
    asyncio.run(run())
    assert len(calls) == 3
