import requests
import math
import time
import re
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

from streetlevel import streetview
import folium


# ============================================================
# NASTAVENÍ
# ============================================================

print("1/6 Stahuji silniční síť Prahy…")

query = '''[out:json][timeout:300];
relation["boundary"="administrative"]["admin_level"="4"]["name"="Praha"];
map_to_area->.praha;
way["highway"]["highway"!~"footway|path|cycleway|pedestrian|steps|track|bridleway|construction|proposed|service|corridor|raceway"]["access"!~"private|no"](area.praha);
out geom;'''

headers = {
    "User-Agent": "streetview-cz/1.0 (Google Street View freshness map; OpenStreetMap data)",
    "Referer": "https://petulesa.github.io/streetview-cz/",
    "Accept": "application/json",
}

servers = [
    "https://overpass.private.coffee/api/interpreter",
    "https://overpass-api.de/api/interpreter",
]


# ============================================================
# 1. OPENSTREETMAP
# ============================================================

data = None
last_error = None

for server in servers:
    for attempt in range(2):
        try:
            print(f"   Zkouším: {server} (pokus {attempt+1}/2)")

            rr = requests.post(
                server,
                data={"data": query},
                headers=headers,
                timeout=360
            )

            rr.raise_for_status()
            data = rr.json()

            print("   OK")
            break

        except Exception as e:
            last_error = e
            print("   Server neodpověděl:", str(e)[:160])

            if attempt == 0:
                time.sleep(3)

    if data is not None:
        break

if data is None:
    raise RuntimeError(
        "OpenStreetMap data se nepodařilo načíst. "
        f"Poslední chyba: {last_error}"
    )


roads = [
    x for x in data["elements"]
    if x.get("geometry") and len(x["geometry"]) >= 2
]

print("   Nalezeno silničních úseků:", len(roads))


# ============================================================
# 2. VÝPOČET VZDÁLENOSTI A KONTROLNÍ BODY
# ============================================================

def hav(a, b):
    R = 6371000
    p = math.pi / 180

    dlat = (b[0] - a[0]) * p
    dlon = (b[1] - a[1]) * p

    x = (
        math.sin(dlat / 2) ** 2
        + math.cos(a[0] * p)
        * math.cos(b[0] * p)
        * math.sin(dlon / 2) ** 2
    )

    return 2 * R * math.asin(math.sqrt(x))


def sample_line(geom, spacing=150):
    pts = []
    carry = 0
    total = 0

    for i in range(len(geom) - 1):

        a = [geom[i]["lat"], geom[i]["lon"]]
        b = [geom[i + 1]["lat"], geom[i + 1]["lon"]]

        d = hav(a, b)
        total += d
        pos = 0

        while d > 0 and carry + (d - pos) >= spacing:

            need = spacing - carry
            pos += need

            pts.append([
                a[0] + (b[0] - a[0]) * pos / d,
                a[1] + (b[1] - a[1]) * pos / d
            ])

            carry = 0

        carry += d - pos

    if not pts and total > 0:

        a = geom[0]
        b = geom[-1]

        pts = [[
            (a["lat"] + b["lat"]) / 2,
            (a["lon"] + b["lon"]) / 2
        ]]

    return pts


samples = []

for ri, road in enumerate(roads):

    for p in sample_line(road["geometry"], 150):

        samples.append(
            (ri, p[0], p[1])
        )


# odstranění duplicitních bodů
uniq = {}

for item in samples:

    ri, lat, lon = item

    uniq.setdefault(
        (round(lat, 5), round(lon, 5)),
        item
    )

samples = list(uniq.values())

print("   Kontrolních bodů:", len(samples))
print("   Praha je velké území – další část může chvíli trvat.")


# ============================================================
# 3. GOOGLE STREET VIEW
# ============================================================

print("2/6 Zjišťuji Google Street View…")


def get_year(p):
    try:
        return int(p.date.year) if p and p.date else None
    except:
        return None


def inspect(item):

    ri, lat, lon = item

    try:

        pano = streetview.find_panorama(
            lat,
            lon,
            radius=50,
            search_third_party=False
        )

        if pano is None:
            return item + (None, None)

        panos = [pano]

        try:
            panos += list(pano.historical or [])
        except:
            pass

        years = [
            get_year(p)
            for p in panos
            if get_year(p) in (2024, 2025, 2026)
        ]

        return item + (
            max(years) if years else None,
            pano.id
        )

    except:

        return item + (
            None,
            "ERROR"
        )


results = []

with ThreadPoolExecutor(max_workers=6) as ex:

    futures = [
        ex.submit(inspect, s)
        for s in samples
    ]

    for i, f in enumerate(as_completed(futures), 1):

        results.append(
            f.result()
        )

        if i % 50 == 0 or i == len(futures):

            print(
                f"   Ověřeno {i}/{len(futures)} bodů…"
            )


# ============================================================
# 4. VYTVOŘENÍ MAPY
# ============================================================

print("3/6 Vytvářím mapu…")


byroad = {}

for ri, lat, lon, year, panoid in results:

    byroad.setdefault(
        ri,
        []
    ).append(year)


