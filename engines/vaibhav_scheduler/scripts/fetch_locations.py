"""Build hourly weather profiles for 100 well-spaced places in one country.

    python scripts/fetch_locations.py                 # data/ch_weather_2025.json
    python scripts/fetch_locations.py --country NZ    # data/nz_weather_2025.json
    python scripts/fetch_locations.py --country NZ --list   # only show the geocoding

Steps
  1. Geocode a list of ~150 towns (Open-Meteo geocoding API). Towns, not grid
     points, because a server needs a house to heat.
  2. Keep 100 of them by farthest-point sampling from a first town: each pick
     is the town farthest from all towns already picked.
  3. Download hourly temperature_2m for the year from the Open-Meteo
     historical archive.
  4. Download a coarse outline of the country for the map (optional).

Time axis. Every file shares one clock, so hour h is the same instant
everywhere: the Swiss local hours of the year (Europe/Zurich). A country far
from Switzerland still needs its own calendar days, because a house heats by
its own day. Its temperatures are therefore stored from its own midnight on
1 January (standard time) for 366 days, and `axis_offset` says which of those
hours is hour 0 of the shared clock. New Zealand: midnight NZST is 13:00 in
Zurich the day before, so the offset is 11 hours.

No API key is needed. Requests are spaced out to stay inside the free limits,
and every response is cached in data/cache so a rerun continues where it stopped.
"""

from __future__ import annotations

import argparse
import json
import math
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from pathlib import Path

GEOCODE = "https://geocoding-api.open-meteo.com/v1/search"
ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
OUTLINE = "https://raw.githubusercontent.com/georgique/world-geojson/develop/countries/{name}.json"

CH_CANDIDATES = """
Basel, Liestal, Rheinfelden, Laufen, Delémont, Porrentruy, Saignelégier, Moutier, Olten, Aarau,
Baden, Brugg, Zofingen, Solothurn, Grenchen, Biel, Schaffhausen, Stein am Rhein, Frauenfeld,
Kreuzlingen, Romanshorn, Weinfelden, Winterthur, Zürich, Uster, Wetzikon, Rapperswil, Bülach, Wil,
St. Gallen, Herisau, Appenzell, Altstätten, Rorschach, Sargans, Bad Ragaz, Wattwil, Wildhaus,
Glarus, Linthal, Elm, Zug, Luzern, Sursee, Willisau, Schüpfheim, Schwyz, Einsiedeln, Altdorf,
Andermatt, Göschenen, Sarnen, Engelberg, Stans, Sörenberg, Bern, Burgdorf, Langnau im Emmental,
Langenthal, Thun, Spiez, Interlaken, Grindelwald, Lauterbrunnen, Meiringen, Brienz, Kandersteg,
Adelboden, Lenk, Zweisimmen, Gstaad, Schwarzenburg, Lyss, Neuchâtel, La Chaux-de-Fonds, Le Locle,
Fleurier, Yverdon-les-Bains, Sainte-Croix, Vallorbe, Le Sentier, Lausanne, Morges, Nyon, Genève,
Vevey, Montreux, Aigle, Château-d'Oex, Bulle, Fribourg, Romont, Payerne, Murten, Echallens,
Moudon, Monthey, Martigny, Verbier, Orsières, Sion, Sierre, Crans-Montana, Leukerbad, Visp,
Zermatt, Saas-Fee, Brig, Fiesch, Ulrichen, Evolène, Zinal, Champéry, Airolo, Faido, Biasca,
Olivone, Bellinzona, Locarno, Cevio, Lugano, Mendrisio, Chiasso, Chur, Landquart, Klosters,
Davos, Arosa, Lenzerheide, Thusis, Tiefencastel, Savognin, Bivio, Splügen, San Bernardino,
Mesocco, Roveredo, Ilanz, Disentis, Vals, Flims, St. Moritz, Samedan, Pontresina, Zuoz, Zernez,
Scuol, Samnaun, Müstair, Poschiavo, Maloja, Vicosoprano
"""

