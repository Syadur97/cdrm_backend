# idrms-backend

**Integrated Disaster Risk Management System — FastAPI backend**
Mauza-level Population & Housing Census 2022 data (BBS), backed by
**PostgreSQL + PostGIS**. Full Division → District → Upazila →
Union/Paurashava → Mauza hierarchy, queryable by geocode or by name at any
level.

> **v3 cutover:** the in-memory pandas/xlsx MVP (v2) is retired. At
> 60,000+ mauza rows, Postgres does the filtering/aggregation instead of
> pandas doing a full in-memory scan per request. The old code is preserved,
> not deleted — see `app/services/legacy_pandas_mvp/README.md`.

---

## Setup

```bash
cd idrms-backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # defaults match the docker-compose below

# Postgres + PostGIS
docker compose up -d db
# (or point .env at an existing instance — anywhere with the PostGIS
# extension available, e.g. Render's managed Postgres, works)

# Load the census workbook into Postgres (builds the schema from the
# Metadata sheet, then bulk-loads Main Data)
python scripts/ingest_mauza_census.py

# Run the API
uvicorn app.main:app --reload --port 8000
```

Interactive docs → **http://localhost:8000/docs**

Re-run `python scripts/ingest_mauza_census.py path/to/new_file.xlsx` any
time you get an updated workbook (e.g. the full national file once it's
ready) — it drops and rebuilds the `mauza` table each time, so it's always
a clean load, not an incremental merge.

---

## Using Neon instead of local Docker Postgres

Works fine, PostGIS included — Neon supports it the same way local Postgres
does, self-serve via `CREATE EXTENSION postgis` (which the ingestion script
already runs). To switch:

1. Create a Neon project, then open **Connect** on the dashboard.
2. Copy the **direct** connection string — not the one with `-pooler` in the
   hostname. Fill in `.env` from it (see the Neon block in `.env.example`),
   and set `POSTGRES_SSLMODE=require` — Neon rejects any connection without
   TLS.
3. Run `python scripts/ingest_mauza_census.py` and `uvicorn app.main:app`
   exactly as before. Nothing else changes — `docker compose up -d db`
   becomes unnecessary since Neon *is* the Postgres server now.

**Why the direct string, not the pooled one:** Neon's pooled endpoint routes
through PgBouncer, which exists to solve "thousands of short-lived
serverless connections exhausting Postgres." That's not your situation —
`uvicorn` is a persistent process, and `app/database.py` already opens its
own small connection pool (2-10 connections) once at startup and reuses it
for every request. Layering PgBouncer under that solves a problem you don't
have and adds one you don't want: transaction-mode pooling drops session
state between requests, which breaks `SET`, `LISTEN/NOTIFY`, and — this is
the specific asyncpg gotcha — can conflict with asyncpg's prepared
statements. The direct connection has none of that, and your actual
connection count (≤10, plus one from the ingestion script when it runs)
sits nowhere near Neon's direct-connection ceiling (97 usable on the
smallest 0.25 CU compute size). Keep the pooled string in your back pocket
only if you later add something that opens a connection per request
(serverless functions, edge middleware) — not needed for this app.

**Two free-tier things worth knowing before you commit to it:**
- **0.5 GB storage per project.** The current 99-row sample uses a few MB.
  Fine at 60,000+ rows without geometry too — I load-tested that scale (see
  above) on a plain Docker Postgres and the numeric data alone stays well
  under the cap. Once real mauza boundary polygons are joined in across the
  full national dataset, storage is worth watching; Neon's paid tier is
  $0.35/GB-month with no minimum if you outgrow it.
- **Scale-to-zero.** Free-tier compute suspends after 5 minutes idle, and
  the first request after that eats a ~300-500ms cold start. Fine for
  development; for an actual emergency-response deployment where a burst of
  traffic during an incident can't afford that delay, disable scale-to-zero
  (available on the paid tiers).

I verified the PostGIS extension, prepared-statement, and connection-string
details above against Neon's current docs, but couldn't do a live
end-to-end connection test from this environment — outbound network here
is restricted to a fixed allowlist that doesn't include neon.tech. Worth a
quick smoke test (`python scripts/ingest_mauza_census.py`) against your
actual Neon project before relying on it.

---

## The query pattern this was built for

> select something by geocode or name → summary → drill into detail

```
/api/search?q=Amtali                         → [{ geo_code: "10040009", level: "upazila", ... }]
/api/hierarchy/children?geo_code=1004        → immediate children (cascading dropdown)
/api/summary?geo_code=10040009               → headline KPIs for that upazila
/api/summary/breakdown?geo_code=1004         → one row per upazila in that district (summary table)
/api/population?geo_code=10040009            → full mauza-level detail rows (details dashboard)
/api/tables/C09?geo_code=10040009            → any BBS table, same pattern
```

**`geo_code` is the single mechanism that drives every level.** Its length
tells the API what it is — no separate district/upazila/union params needed
once you have a geo_code from search or the hierarchy endpoint:

