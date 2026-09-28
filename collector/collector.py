"""Collect public hazard feeds and write location events to InfluxDB 1.x."""

import json
import os
import re
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

INFLUX_URL = os.getenv("INFLUX_URL", "http://localhost:8086").rstrip("/")
INFLUX_DB = os.getenv("INFLUX_DB", "global_status")
POLL_SECONDS = max(120, int(os.getenv("POLL_SECONDS", "600")))
FIRMS_MAP_KEY = os.getenv("FIRMS_MAP_KEY", "").strip()
UA = "GlobalStatusDashboard/1.0 (public hazard feed collector)"
GEOCODE_CACHE = os.getenv("GEOCODE_CACHE", "/app/global-status-geocode.sqlite3")
HAZARD_COLOR_ID = {
    "Terremoto": 1,
    "Tsunami": 2,
    "Tornado": 3,
    "Ciclón tropical": 4,
    "Inundación": 5,
    "Incendio forestal": 6,
    "Volcán": 7,
    "Sequía": 8,
}


def fetch_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/geo+json, application/json"})
    with urllib.request.urlopen(req, timeout=25) as response:
        return json.load(response)


def iso_epoch(value, fallback=None):
    if not value:
        return fallback or int(time.time())
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return int(parsed.timestamp())
    except (ValueError, TypeError):
        return fallback or int(time.time())


def line_escape(value):
    return str(value).replace("\\", "\\\\").replace(" ", "\\ ").replace(",", "\\,").replace("=", "\\=")


def localize_title(value):
    """Translate common GDACS hazard wording while preserving place names and IDs."""
    replacements = (
        (r"\bTropical Cyclone\b", "Ciclón tropical"),
        (r"\bCyclone\b", "Ciclón"),
        (r"\bForest fires\b", "Incendios forestales"),
        (r"\bForest fire\b", "Incendio forestal"),
        (r"\bWildfires?\b", "Incendio forestal"),
        (r"\bTornado Warning\b", "Aviso de tornado"),
        (r"\bTornadoes?\b", "Tornado"),
        (r"\bFlooding\b", "Inundaciones"),
        (r"\bFloods?\b", "Inundación"),
        (r"\bEarthquake\b", "Terremoto"),
        (r"\bVolcano\b", "Volcán"),
        (r"\bDrought\b", "Sequía"),
    )
    for pattern, replacement in replacements:
        value = re.sub(pattern, replacement, value, flags=re.IGNORECASE)
    return value


def string_field(value):
    value = str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")
    return f'"{value[:500]}"'


def geocode_events(events):
    """Fill locality from OSM's reverse API with a persistent cache and low request rate."""
    db = sqlite3.connect(GEOCODE_CACHE)
    db.execute("CREATE TABLE IF NOT EXISTS locations (cell TEXT PRIMARY KEY, country TEXT, city TEXT, town TEXT)")
    lookup = {}
    for event in events:
        cell = f"{round(float(event['lat']), 2):.2f},{round(float(event['lon']), 2):.2f}"
        row = db.execute("SELECT country, city, town FROM locations WHERE cell=?", (cell,)).fetchone()
        if row:
            lookup[cell] = row
    missing = sorted(
        (event for event in events if f"{round(float(event['lat']), 2):.2f},{round(float(event['lon']), 2):.2f}" not in lookup),
        key=lambda event: float(event.get("score", 0)), reverse=True,
    )
    # Nominatim's public service permits only four requests per minute for
    # periodic scripts. At most four lookups per ten-minute collection cycle.
    requested = set()
    for event in missing:
        cell = f"{round(float(event['lat']), 2):.2f},{round(float(event['lon']), 2):.2f}"
        if cell in requested or len(requested) >= 4:
            continue
        requested.add(cell)
        params = urllib.parse.urlencode({"lat": event["lat"], "lon": event["lon"], "format": "jsonv2", "addressdetails": 1, "zoom": 12})
        req = urllib.request.Request(f"https://nominatim.openstreetmap.org/reverse?{params}", headers={"User-Agent": "GlobalStatusDashboard/1.0 (public disaster monitoring dashboard)", "Accept-Language": "es"})
        country = city = town = ""
        try:
            with urllib.request.urlopen(req, timeout=15) as response:
                address = json.load(response).get("address", {})
            country = address.get("country", "")
            city = address.get("city") or address.get("municipality") or address.get("county") or ""
            town = address.get("town") or address.get("village") or address.get("hamlet") or address.get("suburb") or ""
        except Exception as exc:
            print(f"Reverse geocoding unavailable: {type(exc).__name__}")
        lookup[cell] = (country, city, town)
        db.execute("INSERT OR REPLACE INTO locations VALUES (?, ?, ?, ?)", (cell, country, city, town))
        db.commit()
        time.sleep(15)
    for event in events:
        cell = f"{round(float(event['lat']), 2):.2f},{round(float(event['lon']), 2):.2f}"
        country, city, town = lookup.get(cell, ("", "", ""))
        event["country"] = country or event.get("country", "")
        event["city"] = city or event.get("city", "")
        event["town"] = town or event.get("town", "")
    db.close()