colors = {
    2026: "#16a34a",
    2025: "#2563eb",
    2024: "#eab308"
}


counts = {
    2026: 0,
    2025: 0,
    2024: 0,
    "other": 0
}


m = folium.Map(
    location=[50.087, 14.42],
    zoom_start=11,
    tiles="OpenStreetMap",
    control_scale=True
)


for ri, road in enumerate(roads):

    years = byroad.get(
        ri,
        []
    )

    valid = [
        y for y in years
        if y in colors
    ]

    y = max(valid) if valid else None

    color = colors[y] if y else "#9ca3af"

    label = (
        str(y)
        if y
        else "jiný rok / nezjištěno"
    )

    counts[
        y if y else "other"
    ] += 1

    coords = [
        (g["lat"], g["lon"])
        for g in road["geometry"]
    ]

    name = road.get(
        "tags",
        {}
    ).get(
        "name",
        "bezejmenná silnice"
    )

    folium.PolyLine(
        coords,
        color=color,
        weight=4,
        opacity=.85,
        tooltip=f"{name} – {label}"
    ).add_to(m)


legend = '''<div style="position:fixed;bottom:25px;left:25px;z-index:9999;
background:white;padding:12px 14px;border:1px solid #aaa;border-radius:8px;
box-shadow:0 1px 8px rgba(0,0,0,.2);font-size:13px">
<b>Praha – Street View 2024–2026</b><br>
<span style="color:#16a34a">━━</span> 2026<br>
<span style="color:#2563eb">━━</span> 2025<br>
<span style="color:#eab308">━━</span> 2024<br>
<span style="color:#9ca3af">━━</span> jiný rok / nezjištěno
</div>'''


m.get_root().html.add_child(
    folium.Element(legend)
)


raw = Path(
    "praha_streetview_2024_2026_raw.html"
)

m.save(raw)


# ============================================================
# 5. ZMENŠENÍ MAPY
# ============================================================

print("4/6 Zmenšuji mapu…")


text = raw.read_text(
    encoding="utf-8"
)


pattern = re.compile(
    r'(L\.polyline\(\s*)'
    r'(\[\s*(?:\[\s*-?\d+(?:\.\d+)?\s*,\s*-?\d+(?:\.\d+)?\s*\]\s*,?\s*)+\])'
    r'(\s*,\s*\{)',
    re.S
)


changed = 0


def simplify(match):

    global changed

    coords_text = match.group(2)

    coords = re.findall(
        r'\[\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\]',
        coords_text
    )

    if len(coords) < 5:
        return match.group(0)

    # ponecháme každý 6. bod
    new_coords = coords[::6]

    # vždy zachováme konec úseku
    if new_coords[-1] != coords[-1]:
        new_coords.append(coords[-1])

    new_text = "[" + ",".join(
        f"[{lat},{lon}]"
        for lat, lon in new_coords
    ) + "]"

    changed += 1

    return (
        match.group(1)
        + new_text
        + match.group(3)
    )


result = pattern.sub(
    simplify,
    text
)


simplified = Path(
    "praha_streetview_2024_2026.html"
)

simplified.write_text(
    result,
    encoding="utf-8"
)


print(
    "   Zjednodušených silnic:",
    changed
)

print(
    "   Velikost po zmenšení:",
    round(
        simplified.stat().st_size / 1024 / 1024,
        1
    ),
    "MB"
)


# ============================================================
# 6. ROZDĚLENÍ NA DVĚ ČÁSTI
# ============================================================

print("5/6 Rozděluji Prahu na dvě části…")


text = simplified.read_text(
    encoding="utf-8"
)

mid = len(text) // 2

part1 = text[:mid]
part2 = text[mid:]


Path(
    "praha_cast1.txt"
).write_text(
    part1,
    encoding="utf-8"
)


Path(
    "praha_cast2.txt"
).write_text(
    part2,
    encoding="utf-8"
)


# ============================================================
# SPOJOVACÍ STRÁNKA PRAHY
# ============================================================

praha_html = """<!DOCTYPE html>
<html lang="cs">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Praha – Google Street View 2024–2026</title>
</head>

<body>

<div style="
font-family:Arial;
text-align:center;
padding:30px;
">

Načítám mapu Prahy…

</div>


<script>

Promise.all([

    fetch("praha_cast1.txt").then(r => r.text()),
    fetch("praha_cast2.txt").then(r => r.text())

])

.then(parts => {

    const html = parts[0] + parts[1];

    document.open();

    document.write(html);

    document.close();

})

.catch(error => {

    document.body.innerHTML =
        "<h2>Nepodařilo se načíst mapu.</h2>" +
        "<p>" + error + "</p>";

});

</script>

</body>
</html>
"""


Path(
    "praha.html"
).write_text(
    praha_html,
    encoding="utf-8"
)


# ============================================================
# KONEC
# ============================================================

print("6/6 HOTOVO")
print("")
print("Úseky:", len(roads))
print("Výsledek:", counts)
print("")
print("Vytvořeno:")
print(" - praha_cast1.txt")
print(" - praha_cast2.txt")
print(" - praha.html")
