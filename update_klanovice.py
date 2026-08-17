import requests
import math
from concurrent.futures import ThreadPoolExecutor, as_completed
import folium
from streetlevel import streetview

S, W, N, E = 50.075, 14.635, 50.115, 14.700

print("1/4 Stahuji silniční síť Klánovic…")

query = '''[out:json][timeout:90];
way["highway"]["highway"!~"footway|path|cycleway|pedestrian|steps|track|bridleway|construction|proposed|service"]["access"!~"private|no"]
(50.075,14.635,50.115,14.700);
out geom;'''

servers = [
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://overpass-api.de/api/interpreter"
]

headers = {
    "User-Agent": "Klanovice-StreetView/1.0"
}

data = None
last_error = None

for server in servers:
    try:
        print("   Zkouším:", server)

        rr = requests.get(
            server,
            params={"data": query},
            headers=headers,
            timeout=120
        )

        rr.raise_for_status()
        data = rr.json()

        print("   OK")
        break

    except Exception as e:
        last_error = e
        print("   Server neodpověděl, zkouším další…")

if data is None:
    raise RuntimeError(
        f"OpenStreetMap data se nepodařilo načíst. Poslední chyba: {last_error}"
    )

roads = [
    x for x in data["elements"]
    if x.get("geometry") and len(x["geometry"]) >= 2
]

print("   Nalezeno silničních úseků:", len(roads))


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


def sample_line(geom, spacing=100):
    pts = []
    carry = 0

    for i in range(len(geom) - 1):

        a = [geom[i]["lat"], geom[i]["lon"]]
        b = [geom[i + 1]["lat"], geom[i + 1]["lon"]]

        d = hav(a, b)
        pos = 0

        while carry + (d - pos) >= spacing:

            need = spacing - carry
            pos += need

            pts.append([
                a[0] + (b[0] - a[0]) * pos / d,
                a[1] + (b[1] - a[1]) * pos / d
            ])

            carry = 0

        carry += d - pos

    return pts


samples = []

for ri, road in enumerate(roads):

    for p in sample_line(road["geometry"], 100):
        samples.append((ri, p[0], p[1]))


uniq = {}

for item in samples:

    ri, lat, lon = item

    uniq.setdefault(
        (round(lat, 5), round(lon, 5)),
        item
    )

samples = list(uniq.values())

print("   Kontrolních bodů:", len(samples))
print("2/4 Zjišťuji Google Street View…")


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

        return item + (None, "ERROR")


results = []

with ThreadPoolExecutor(max_workers=6) as ex:

    futures = [
        ex.submit(inspect, s)
        for s in samples
    ]

    for i, f in enumerate(as_completed(futures), 1):

        results.append(f.result())

        if i % 25 == 0 or i == len(futures):
            print(
                f"   Ověřeno {i}/{len(futures)} bodů…"
            )


print("3/4 Vytvářím mapu…")


byroad = {}

for ri, lat, lon, year, panoid in results:

    byroad.setdefault(ri, []).append(year)


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
    location=[50.0948, 14.6695],
    zoom_start=13,
    tiles="OpenStreetMap",
    control_scale=True
)


for ri, road in enumerate(roads):

    years = byroad.get(ri, [])

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
        "tags", {}
    ).get(
        "name",
        "bezejmenná silnice"
    )

    folium.PolyLine(
        coords,
        color=color,
        weight=5,
        opacity=.85,
        tooltip=f"{name} – {label}"
    ).add_to(m)


legend = '''<div style="
position:fixed;
bottom:25px;
left:25px;
z-index:9999;
background:white;
padding:12px 14px;
border:1px solid #aaa;
border-radius:8px;
box-shadow:0 1px 8px rgba(0,0,0,.2);
font-size:13px">

<b>Klánovice – Street View</b><br>

<span style="color:#16a34a">━━</span> 2026<br>
<span style="color:#2563eb">━━</span> 2025<br>
<span style="color:#eab308">━━</span> 2024<br>
<span style="color:#9ca3af">━━</span>
jiný rok / nezjištěno

</div>'''


m.get_root().html.add_child(
    folium.Element(legend)
)


out = "klanovice_streetview_2024_2026.html"

m.save(out)

print("4/4 HOTOVO")
print("Úseky:", len(roads))
print("Výsledek:", counts)
print("Mapa uložena jako:", out)