def event_line(event):
    # Spanish tag names let Grafana display useful field names directly.
    tags = ",".join((
        f"id_evento={line_escape(event['event_id'])}",
        f"tipo={line_escape(event['hazard'])}",
        f"fuente={line_escape(event['source'])}",
        f"nivel_alerta={line_escape(event['severity'])}",
    ))
    fields = [
        f"country={string_field(event.get('country') or 'Pendiente de identificar')}",
        f"city={string_field(event.get('city') or 'No indicada por la fuente')}",
        f"town={string_field(event.get('town') or 'No indicado por la fuente')}",
        f"title={string_field(event.get('title', ''))}",
        f"latitude={float(event['lat'])}",
        f"longitude={float(event['lon'])}",
        f"severity_score={float(event.get('score', 1))}",
        f"color_id={float(HAZARD_COLOR_ID.get(event['hazard'], 9))}",
    ]
    if event.get("magnitude") is not None:
        fields.append(f"magnitude={float(event['magnitude'])}")
    if event.get("url"):
        fields.append(f"url={string_field(event['url'])}")
    return f"hazard_events,{tags} {','.join(fields)} {int(event['time'])}"


def write_events(events):
    if not events:
        return
    payload = "\n".join(event_line(item) for item in events).encode()
    query = urllib.parse.urlencode({"db": INFLUX_DB, "precision": "s"})
    req = urllib.request.Request(f"{INFLUX_URL}/write?{query}", data=payload, method="POST", headers={"Content-Type": "text/plain"})
    with urllib.request.urlopen(req, timeout=20) as response:
        response.read()
    print(f"Wrote {len(events)} event observations")


def usgs_events():
    url = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_day.geojson"
    data = fetch_json(url)
    events = []
    for feature in data.get("features", []):
        props = feature.get("properties") or {}
        coords = (feature.get("geometry") or {}).get("coordinates") or []
        if len(coords) < 2:
            continue
        tsunami = props.get("tsunami") == 1
        events.append({
            "event_id": "usgs_" + str(feature.get("id", "unknown")),
            "hazard": "Tsunami" if tsunami else "Terremoto",
            "source": "USGS",
            "severity": "Significativo" if (props.get("sig") or 0) >= 600 else "Habitual",
            "title": props.get("place") or "Terremoto",
            "lat": coords[1], "lon": coords[0],
            "magnitude": props.get("mag"), "score": min(10, max(1, props.get("mag") or 1)),
            "time": int((props.get("time") or 0) / 1000), "url": props.get("url"),
        })
    return events