NZ_CANDIDATES = """
Kaitaia, Kerikeri, Kaikohe, Dargaville, Whangarei, Mangawhai, Wellsford, Warkworth, Auckland,
Helensville, Pukekohe, Thames, Coromandel, Whitianga, Whangamata, Waihi, Huntly, Hamilton, Raglan,
Te Awamutu, Matamata, Tauranga, Te Puke, Whakatane, Opotiki, Kawerau, Rotorua, Tokoroa, Taupo,
Turangi, Te Kuiti, Taumarunui, Ohakune, Waiouru, Taihape, New Plymouth, Stratford, Hawera,
Opunake, Whanganui, Marton, Feilding, Palmerston North, Levin, Dannevirke, Pahiatua, Masterton,
Martinborough, Featherston, Wellington, Upper Hutt, Paraparaumu, Otaki, Napier, Hastings, Wairoa,
Waipukurau, Gisborne, Ruatoria, Te Araroa, Tolaga Bay, Murupara, Mangakino, Kawhia, Eketahuna,
Te Kaha, Waverley, Bulls, Porangahau, Picton, Blenheim, Nelson, Motueka, Takaka, Collingwood,
Murchison, Saint Arnaud, Kaikoura, Seddon, Hanmer Springs, Culverden, Cheviot, Amberley, Rangiora,
Christchurch, Akaroa, Darfield, Methven, Ashburton, Geraldine, Timaru, Waimate, Fairlie,
Lake Tekapo, Twizel, Omarama, Kurow, Oamaru, Dunedin, Mosgiel, Milton, Balclutha, Owaka,
Lawrence, Roxburgh, Alexandra, Cromwell, Wanaka, Queenstown, Glenorchy, Lumsden, Te Anau,
Manapouri, Tuatapere, Riverton, Invercargill, Bluff, Gore, Winton, Haast, Fox Glacier,
Franz Josef, Hokitika, Greymouth, Reefton, Westport, Karamea, Arthur's Pass, Ranfurly,
Middlemarch, Makarora, Harihari, Punakaiki, Rakaia, Waikari, Tapanui, Mossburn, Wyndham,
Ahaura, Granity, Rai Valley, Wakefield, Tapawera, Havelock, Ward, Waiau, Oxford,
Leeston, Temuka, Pleasant Point, Hampden, Waikouaiti, Clinton, Otautau, Edendale, Ohai,
Paeroa, Morrinsville, Cambridge, Putaruru, Otorohanga, Piopio, Waitara, Inglewood, Eltham,
Patea, Raetihi, Hunterville, Foxton, Woodville, Carterton, Tokomaru Bay, Te Karaka
"""

# day_shift: hours after midnight on the reference clock at which the country's
# own day starts (None: the country keeps the reference clock itself).
COUNTRIES = {
    "CH": {"name": "Switzerland", "first": "Basel", "language": "de", "candidates": CH_CANDIDATES,
           "outline": "switzerland", "box": None, "day_shift": None, "day_label": "",
           "cache": ""},
    "NZ": {"name": "New Zealand", "first": "Auckland", "language": "en", "candidates": NZ_CANDIDATES,
           "outline": "new_zealand", "box": (165.0, 179.5, -48.0, -34.0), "day_shift": 13,
           "day_label": "NZST (UTC+12)", "cache": "nz_"},
}
# Villages that the geocoder places on a ridge or a river some kilometres away.
# The weather must be the village's, so these coordinates and regions replace
# the geocoder's once the 100 towns are chosen.
CORRECTIONS = {
    "NZ": {"Saint Arnaud": (-41.803, 172.845, "Tasman District"), "Makarora": (-44.232, 169.231, "Otago")},
}
REFERENCE_TIMEZONE = "Europe/Zurich"
OUTLINE_MIN_POINTS = 40          # drops the islets, keeps the main islands


