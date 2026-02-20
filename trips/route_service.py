"""
Route Service - integrates with OpenRouteService for directions and geocoding.
Falls back to straight-line distance estimation if no API key is configured.
"""

import math
import requests
from django.conf import settings

ORS_BASE = "https://api.openrouteservice.org"
NOMINATIM_BASE = "https://nominatim.openstreetmap.org"


def geocode(query: str) -> dict:
    """Geocode an address string to coordinates using Nominatim (free)."""
    resp = requests.get(
        f"{NOMINATIM_BASE}/search",
        params={"q": query, "format": "json", "limit": 1, "countrycodes": "us"},
        headers={"User-Agent": "ELDTripPlanner/1.0"},
        timeout=10,
    )
    resp.raise_for_status()
    results = resp.json()
    if not results:
        raise ValueError(f"Could not geocode: {query}")
    r = results[0]
    return {
        "lat": float(r["lat"]),
        "lng": float(r["lon"]),
        "name": r.get("display_name", query),
    }


def geocode_autocomplete(query: str) -> list:
    """Get location suggestions for autocomplete."""
    resp = requests.get(
        f"{NOMINATIM_BASE}/search",
        params={"q": query, "format": "json", "limit": 5, "countrycodes": "us"},
        headers={"User-Agent": "ELDTripPlanner/1.0"},
        timeout=10,
    )
    resp.raise_for_status()
    return [
        {
            "lat": float(r["lat"]),
            "lng": float(r["lon"]),
            "name": r.get("display_name", ""),
        }
        for r in resp.json()
    ]


def get_route(start: dict, end: dict) -> dict:
    """
    Get driving route between two points.
    Uses OSRM first, then ORS, then falls back to haversine estimation.
    Returns: { distance_miles, duration_hours, geometry: [[lng,lat],...] }
    """
    # Try OSRM first (no API key needed, free)
    try:
        return _get_route_osrm(start, end)
    except Exception as e:
        import logging
        logging.warning(f"OSRM routing failed: {e}")

    # Try ORS if key is available
    api_key = getattr(settings, 'ORS_API_KEY', '')
    if api_key:
        try:
            return _get_route_ors(start, end, api_key)
        except Exception as e:
            import logging
            logging.warning(f"ORS routing failed: {e}")

    # Fallback to straight-line estimation (always works)
    return _get_route_fallback(start, end)


def _get_route_osrm(start: dict, end: dict) -> dict:
    """Get route from OSRM (free, no API key)."""
    url = (
        f"https://router.project-osrm.org/route/v1/driving/"
        f"{start['lng']},{start['lat']};{end['lng']},{end['lat']}"
        f"?overview=simplified&geometries=geojson"
    )
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    if data.get("code") != "Ok" or not data.get("routes"):
        raise ValueError("OSRM returned no route")

    route = data["routes"][0]
    distance_meters = route["distance"]
    duration_seconds = route["duration"]
    coords = route["geometry"]["coordinates"]  # [[lng, lat], ...]

    return {
        "distance_miles": round(distance_meters / 1609.344, 1),
        "duration_hours": round(duration_seconds / 3600, 2),
        "geometry": coords,  # [[lng, lat], ...]
    }


def _get_route_ors(start: dict, end: dict, api_key: str) -> dict:
    """Get route from OpenRouteService."""
    resp = requests.post(
        f"{ORS_BASE}/v2/directions/driving-hgv/geojson",
        json={
            "coordinates": [
                [start["lng"], start["lat"]],
                [end["lng"], end["lat"]],
            ]
        },
        headers={
            "Authorization": api_key,
            "Content-Type": "application/json",
        },
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()

    feat = data["features"][0]
    props = feat["properties"]["summary"]
    coords = feat["geometry"]["coordinates"]

    return {
        "distance_miles": round(props["distance"] / 1609.344, 1),
        "duration_hours": round(props["duration"] / 3600, 2),
        "geometry": coords,
    }


def _get_route_fallback(start: dict, end: dict) -> dict:
    """Straight-line estimation as last resort."""
    dist_miles = _haversine_miles(start["lat"], start["lng"], end["lat"], end["lng"])
    # Road distance is roughly 1.3x straight line
    road_miles = dist_miles * 1.3
    duration_hours = road_miles / 55  # avg truck speed

    # Simple straight-line geometry
    steps = 50
    geometry = []
    for i in range(steps + 1):
        frac = i / steps
        lat = start["lat"] + (end["lat"] - start["lat"]) * frac
        lng = start["lng"] + (end["lng"] - start["lng"]) * frac
        geometry.append([lng, lat])

    return {
        "distance_miles": round(road_miles, 1),
        "duration_hours": round(duration_hours, 2),
        "geometry": geometry,
    }


def _haversine_miles(lat1, lon1, lat2, lon2):
    R = 3959  # Earth radius in miles
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(dlon / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))