def gdacs_events():
    # GDACS supplies global alerts for major sudden-onset disasters as GeoJSON.
    data = fetch_json("https://www.gdacs.org/gdacsapi/api/Events/geteventlist/events4app")
    events = []
    hazard_names = {
        "EQ": "Terremoto",
        "TC": "Ciclón tropical",
        "FL": "Inundación",
        "VO": "Volcán",
        "WF": "Incendio forestal",
        "DR": "Sequía",
    }
    for feature in data.get("features", []):
        props = feature.get("properties") or {}
        geom = feature.get("geometry") or {}
        coords = geom.get("coordinates") or []
        if geom.get("type") == "Point" and len(coords) >= 2:
            lon, lat = coords[:2]
        else:
            # Some GDACS event geometries are tracks/polygons; derive a centroid
            # from their coordinate pairs for the overview marker.
            flat = []
            def walk(node):
                if isinstance(node, list) and len(node) >= 2 and all(isinstance(x, (int, float)) for x in node[:2]):
                    flat.append(node[:2])
                elif isinstance(node, list):
                    for child in node:
                        walk(child)
            walk(coords)
            if not flat:
                continue
            lon = sum(point[0] for point in flat) / len(flat)
            lat = sum(point[1] for point in flat) / len(flat)
        kind = str(props.get("eventtype") or props.get("eventType") or props.get("type") or "Disaster")
        title = localize_title(str(props.get("name") or props.get("htmldescription") or props.get("title") or kind))
        country = str(props.get("country") or props.get("countryname") or props.get("countryName") or "")
        country_match = re.search(r"\bin\s+([A-Z][A-Za-zÀ-ÿ' -]+)$", title)
        if not country and country_match:
            country = country_match.group(1).strip()
        alert = str(props.get("alertlevel") or props.get("alertLevel") or "green").lower()
        fid = feature.get("id") or props.get("eventid") or props.get("eventId") or title
        when = props.get("fromdate") or props.get("fromDate") or props.get("date") or props.get("toDate")
        events.append({
            "event_id": "gdacs_" + str(fid), "hazard": hazard_names.get(kind.upper(), kind), "source": "GDACS",
            "severity": {"red": "Rojo", "orange": "Naranja", "green": "Verde"}.get(alert, "Sin clasificar"),
            "title": title, "lat": lat, "lon": lon, "country": country,
            "score": {"red": 9, "orange": 6, "green": 3}.get(alert, 2),
            "time": iso_epoch(when), "url": props.get("url") or "https://www.gdacs.org/",
        })
    return events


def tornado_events():
    # NWS active alerts include geometry; tornado alerts are currently US-only.
    url = "https://api.weather.gov/alerts/active?event=Tornado%20Warning"
    data = fetch_json(url)
    events = []
    for feature in data.get("features", []):
        props = feature.get("properties") or {}
        geom = feature.get("geometry") or {}
        coords = geom.get("coordinates") or []
        points = []
        def walk(node):
            if isinstance(node, list) and len(node) >= 2 and all(isinstance(x, (int, float)) for x in node[:2]):
                points.append(node[:2])
            elif isinstance(node, list):
                for child in node:
                    walk(child)
        walk(coords)
        if not points:
            continue
        events.append({
            "event_id": "nws_" + str(feature.get("id") or props.get("id") or props.get("sent")),
            "hazard": "Tornado", "source": "NWS",
            "severity": {"extreme": "Extremo", "severe": "Grave", "moderate": "Moderado", "minor": "Leve"}.get(str(props.get("severity") or "").lower(), "Sin clasificar"),
            "title": props.get("headline") or props.get("areaDesc") or "Aviso de tornado",
            "lat": sum(p[1] for p in points) / len(points),
            "lon": sum(p[0] for p in points) / len(points),
            "score": 8, "time": iso_epoch(props.get("effective") or props.get("sent")),
            "url": props.get("web") or "https://www.weather.gov/",
        })
    return events