def get_json(url: str, cache: Path, pause_s: float, retries: int = 6):
    """GET with an on-disk cache and patient retries on rate limiting."""
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    for attempt in range(retries):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "weather-profile-script"})
            with urllib.request.urlopen(request, timeout=120) as response:
                body = response.read().decode("utf-8")
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(body, encoding="utf-8")
            time.sleep(pause_s)
            return json.loads(body)
        except urllib.error.HTTPError as error:
            if error.code != 429 or attempt == retries - 1:
                raise
            wait = 65 * (attempt + 1)
            print(f"  rate limited, waiting {wait} s", flush=True)
            time.sleep(wait)
    raise RuntimeError("unreachable")


def km(a: dict, b: dict) -> float:
    """Great-circle distance."""
    p1, p2 = math.radians(a["lat"]), math.radians(b["lat"])
    dl = math.radians(b["lon"] - a["lon"])
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(h))


def geocode(name: str, cache_dir: Path, code: str = "CH") -> dict | None:
    country = COUNTRIES[code]
    query = urllib.parse.urlencode({"name": name, "count": 10, "language": country["language"],
                                    "format": "json"})
    safe = "".join(c if c.isalnum() else "_" for c in name)
    data = get_json(f"{GEOCODE}?{query}", cache_dir / f"geo_{country['cache']}{safe}.json", pause_s=0.2)
    inside = [r for r in data.get("results", []) if r.get("country_code") == code]
    if not inside:
        return None
    exact = [r for r in inside if r["name"].lower() == name.lower()] or inside[:1]
    best = max(exact, key=lambda r: r.get("population") or 0)
    return {"name": name, "lat": round(best["latitude"], 4), "lon": round(best["longitude"], 4),
            "canton": best.get("admin1", "")}


def outline_rings(geo: dict, box: tuple[float, float, float, float] | None) -> list[list[list[float]]]:
    """Outer rings of the country outline, largest first."""
    rings = []
    for feature in geo["features"]:
        geometry = feature["geometry"]
        polygons = geometry["coordinates"] if geometry["type"] == "MultiPolygon" else [geometry["coordinates"]]
        for polygon in polygons:
            ring = polygon[0]
            if box and not all(box[0] <= x <= box[1] and box[2] <= y <= box[3] for x, y in ring):
                continue
            rings.append([[round(x, 3), round(y, 3)] for x, y in ring])
    rings.sort(key=len, reverse=True)
    return [ring for i, ring in enumerate(rings) if i == 0 or len(ring) >= OUTLINE_MIN_POINTS]