| Length | Level | Example |
|---|---|---|
| 2  | Division | `10` |
| 4  | District | `1004` |
| 8  | Upazila  | `10040009` |
| 13 | Union / Paurashava ward | `1004000900109` |
| 16 | Mauza (exact) | `1004000900109036` |

The explicit `district=` / `upazila_code=` / `union_code=` / `mauza_code=`
filters still work on every endpoint too, for callers that already know
those values and don't need the search/geocode flow.

---

## Load-tested at 60,000+ rows

The real workbook is a 99-row sample (see "Known gaps" below), so before
committing to this design it was stress-tested against a synthetic
61,440-row dataset generated in-memory (not written to xlsx — see the note
in `scripts/ingest_mauza_census.py` about why). Measured on a single-core,
4GB sandbox:

| Operation | Time |
|---|---|
| Bulk load 61,440 rows into Postgres | ~80s (one-time batch job, not a request) |
| geo_code prefix lookup, any level (indexed) | **< 1ms** |
| Filter by district_name (indexed) | ~1-2ms |
| District-level detail pull, ~1000 mauza rows, narrow columns | ~7ms |
| District-level detail pull, ~1000 rows, `SELECT *` (211 cols) | ~700ms |
| Weighted-average summary, single district | ~4ms |
| Weighted-average summary, national (full table scan) | ~140ms |
| Name search (trigram index), broad match | ~50-70ms |

Takeaway: the named/generic topic endpoints (narrow column selection) are
fast at any practical query scope. `/api/data` with no `columns=` filter —
i.e. `SELECT *` across all 211 columns — is the one path that gets
noticeably slower for a wide pull; use it for exports/debugging, not as a
dashboard's primary data source. If it becomes a bottleneck later, that's a
column-projection or response-serialization optimization, not a schema
problem.

---

## Data model

`mauza` — one row per mauza, hierarchy columns (with `geo_code`, `div_code`
… `mauza_code`, `location_type`) + a `geom GEOMETRY(MultiPolygon, 4326)`
column (null until the boundary shapefile is joined in, see below) + 195
census indicator columns, typed INTEGER or NUMERIC(10,2) automatically from
the real data (127 are whole-number counts, 68 are rates/percentages).

`indicator_dictionary` — column_code → table_code → description → group,
loaded straight from the workbook's "Metadata" sheet. Served at
`/api/meta/dictionary` and used internally by `/api/tables/{code}` to
resolve which columns belong to which BBS table — the schema and the API
can't drift out of sync with each other.

Indexes: btree on `district_name`, `(dist_code, upazila_code)`,
`(dist_code, upazila_code, union_code)`; `text_pattern_ops` on `geo_code`
for prefix search; GIN/trigram on the four name columns for `/api/search`;
GIST on `geom` for when boundaries land.

---

## API endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/api/meta` | Hierarchy levels, districts, BBS tables, row/indicator counts |
| GET | `/api/meta/dictionary` | Full data dictionary (`?table=C05` to scope) |
| GET | `/api/search` | Name → geo_code, any level (`?q=&level=`) |
| GET | `/api/hierarchy/children` | Immediate children of a geo_code (cascading dropdown) |
| GET | `/api/divisions` `/api/districts` `/api/upazilas` `/api/unions` `/api/mauzas` | Catalogue at each level |
| GET | `/api/summary` | Headline KPIs at any geo_code level |
| GET | `/api/summary/breakdown` | One aggregated row per immediate child |
| GET | `/api/tables/{code}` | Generic, dictionary-driven detail view of any BBS table |
| GET | `/api/population` `/api/population/age-pyramid` `/api/religion` `/api/literacy` `/api/labour` `/api/housing/*` `/api/digital` | Named topic views (subset of tables, friendly field names) |
| GET | `/api/data` | Flexible raw query + column projection |

All topic/table endpoints accept `?district=`, `?upazila_code=`,
`?union_code=`, `?mauza_code=`, `?geo_code=`, `?location_type=`, and
paginate via `?limit=` (default 2000, max 20000) / `?offset=`.

---

## Known gaps / TODO

1. **Still the 99-row Barguna sample.** Nothing else needs to change to
   load the full national file — `python scripts/ingest_mauza_census.py
   path/to/full_file.xlsx` rebuilds the schema and reloads.
2. **`geom` is unpopulated.** Once the matching mauza-level shapefile is
   available: `ogr2ogr -f PostgreSQL PG:"host=localhost dbname=idrms
   user=postgres password=postgres" your_shapefile.shp -nln
   mauza_boundary`, then `UPDATE mauza SET geom = b.geom FROM
   mauza_boundary b WHERE mauza.geo_code = b.geo_code` (adjust the geocode
   field name/join to match whatever your shapefile actually carries).
3. **Named topic endpoints cover a subset of the 18 BBS tables** (the ones
   the old MVP had, plus the ones already wired). Everything else —
   marital status (C04), students (C07), NEET (C10), financial inclusion
   (C12), cooking fuel/household size (C17/C18) — is fully queryable via
   `/api/tables/{code}` today; adding named wrappers for them is a
   mechanical follow-up, not a design change.
4. **`_Pct` fields are percentages, not counts** — same caveat as the v2
   cutover, still applies here.
