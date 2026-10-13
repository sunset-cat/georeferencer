"""CRS resolution and auditable PROJ transformations, with no network grids."""
import math
import re
import warnings
from functools import lru_cache

from pyproj import CRS, Transformer
from pyproj.aoi import AreaOfInterest
from pyproj.transformer import TransformerGroup


def source_crs(value, zone=None, hemisphere=None):
    label = (value or "unknown").strip()
    if label.upper().startswith("EPSG:"):
        return CRS.from_user_input(label)
    upper = label.upper()
    if "UTM" in upper:
        matches = list(re.finditer(r"(?:ZONE\s*)?(\d{1,2})\s*([NS])\b", upper))
        if len({(m[1],m[2]) for m in matches})>1:
            raise ValueError('Multiple UTM zones occur in CRS evidence; no automatic selection')
        match = matches[0] if matches else None
        if match:
            if zone is not None and zone!=int(match[1]) or hemisphere is not None and hemisphere!=match[2]:
                raise ValueError('Structured UTM zone/hemisphere contradicts the source CRS')
            zone, hemisphere = int(match[1]), match[2]
        if zone is None or hemisphere not in {"N", "S"}:
            raise ValueError("UTM zone and hemisphere are missing; projected coordinates retained")
        if not 1 <= zone <= 60:
            raise ValueError("UTM zone is outside 1–60")
        if re.search(r"SAD\s*69", upper):
            return CRS.from_epsg((29170 if hemisphere == "S" else 29100) + zone)
        if "WGS" in upper:
            return CRS.from_epsg((32700 if hemisphere == "S" else 32600) + zone)
        if "NAD83" in upper or "NAD 83" in upper:
            if hemisphere != "N" or not 1 <= zone <= 23:
                raise ValueError("NAD83 UTM zone is unsupported")
            return CRS.from_epsg(26900 + zone)
        raise ValueError("UTM datum is missing or unsupported; no datum assumption for projected coordinates")
    if re.search(r"SAD\s*69", upper):
        return CRS.from_epsg(4618)
    if upper in {"WGS84", "WGS 84"}:
        return CRS.from_epsg(4326)
    return CRS.from_user_input(label)


@lru_cache(maxsize=128)
def operation(code, lon, lat):
    # A coarse area reduces inappropriate datum operations across continents.
    area = AreaOfInterest(max(-180, lon-1), max(-90, lat-1), min(180, lon+1), min(90, lat+1))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        group = TransformerGroup(code, 4326, always_xy=True, area_of_interest=area, allow_ballpark=False)
    if not group.transformers:
        raise ValueError("No installed non-ballpark transformation supports this coordinate system")
    return group.transformers[0], group.best_available, sorted({g.short_name for op in group.unavailable_operations for g in op.grids if not g.available})


def transform_coordinates(crs, x, y, zone=None, hemisphere=None):
    source = source_crs(crs, zone, hemisphere)
    if source.is_projected and "UTM" in source.name.upper() and not (100_000 <= x <= 900_000 and 0 <= y <= 10_000_000):
        raise ValueError("UTM easting/northing out of range; check column ordering")
    geographic = source.geodetic_crs
    approx_lon, approx_lat = Transformer.from_crs(source, geographic, always_xy=True).transform(x, y, errcheck=True)
    transformer, best, missing = operation(source.to_string(), round(approx_lon), round(approx_lat))
    lon, lat = transformer.transform(x, y, errcheck=True)
    if not all(math.isfinite(v) for v in (lon, lat)) or not (-180 <= lon <= 180 and -90 <= lat <= 90):
        raise ValueError("Transformation produced invalid geographic coordinates")
    bounds = source.area_of_use
    if bounds and not (bounds.west-0.1 <= approx_lon <= bounds.east+0.1 and bounds.south-0.1 <= approx_lat <= bounds.north+0.1):
        raise ValueError("Coordinates fall outside the source CRS area of use; check zone and hemisphere")
    return (lon, lat), {"source_crs": source.to_string(), "target_crs": "EPSG:4326",
        "operation": transformer.description, "accuracy_metres": transformer.accuracy,
        "best_available": best, "missing_grids": missing, "axis_order": "easting,northing -> longitude,latitude",
        "original_x": x, "original_y": y}
