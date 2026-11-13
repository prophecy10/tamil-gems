#!/usr/bin/env python3
"""
Hard refresh (run ~twice a month).

1. TMDB discover: every original-language Tamil film, year by year (2000 -> today).
2. TMDB details (cached in data/tmdb_cache.json): IMDb id, runtime, genres.
3. IMDb public dataset: rating + vote count for each film.
4. Bayesian weighted rating -> keep films above threshold, max N per year.
5. Write data/pool.json (+ data/report.md for eyeballing).

Stdlib only. Needs env TMDB_API_KEY (v3 key or v4 read-access token).
"""
import concurrent.futures as cf
import datetime as dt
import gzip
import io
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CFG = json.loads((ROOT / "config.json").read_text())

TMDB = "https://api.themoviedb.org/3"
IMDB_RATINGS = "https://datasets.imdbws.com/title.ratings.tsv.gz"
IMG = "https://image.tmdb.org/t/p/"


# ---------------------------------------------------------------- HTTP
def _auth():
    key = os.environ.get("TMDB_API_KEY", "").strip()
    if not key:
        sys.exit("TMDB_API_KEY env var missing")
    if key.startswith("eyJ"):  # v4 read-access token (JWT)
        return {}, {"Authorization": f"Bearer {key}"}
    return {"api_key": key}, {}


AUTH_PARAMS, AUTH_HEADERS = _auth() if __name__ == "__main__" else ({}, {})


def get_json(path, **params):
    params = {**AUTH_PARAMS, **params}
    url = f"{TMDB}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={**AUTH_HEADERS, "Accept": "application/json"})
    for attempt in range(6):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code in (429, 500, 502, 503, 504):
                time.sleep(int(e.headers.get("Retry-After") or 0) or 2 ** attempt)
                continue
            raise
        except (urllib.error.URLError, TimeoutError):
            time.sleep(2 ** attempt)
    raise RuntimeError(f"TMDB failed after retries: {path}")


# ---------------------------------------------------------------- steps
def discover_year(year, today):
    end = min(dt.date(year, 12, 31), today)
    out, page, total = [], 1, 1
    while page <= min(total, CFG["max_pages_per_year"]):
        d = get_json(
            "/discover/movie",
            with_original_language=CFG["language"],
            **{"primary_release_date.gte": f"{year}-01-01",
               "primary_release_date.lte": end.isoformat()},
            sort_by="popularity.desc",
            include_adult="false",
            include_video="false",
            language="en-US",
            page=page,
        )
        out += d.get("results", [])
        total = d.get("total_pages", 1)
        page += 1
    return out


def fetch_details(tmdb_id):
    d = get_json(f"/movie/{tmdb_id}", language="en-US")
    if not d:
        return tmdb_id, {"imdb_id": None, "runtime": 0, "genres": []}
    return tmdb_id, {
        "imdb_id": d.get("imdb_id") or None,
        "runtime": d.get("runtime") or 0,
        "genres": [g["name"] for g in d.get("genres", [])],
    }


def load_imdb_ratings(wanted):
    print(f"Downloading IMDb ratings dataset…")
    req = urllib.request.Request(IMDB_RATINGS, headers={"User-Agent": "tamil-gems-addon"})
    with urllib.request.urlopen(req, timeout=120) as r:
        raw = r.read()
    ratings = {}
    with gzip.open(io.BytesIO(raw), "rt", encoding="utf-8") as f:
        next(f)
        for line in f:
            tt, rating, votes = line.rstrip("\n").split("\t")
            if tt in wanted:
                ratings[tt] = (float(rating), int(votes))
    return ratings