def tsunami_events():
    # Public PTWC/NTWC Atom bulletins. PTWC serves the Pacific and Caribbean regions.
    feeds = (
        ("PTWC", "https://www.tsunami.gov/events/xml/PHEBAtom.xml"),
        ("NTWC", "https://www.tsunami.gov/events/xml/PAAQAtom.xml"),
    )
    ns = {"a": "http://www.w3.org/2005/Atom", "geo": "http://www.w3.org/2003/01/geo/wgs84_pos#"}
    events = []
    for center, url in feeds:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/atom+xml, application/xml"})
        with urllib.request.urlopen(req, timeout=25) as response:
            root = ET.fromstring(response.read())
        for entry in root.findall("a:entry", ns):
            title = (entry.findtext("a:title", default="Tsunami bulletin", namespaces=ns) or "Tsunami bulletin").strip()
            lat = entry.findtext("geo:lat", namespaces=ns)
            lon = entry.findtext("geo:long", namespaces=ns)
            if lat is None or lon is None:
                continue
            summary = " ".join("".join(entry.find("a:summary", ns).itertext()).split()) if entry.find("a:summary", ns) is not None else ""
            bulletin_url = next((link.attrib.get("href") for link in entry.findall("a:link", ns) if link.attrib.get("rel") == "alternate"), url)
            severity = "Aviso" if "warning" in summary.lower() else "Advertencia" if "advisory" in summary.lower() else "Información"
            events.append({
                "event_id": "tsunami_" + center + "_" + str(entry.findtext("a:id", default=title, namespaces=ns)),
                "hazard": "Tsunami", "source": center, "severity": severity,
                "title": title, "lat": float(lat), "lon": float(lon),
                "score": {"Aviso": 9, "Advertencia": 6, "Información": 3}[severity],
                "time": iso_epoch(entry.findtext("a:updated", namespaces=ns)), "url": bulletin_url,
            })
    return events


def fire_events():
    if not FIRMS_MAP_KEY:
        return []
    # VIIRS Suomi NPP near-real-time detections, global area, latest day.
    url = f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/{urllib.parse.quote(FIRMS_MAP_KEY)}/VIIRS_SNPP_NRT/world/1"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=45) as response:
        rows = response.read().decode("utf-8", "replace").splitlines()
    if len(rows) < 2:
        return []
    headers = [item.strip() for item in rows[0].split(",")]
    events = []
    for row in rows[1:]:
        values = row.split(",")
        if len(values) != len(headers):
            continue
        item = dict(zip(headers, values))
        try:
            lat, lon = float(item["latitude"]), float(item["longitude"])
            acquired = datetime.strptime(item["acq_date"] + item["acq_time"].zfill(4), "%Y-%m-%d%H%M").replace(tzinfo=timezone.utc)
        except (KeyError, ValueError):
            continue
        # One record per satellite hotspot; confidence/FRP are preserved as severity.
        events.append({
            "event_id": f"nasa_{item.get('satellite','viirs')}_{item.get('acq_date')}_{item.get('acq_time')}_{lat}_{lon}",
            "hazard": "Incendio forestal", "source": "NASA FIRMS",
            "severity": {"low": "Baja", "nominal": "Media", "high": "Alta"}.get(str(item.get("confidence") or "nominal").lower(), "Sin clasificar"),
            "title": f"Detección satelital · {item.get('satellite','VIIRS')} · {item.get('acq_date','')}",
            "lat": lat, "lon": lon, "score": max(1, min(10, float(item.get("frp") or 1))),
            "time": int(acquired.timestamp()), "url": "https://firms.modaps.eosdis.nasa.gov/",
        })
    return events


SOURCES = (("USGS", usgs_events), ("GDACS", gdacs_events), ("Centros de tsunami", tsunami_events), ("Avisos de tornado NWS", tornado_events), ("NASA FIRMS", fire_events))


def main():
    while True:
        combined = []
        for label, collector in SOURCES:
            try:
                result = collector()
                combined.extend(result)
                print(f"{label}: {len(result)} events")
            except Exception as exc:  # Keep other feeds running if a provider is unavailable.
                print(f"{label} feed unavailable: {type(exc).__name__}: {exc}")
        try:
            geocode_events(combined)
        except Exception as exc:
            print(f"Location lookup unavailable: {type(exc).__name__}: {exc}")
        try:
            write_events(combined)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            print(f"InfluxDB write unavailable: {exc}")
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
