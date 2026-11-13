# 🎬 Tamil Gems — Nuvio / Stremio addon

Top-rated Tamil films (2000 → today), shuffled fresh every day.

**Catalogs:** 🎲 Random Tamil Gems · 📅 2000s · 📅 2010s · 📅 2020s

## How it picks films
1. TMDB: every *original-language Tamil* film per year (no dubs).
2. IMDb public dataset: rating + votes.
3. Weighted rating `WR = v/(v+m)·R + m/(v+m)·C` (IMDb Top-250 formula) — a 9.2 with 40 votes can't beat a 7.9 with 50k.
4. Keep films with ≥300 votes, IMDb rating ≥ 6.1, WR ≥ 6.1, runtime ≥ 70 min → max 50 per year.
5. Daily: weighted shuffle (better films surface more often), seeded by date (same order all day, paging safe), no same-year clusters.

All knobs live in `config.json`.

## Schedule (GitHub Actions, free)
| When | What |
|---|---|
| Daily 00:05 IST | Reshuffle + deploy |
| 2nd & 17th of month | Hard refresh (new releases, updated ratings) + deploy |

Each refresh writes `data/report.md` — per-year counts, top pick and the weakest film that made the cut. Check it to tune `min_weighted_rating` / `bayes_m`.

## Setup (one time)
1. Create a public repo (e.g. `tamil-gems`), push these files.
2. **Settings → Secrets and variables → Actions** → New secret `TMDB_API_KEY` (v3 key or v4 read token both work).
3. **Settings → Pages → Source: GitHub Actions**.
4. **Actions → Tamil Gems addon → Run workflow** → tick **Hard refresh** → Run. (First run: ~10–15 min.)
5. In Nuvio, add: `https://<your-username>.github.io/tamil-gems/manifest.json`

Item IDs are IMDb `tt…` ids, so posters/metadata (Cinemeta/TMDB), stream addons and IMDb-rating overlays work as with any other catalog.
