"""Bounded public HTTP(S) downloads with DNS-pinned connections."""
import asyncio
import ipaddress
import socket
from urllib.parse import urljoin

import httpx


class DownloadConnectionError(RuntimeError):
    pass


async def public_target(value: str):
    url = httpx.URL(value)
    if url.scheme not in {"http", "https"} or not url.host or url.userinfo:
        raise ValueError("Use a public HTTP(S) URL without embedded credentials")
    if url.port not in {None, 80, 443}:
        raise ValueError("Only standard HTTP(S) ports are supported")
    records = await asyncio.wait_for(asyncio.to_thread(socket.getaddrinfo, url.host, url.port or (443 if url.scheme == "https" else 80), type=socket.SOCK_STREAM), timeout=10)
    addresses = {record[4][0] for record in records}
    if not addresses or any(not ipaddress.ip_address(address).is_global or ipaddress.ip_address(address).is_multicast for address in addresses):
        raise ValueError("URL resolves to a private or non-public network address")
    return url, sorted(addresses)[0]


async def download_document(value: str, max_bytes: int, timeout: float, *, total_timeout: float = 60):
    async with asyncio.timeout(total_timeout):
        for _ in range(5):
            url, address = await public_target(value)
            # Connect to the checked address, preserving HTTP Host and TLS identity.
            # Disable proxies so they cannot bypass the DNS/IP restrictions.
            async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
                host = url.host
                if ":" in host:
                    host = f"[{host}]"
                if url.port and url.port != (443 if url.scheme == "https" else 80):
                    host += f":{url.port}"
                request = client.build_request("GET", url.copy_with(host=address), headers={"Host": host, "User-Agent": "georeferencer-poc/0.1"}, extensions={"sni_hostname": url.host})
                try:
                    response = await client.send(request, stream=True)
                except httpx.ConnectError as exc:
                    raise DownloadConnectionError(
                        "Cannot connect to the document host. Check server outbound-network access, "
                        "firewall restrictions, and host availability. " + str(exc)
                    ) from exc
                try:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        if not response.headers.get("location"):
                            raise ValueError("Redirect has no destination")
                        value = urljoin(str(url), response.headers["location"])
                        continue
                    response.raise_for_status()
                    chunks, total = [], 0
                    async for chunk in response.aiter_bytes():
                        total += len(chunk)
                        if total > max_bytes:
                            raise ValueError("Download exceeds the document size limit")
                        chunks.append(chunk)
                    return b"".join(chunks), response.headers.get("content-type", ""), str(url)
                finally:
                    await response.aclose()
        raise ValueError("Too many redirects")