# ---------------------------------------------------------------- scoring
def select_pool(films, ratings, cache):
    """films: {tmdb_id: discover-row}. Returns (pool list, stats dict)."""
    rows = {}
    for tid, f in films.items():
        det = cache.get(str(tid)) or {}
        tt = det.get("imdb_id")
        if not tt or tt not in ratings:
            continue
        runtime = det.get("runtime") or 0
        if 0 < runtime < CFG["min_runtime_minutes"]:
            continue  # shorts / featurettes (0 = unknown, keep)
        rating, votes = ratings[tt]
        if votes < CFG["min_votes"]:
            continue
        year = int((f.get("release_date") or "0")[:4] or 0)
        if year < CFG["start_year"]:
            continue
        row = {
            "id": tt,
            "tmdb_id": tid,
            "name": f.get("title") or f.get("original_title"),
            "year": year,
            "poster": f"{IMG}w500{f['poster_path']}" if f.get("poster_path") else None,
            "background": f"{IMG}w1280{f['backdrop_path']}" if f.get("backdrop_path") else None,
            "overview": (f.get("overview") or "").strip(),
            "genres": det.get("genres", []),
            "imdb_rating": rating,
            "imdb_votes": votes,
        }
        # de-dupe: same IMDb id on two TMDB entries -> keep the one with a poster
        if tt not in rows or (not rows[tt]["poster"] and row["poster"]):
            rows[tt] = row

    if not rows:
        return [], {"C": 0, "eligible": 0}

    # C = mean over ALL rated Tamil films (before the rating cut), so the prior isn't inflated
    C = sum(r["imdb_rating"] for r in rows.values()) / len(rows)
    rows = {k: r for k, r in rows.items() if r["imdb_rating"] >= CFG["min_imdb_rating"]}
    m = CFG["bayes_m"]
    for r in rows.values():
        v, R = r["imdb_votes"], r["imdb_rating"]
        r["wr"] = round((v / (v + m)) * R + (m / (v + m)) * C, 3)

    by_year = {}
    for r in rows.values():
        by_year.setdefault(r["year"], []).append(r)

    pool = []
    for y in sorted(by_year):
        keep = sorted(
            (r for r in by_year[y] if r["wr"] >= CFG["min_weighted_rating"]),
            key=lambda r: r["wr"], reverse=True,
        )[: CFG["per_year_cap"]]
        pool += keep
    return pool, {"C": round(C, 3), "eligible": len(rows)}


def write_report(pool, stats, n_candidates):
    by_year = {}
    for r in pool:
        by_year.setdefault(r["year"], []).append(r)
    lines = [
        f"# Tamil Gems pool — refreshed {dt.date.today().isoformat()}",
        "",
        f"- TMDB Tamil candidates scanned: **{n_candidates}**",
        f"- With IMDb ≥{CFG['min_imdb_rating']} & ≥{CFG['min_votes']} votes: **{stats['eligible']}**",
        f"- Tamil IMDb mean (C): **{stats['C']}** · m = {CFG['bayes_m']} · WR floor = {CFG['min_weighted_rating']}",
        f"- **Final pool: {len(pool)} films**",
        "",
        "| Year | Count | Top pick | Weakest pick (cut-off) |",
        "|---|---|---|---|",
    ]
    for y in sorted(by_year):
        rs = by_year[y]
        top, low = rs[0], rs[-1]
        lines.append(
            f"| {y} | {len(rs)} | {top['name']} ({top['imdb_rating']}, WR {top['wr']}) "
            f"| {low['name']} ({low['imdb_rating']}, {low['imdb_votes']:,} votes, WR {low['wr']}) |"
        )
    (DATA / "report.md").write_text("\n".join(lines) + "\n")


# ---------------------------------------------------------------- main
def main():
    DATA.mkdir(exist_ok=True)
    today = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=CFG["utc_offset_minutes"])).date()

    films = {}
    for y in range(CFG["start_year"], today.year + 1):
        rows = discover_year(y, today)
        for f in rows:
            films[f["id"]] = f
        print(f"{y}: {len(rows)} Tamil titles on TMDB")
    print(f"Total candidates: {len(films)}")

    cache_path = DATA / "tmdb_cache.json"
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    missing = [tid for tid in films if str(tid) not in cache]
    print(f"Fetching details for {len(missing)} new titles (cached: {len(films) - len(missing)})")
    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        for i, (tid, det) in enumerate(ex.map(fetch_details, missing), 1):
            cache[str(tid)] = det
            if i % 500 == 0:
                print(f"  …{i}/{len(missing)}")
    cache_path.write_text(json.dumps(cache, separators=(",", ":"), sort_keys=True))

    wanted = {c["imdb_id"] for c in cache.values() if c.get("imdb_id")}
    ratings = load_imdb_ratings(wanted)
    print(f"IMDb ratings matched: {len(ratings)}")

    pool, stats = select_pool(films, ratings, cache)

    # Safety net: don't publish a broken refresh (API outage, schema change…)
    pool_path = DATA / "pool.json"
    if pool_path.exists():
        prev = len(json.loads(pool_path.read_text()))
        if prev and len(pool) < prev * 0.6:
            sys.exit(f"ABORT: pool shrank {prev} -> {len(pool)}; keeping previous pool.")
    if len(pool) < 100:
        sys.exit(f"ABORT: pool only {len(pool)} films — something is off.")

    pool_path.write_text(json.dumps(pool, ensure_ascii=False, indent=0))
    write_report(pool, stats, len(films))
    print(f"Pool written: {len(pool)} films (C={stats['C']})")


if __name__ == "__main__":
    main()
