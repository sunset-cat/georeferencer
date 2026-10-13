import json
import asyncio
import hashlib
import sqlite3
import time
from pathlib import Path
from typing import Protocol

import httpx

from .models import Candidate, Category, Entity, Point
from .validation import normalize

GEOCODER_CANDIDATE_LIMIT = 20

GEOCODER_TYPES = {"city": "settlement", "town": "settlement", "village": "settlement", "hamlet": "settlement",
                  "administrative": "administrative_area", "state": "administrative_area", "country": "administrative_area",
                  "peak": "mountain", "volcano": "volcano", "river": "river", "lake": "lake"}


def geocoder_type(value):
    if value in GEOCODER_TYPES:
        return Category.geographic, GEOCODER_TYPES[value], "representative_point"
    if value == "mine":
        return Category.mining, "mine", "representative_point"
    # Preserve unfamiliar provider records for review without asserting usable geometry.
    return Category.other, str(value or "unknown"), "unknown"


class Source(Protocol):
    name: str
    async def search(self, entity: Entity, refined: bool = False) -> list[Candidate]: ...


class LocalRegistry:
    """Adapter for normalized, user-provided gazetteer/survey/registry exports."""
    name = "local_registry"
    supports_refinement = False

    def __init__(self, path: Path):
        self.path = path

    async def search(self, entity, refined=False):
        records = [Candidate.model_validate(row) for row in json.loads(self.path.read_text(encoding="utf-8"))]
        names = {normalize(entity.name), *(normalize(n) for n in entity.aliases)}
        ids = {(i.namespace, i.value) for i in entity.identifiers}
        return [r for r in records if normalize(r.name) in names or ids.intersection((i.namespace, i.value) for i in r.identifiers)]


class HTTPGeocoder:
    """Shared per-provider serialization and 24-hour persistent response caching."""
    name = "geocoder"

    def __init__(self, url: str, user_agent: str, timeout: float, cache_path: Path | None = None, min_interval: float = 1.1):
        self.url, self.user_agent, self.timeout = url, user_agent, timeout
        self.cache_path, self.min_interval = cache_path, min_interval
        self.lock = asyncio.Lock()
        self.next_request = 0.0
        self.last_cache_hit = False
        self.cache = {}
        if cache_path:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(cache_path) as db:
                db.execute("CREATE TABLE IF NOT EXISTS responses (key TEXT PRIMARY KEY, created REAL NOT NULL, payload TEXT NOT NULL)")

    def cached(self, key):
        if self.cache_path:
            with sqlite3.connect(self.cache_path) as db:
                row = db.execute("SELECT created, payload FROM responses WHERE key=?", (key,)).fetchone()
            if row and time.time() - row[0] < 86400:
                return json.loads(row[1])
        else:
            row = self.cache.get(key)
            if row and time.time() - row[0] < 86400:
                return row[1]
        return None

    async def request_json(self, params):
        key = hashlib.sha256(json.dumps([self.name, self.url, params], sort_keys=True).encode()).hexdigest()
        async with self.lock:
            cached = self.cached(key)
            self.last_cache_hit = cached is not None
            if cached is not None:
                return cached
            delay = self.next_request - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            self.next_request = time.monotonic() + self.min_interval
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(self.url, params=params, headers={"User-Agent": self.user_agent})
                response.raise_for_status()
                data = response.json()
            if self.name == "photon" and (not isinstance(data, dict) or not isinstance(data.get("features"), list)):
                raise ValueError("Photon returned an invalid feature collection")
            if self.name == "nominatim" and not isinstance(data, list):
                raise ValueError("Nominatim returned an invalid candidate list")
            if self.cache_path:
                with sqlite3.connect(self.cache_path) as db:
                    db.execute("INSERT OR REPLACE INTO responses VALUES (?, ?, ?)", (key, time.time(), json.dumps(data)))
            else:
                self.cache[key] = (time.time(), data)
            return data


class NominatimSource(HTTPGeocoder):
    name = "nominatim"
    supports_refinement = True

    def query_text(self, entity, refined=False):
        return entity.name + (", " + entity.jurisdiction if refined and entity.jurisdiction else "")

    async def search(self, entity, refined=False):
        query = self.query_text(entity, refined)
        params = {"q": query, "format": "jsonv2", "addressdetails": 1, "limit": GEOCODER_CANDIDATE_LIMIT}
        if entity.country_code:
            params["countrycodes"] = entity.country_code.lower()
        rows = await self.request_json(params)
        candidates = []
        for row in rows:
            category, subtype, basis = geocoder_type(row.get("type"))
            address = row.get("address", {})
            candidates.append(Candidate(
                name=row.get("name") or row["display_name"].split(",")[0],
                category=category, subtype=subtype,
                country_code=address.get("country_code"), jurisdiction=address.get("state"),
                geometry=Point(coordinates=(float(row["lon"]), float(row["lat"]))),
                geometry_basis=basis, source=self.url,
                source_record_id=f"{row.get('osm_type', 'place')}:{row.get('osm_id', row['place_id'])}",
                source_record_url=(f"https://www.openstreetmap.org/{row['osm_type']}/{row['osm_id']}"
                                   if row.get("osm_type") in {"node", "way", "relation"} and row.get("osm_id") else None),
                independence="unknown",
                evidence={"provider_type": str(row.get("type") or "unknown")},
            ))
        return candidates


class PhotonSource(HTTPGeocoder):
    name = "photon"
    supports_refinement = True

    def query_text(self, entity, refined=False):
        return entity.name + (", " + entity.jurisdiction if refined and entity.jurisdiction else "")

    async def search(self, entity, refined=False):
        query = self.query_text(entity, refined)
        params = {"q": query, "limit": GEOCODER_CANDIDATE_LIMIT, "lang": "en"}
        if entity.country_code:
            params["countrycode"] = entity.country_code.upper()
        data = await self.request_json(params)
        candidates = []
        for row in data["features"]:
            p = row.get("properties", {})
            value = p.get("osm_value") if p.get("osm_value") in GEOCODER_TYPES or p.get("osm_value") == "mine" else p.get("type") if p.get("type") in GEOCODER_TYPES else p.get("osm_value") or p.get("type")
            category, subtype, basis = geocoder_type(value)
            if not p.get("name") or row.get("geometry", {}).get("type") != "Point":
                continue
            osm_type = {"N": "node", "W": "way", "R": "relation"}.get(p.get("osm_type"), p.get("osm_type"))
            osm_id = p.get("osm_id")
            record_url = f"https://www.openstreetmap.org/{osm_type}/{osm_id}" if osm_type in {"node", "way", "relation"} and osm_id else None
            candidates.append(Candidate(name=p["name"], category=category, subtype=subtype,
                country_code=p.get("countrycode"), jurisdiction=p.get("state"),
                geometry=Point(coordinates=tuple(row["geometry"]["coordinates"])), geometry_basis=basis,
                source=self.url, source_record_id=f"{osm_type}:{osm_id}" if osm_id else hashlib.sha256(json.dumps(row, sort_keys=True).encode()).hexdigest(),
                source_record_url=record_url, independence="unknown",
                evidence={"provider_type": str(value or "unknown"), "osm_tag": f"{p.get('osm_key', '')}:{p.get('osm_value', '')}"}))
        return candidates
