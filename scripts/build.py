#!/usr/bin/env python3
"""
Daily build: data/pool.json -> static Stremio/Nuvio addon in _site/.

- 4 catalogs: Random (all years), 2000s, 2010s, 2020s
- Order changes daily (seeded by IST date -> stable all day, pagination safe)
- Weighted shuffle: higher weighted-rating films surface earlier more often
- Year spacing: avoids runs of films from the same year
"""
import datetime as dt
import json
import math
import random
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CFG = json.loads((ROOT / "config.json").read_text())
OUT = ROOT / "_site"

ADDON_ID = "community.tamilgems"
VERSION = "1.0.0"

CATALOGS = [
    # id, name, year filter, spacing key
    ("tamil-gems-random", "🎲 Random Tamil Gems", lambda y: True, "year_spacing_random"),
    ("tamil-gems-2000s", "📅 Tamil Gems · 2000s", lambda y: 2000 <= y <= 2009, "year_spacing_decade"),
    ("tamil-gems-2010s", "📅 Tamil Gems · 2010s", lambda y: 2010 <= y <= 2019, "year_spacing_decade"),
    ("tamil-gems-2020s", "📅 Tamil Gems · 2020s", lambda y: y >= 2020, "year_spacing_decade"),
]


def weighted_shuffle(items, rng, strength):
    """Efraimidis–Spirakis: key = u^(1/w). Higher WR -> likelier near the top."""
    floor = CFG["min_weighted_rating"]
    keyed = []
    for it in items:
        w = math.exp(strength * (it["wr"] - floor))
        keyed.append((rng.random() ** (1.0 / w), it))
    keyed.sort(key=lambda t: t[0], reverse=True)
    return [it for _, it in keyed]


def space_years(items, gap):
    """Greedy reorder: don't repeat a year within the last `gap` slots if avoidable."""
    if gap <= 0:
        return items
    queue, out = list(items), []
    while queue:
        recent = {x["year"] for x in out[-gap:]}
        idx = next((i for i, x in enumerate(queue) if x["year"] not in recent), 0)
        out.append(queue.pop(idx))
    return out


def to_meta(r):
    rating = f"{r['imdb_rating']:.1f}"
    votes = r["imdb_votes"]
    votes_s = f"{votes/1000:.1f}k" if votes >= 1000 else str(votes)
    overview = r["overview"]
    if len(overview) > 280:
        overview = overview[:277].rsplit(" ", 1)[0] + "…"
    meta = {
        "id": r["id"],                # IMDb tt-id -> works with Cinemeta, rating & stream addons
        "type": "movie",
        "name": r["name"],
        "releaseInfo": str(r["year"]),
        "imdbRating": rating,
        "genres": r["genres"],
        "description": f"⭐ IMDb {rating} ({votes_s} votes)\n\n{overview}".strip(),
    }
    if r.get("poster"):
        meta["poster"] = r["poster"]
    if r.get("background"):
        meta["background"] = r["background"]
    return meta


def write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")))


def main(today=None):
    pool = json.loads((ROOT / "data" / "pool.json").read_text())
    if today is None:
        today = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=CFG["utc_offset_minutes"])).date()

    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir()

    manifest = {
        "id": ADDON_ID,
        "version": VERSION,
        "name": "Tamil Gems",
        "description": f"Top-rated Tamil films 2000–today ({len(pool)} picks), freshly shuffled every day.",
        "resources": ["catalog"],
        "types": ["movie"],
        "idPrefixes": ["tt"],
        "catalogs": [
            {"type": "movie", "id": cid, "name": name, "extra": [{"name": "skip"}]}
            for cid, name, _, _ in CATALOGS
        ],
        "behaviorHints": {"configurable": False, "configurationRequired": False},
    }
    write_json(OUT / "manifest.json", manifest)

    ps, step = CFG["page_size"], CFG["skip_step"]
    summary = []
    for cid, name, yfilter, spacing_key in CATALOGS:
        items = [r for r in pool if yfilter(r["year"])]
        rng = random.Random(f"{today.isoformat()}::{cid}")
        ordered = space_years(weighted_shuffle(items, rng, CFG["shuffle_strength"]), CFG[spacing_key])
        metas = [to_meta(r) for r in ordered]

        base = OUT / "catalog" / "movie"
        write_json(base / f"{cid}.json", {"metas": metas[:ps]})
        for skip in range(step, len(metas) + step, step):
            write_json(base / cid / f"skip={skip}.json", {"metas": metas[skip:skip + ps]})
        summary.append((name, len(metas), metas[0]["name"] if metas else "-"))

    (OUT / "index.html").write_text(INDEX_HTML.replace("{{COUNT}}", str(len(pool))).replace("{{DATE}}", today.isoformat()))
    (OUT / ".nojekyll").write_text("")

    print(f"Built for {today} · pool {len(pool)}")
    for name, n, first in summary:
        print(f"  {name}: {n} films · today's #1: {first}")


INDEX_HTML = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Tamil Gems addon</title>
<style>body{font-family:system-ui;max-width:560px;margin:40px auto;padding:0 16px;line-height:1.5}
code{background:#eee;padding:2px 6px;border-radius:4px;word-break:break-all}
button{padding:8px 14px;border-radius:6px;border:1px solid #888;cursor:pointer}</style></head>
<body><h1>🎬 Tamil Gems</h1>
<p>{{COUNT}} top-rated Tamil films (2000–today), reshuffled daily. Last build: {{DATE}}.</p>
<p>Add this URL in Nuvio → Settings → Addons:</p>
<p><code id="u"></code></p><button onclick="navigator.clipboard.writeText(u.textContent)">Copy URL</button>
<script>const u=document.getElementById('u');u.textContent=location.href.replace(/\\/(index\\.html)?$/,'')+'/manifest.json';</script>
</body></html>"""


if __name__ == "__main__":
    main()