def farthest_point_sample(places: list[dict], count: int, first: str) -> list[dict]:
    chosen = [next(p for p in places if p["name"] == first)]
    rest = [p for p in places if p["name"] != first]
    nearest = [km(p, chosen[0]) for p in rest]
    while len(chosen) < count and rest:
        index = max(range(len(rest)), key=nearest.__getitem__)
        pick = rest.pop(index)
        nearest.pop(index)
        chosen.append(pick)
        nearest = [min(d, km(p, pick)) for d, p in zip(nearest, rest)]
    return chosen


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--country", choices=sorted(COUNTRIES), default="CH")
    parser.add_argument("--year", type=int, default=2025)
    parser.add_argument("--list", action="store_true", help="print the geocoded towns and stop")
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--batch", type=int, default=10, help="locations per archive request")
    parser.add_argument("--pause", type=float, default=28.0, help="seconds between archive requests")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()
    country = COUNTRIES[args.country]
    out = args.out or Path("data") / f"{args.country.lower()}_weather_{args.year}.json"
    cache_dir = out.parent / "cache"

    names = [n.strip() for n in country["candidates"].replace("\n", " ").split(",") if n.strip()]
    places, seen = [], set()
    for name in names:
        place = geocode(name, cache_dir, args.country)
        if place is None:
            print(f"  not found in {country['name']}, skipped: {name}")
        elif (place["lat"], place["lon"]) in seen:
            print(f"  same place as an earlier town, skipped: {name}")
        else:
            seen.add((place["lat"], place["lon"]))
            places.append(place)
    print(f"geocoded {len(places)} of {len(names)} towns", flush=True)
    if args.list:
        for place in sorted(places, key=lambda p: -p["lat"]):
            print(f"  {place['name']:<22}{place['lat']:>9.3f}{place['lon']:>9.3f}  {place['canton']}")
        return

    chosen = farthest_point_sample(places, args.count, country["first"])
    spacing = [min(km(p, q) for q in chosen if q is not p) for p in chosen]
    print(f"kept {len(chosen)}: nearest-neighbour distance {min(spacing):.0f}-{max(spacing):.0f} km, "
          f"median {sorted(spacing)[len(spacing) // 2]:.0f} km", flush=True)

    for place in chosen:
        if place["name"] in CORRECTIONS.get(args.country, {}):
            place["lat"], place["lon"], place["canton"] = CORRECTIONS[args.country][place["name"]]
            print(f"  coordinates corrected by hand: {place['name']}")

    # A country with its own day is downloaded on the reference clock with a day
    # of margin on both sides, then cut to 366 of its own days.
    shift = country["day_shift"]
    first_day, last_day = f"{args.year}-01-01", f"{args.year}-12-31"
    if shift is not None:
        first_day, last_day = f"{args.year - 1}-12-31", f"{args.year + 1}-01-01"
    prefix = f"wx_{country['cache']}{args.year}"
    for start in range(0, len(chosen), args.batch):
        batch = chosen[start:start + args.batch]
        query = urllib.parse.urlencode({
            "latitude": ",".join(str(p["lat"]) for p in batch),
            "longitude": ",".join(str(p["lon"]) for p in batch),
            "start_date": first_day, "end_date": last_day,
            "hourly": "temperature_2m", "timezone": REFERENCE_TIMEZONE, "format": "json",
        })
        key = f"{prefix}_{start:03d}_{zlib.crc32(query.encode()):08x}.json"     # changes with the request
        data = get_json(f"{ARCHIVE}?{query}", cache_dir / key, args.pause)
        data = data if isinstance(data, list) else [data]
        for place, result in zip(batch, data):
            temps, times = result["hourly"]["temperature_2m"], result["hourly"]["time"]
            if shift is not None:
                temps, times = temps[shift:shift + 366 * 24], times[shift:shift + 366 * 24]
            if any(t is None for t in temps):
                raise ValueError(f"missing temperatures for {place['name']}")
            place["elevation_m"] = round(result["elevation"])
            place["temps_c"] = temps
            place["first_hour"] = times[0]            # on the reference clock
        print(f"  weather {start + len(batch)}/{len(chosen)}", flush=True)

    outlines = []
    try:
        geo = get_json(OUTLINE.format(name=country["outline"]),
                       cache_dir / f"outline{'_' + args.country.lower() if country['cache'] else ''}.json",
                       pause_s=0)
        outlines = outline_rings(geo, country["box"])
    except Exception as error:   # the map still works without the outline
        print(f"  outline not available ({error})")

    hours = {len(p["temps_c"]) for p in chosen}
    payload = {
        "year": args.year,
        "country": args.country,
        "country_name": country["name"],
        "source": f"Open-Meteo historical archive, temperature_2m, hourly, {REFERENCE_TIMEZONE}; "
                  "coordinates from the Open-Meteo geocoding API",
        "selection": f"farthest-point sampling of {len(chosen)} from {len(places)} towns in "
                     f"{country['name']}, from {country['first']}",
        "hours": hours.pop() if len(hours) == 1 else sorted(hours),
        # hour 0 of the shared clock is temps_c[axis_offset]; temps_c starts at the
        # country's own midnight so that 24-hour blocks are its calendar days
        "axis_offset": 0 if shift is None else 24 - shift,
        "day_starts": country["day_label"] or REFERENCE_TIMEZONE,
        "outlines": outlines,
        "locations": chosen,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    print(f"wrote {out} ({out.stat().st_size / 1e6:.1f} MB, {payload['hours']} hours per location)")


if __name__ == "__main__":
    main()
