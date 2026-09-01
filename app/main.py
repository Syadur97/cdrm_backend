"""
idrms-backend · app/main.py  v3.0 — PostgreSQL/PostGIS-backed

At 60,000+ mauza rows the in-memory pandas/xlsx MVP is retired. Data now
lives in Postgres (loaded via scripts/ingest_mauza_census.py) and every
endpoint here queries it directly via asyncpg.

Core query pattern — GEO_CODE-driven drill-down:
  1. /api/search?q=<text>            -> resolve a typed name to a geo_code
     (or /api/hierarchy/children?geo_code=<parent> for cascading dropdowns)
  2. /api/summary?geo_code=<code>    -> headline KPIs for whatever level that
     geo_code represents (2/4/8/13/16 digits = division/district/upazila/
     union/mauza — auto-detected from length)
  3. /api/summary/breakdown?geo_code=<code> -> one summary row per immediate
     child (e.g. per-district when geo_code is a division) — the "initial
     summary table" view
  4. Any topic endpoint (/api/population, /api/tables/{code}, ...) with the
     same geo_code -> full detail rows for the details dashboard

Every list endpoint also still accepts the old district / upazila_code /
union_code / mauza_code / location_type filters for direct/explicit queries,
and paginates via ?limit=&offset= (default 2000 / 0) since a district-wide
mauza-level pull can be thousands of rows.
"""
from __future__ import annotations
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import connect, disconnect
from app.services import repository as repo


@asynccontextmanager
async def lifespan(app: FastAPI):
    await connect()
    yield
    await disconnect()


app = FastAPI(
    title=settings.APP_TITLE,
    version="3.0.0",
    description=settings.APP_DESCRIPTION + " Backed by PostgreSQL/PostGIS.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/", tags=["Root"])
def root():
    return {"message": f"{settings.APP_TITLE} v3.0.0 — /docs for reference"}


# ── Metadata ──────────────────────────────────────────────────────────────────

@app.get("/api/meta", tags=["Metadata"])
async def get_metadata():
    return await repo.get_meta()


@app.get("/api/meta/dictionary", tags=["Metadata"])
async def get_dictionary(table: Optional[str] = Query(None, description="e.g. C05 — omit for all indicators")):
    return await repo.get_dictionary(table)


# ── Search & hierarchy drill-down ───────────────────────────────────────────────

@app.get("/api/search", tags=["Search"])
async def search(
    q: str = Query(..., min_length=2, description="Free-text admin unit name"),
    level: Optional[str] = Query(None, description="division|district|upazila|union|mauza — omit to search all"),
    limit: int = Query(10, le=100),
):
    """Resolve a typed name to its geo_code — feeds a frontend search box."""
    return await repo.search(q, level, limit)


@app.get("/api/hierarchy/children", tags=["Search"])
async def hierarchy_children(geo_code: Optional[str] = Query(None, description="omit for top-level divisions")):
    """Immediate children of geo_code — feeds cascading dropdowns."""
    try:
        return await repo.children(geo_code)
    except ValueError as exc:
        raise HTTPException(422, str(exc))


# ── Catalogue ─────────────────────────────────────────────────────────────────

@app.get("/api/divisions", tags=["Catalogue"])
async def list_divisions():
    return await repo.list_divisions()


@app.get("/api/districts", tags=["Catalogue"])
async def list_districts():
    return await repo.list_districts()


@app.get("/api/upazilas", tags=["Catalogue"])
async def list_upazilas(district: Optional[str] = Query(None)):
    return await repo.list_upazilas(district)


@app.get("/api/unions", tags=["Catalogue"])
async def list_unions(district: Optional[str] = Query(None), upazila_code: Optional[int] = Query(None)):
    return await repo.list_unions(district, upazila_code)


@app.get("/api/mauzas", tags=["Catalogue"])
async def list_mauzas(
    district: Optional[str] = Query(None), upazila_code: Optional[int] = Query(None),
    union_code: Optional[int] = Query(None), limit: int = Query(2000, le=20000), offset: int = Query(0, ge=0),
):
    return await repo.list_mauzas(district, upazila_code, union_code, limit, offset)


# ── Summary ───────────────────────────────────────────────────────────────────

@app.get("/api/summary", tags=["Summary"])
async def get_summary(geo_code: Optional[str] = Query(None, description="omit for national")):
    """Headline KPIs for a geo_code at any level — the 'initial summary' view."""
    try:
        return await repo.summary(geo_code)
    except ValueError as exc:
        raise HTTPException(422, str(exc))


@app.get("/api/summary/breakdown", tags=["Summary"])
async def get_summary_breakdown(geo_code: Optional[str] = Query(None, description="omit for national -> divisions")):
    """One aggregated row per immediate child of geo_code (districts under a
    division, upazilas under a district, ...) — the summary *table* view."""
    try:
        return await repo.summary_breakdown(geo_code)
    except ValueError as exc:
        raise HTTPException(422, str(exc))


# ── Generic table access ────────────────────────────────────────────────────────

@app.get("/api/tables/{table}", tags=["Generic"])
async def get_table(
    table: str,
    district: Optional[str] = Query(None), upazila_code: Optional[int] = Query(None),
    union_code: Optional[int] = Query(None), mauza_code: Optional[int] = Query(None),
    geo_code: Optional[str] = Query(None), location_type: Optional[str] = Query(None),
    limit: int = Query(2000, le=20000), offset: int = Query(0, ge=0),
):
    """Row-level (mauza granularity) view of any BBS table, e.g. /api/tables/C05."""
    rows = await repo.get_table(table, district, upazila_code, union_code, mauza_code,
                                 geo_code, location_type, limit, offset)
    if rows is None:
        raise HTTPException(404, f"Table '{table}' not found — see /api/meta for valid codes.")
    return rows


# ── Named topic endpoints ────────────────────────────────────────────────────────

def _kw(district, upazila_code, union_code, mauza_code, geo_code, location_type, limit, offset):
    return dict(district=district, upazila_code=upazila_code, union_code=union_code,
                mauza_code=mauza_code, geo_code=geo_code, location_type=location_type,
                limit=limit, offset=offset)


@app.get("/api/population", tags=["Population"])
async def get_population(district: Optional[str] = Query(None), upazila_code: Optional[int] = Query(None),
                          union_code: Optional[int] = Query(None), mauza_code: Optional[int] = Query(None),
                          geo_code: Optional[str] = Query(None), location_type: Optional[str] = Query(None),
                          limit: int = Query(2000, le=20000), offset: int = Query(0, ge=0)):
    return await repo.get_population(**_kw(district, upazila_code, union_code, mauza_code, geo_code, location_type, limit, offset))


@app.get("/api/population/age-pyramid", tags=["Population"])
async def get_age_pyramid(district: Optional[str] = Query(None), upazila_code: Optional[int] = Query(None),
                           union_code: Optional[int] = Query(None), mauza_code: Optional[int] = Query(None),
                           geo_code: Optional[str] = Query(None), location_type: Optional[str] = Query(None)):
    return await repo.get_age_pyramid(district, upazila_code, union_code, mauza_code, geo_code, location_type)


@app.get("/api/religion", tags=["Demographics"])
async def get_religion(district: Optional[str] = Query(None), upazila_code: Optional[int] = Query(None),
                        union_code: Optional[int] = Query(None), mauza_code: Optional[int] = Query(None),
                        geo_code: Optional[str] = Query(None), location_type: Optional[str] = Query(None),
                        limit: int = Query(2000, le=20000), offset: int = Query(0, ge=0)):
    return await repo.get_religion(**_kw(district, upazila_code, union_code, mauza_code, geo_code, location_type, limit, offset))


@app.get("/api/literacy", tags=["Education"])
async def get_literacy(district: Optional[str] = Query(None), upazila_code: Optional[int] = Query(None),
                        union_code: Optional[int] = Query(None), mauza_code: Optional[int] = Query(None),
                        geo_code: Optional[str] = Query(None), location_type: Optional[str] = Query(None),
                        limit: int = Query(2000, le=20000), offset: int = Query(0, ge=0)):
    return await repo.get_literacy(**_kw(district, upazila_code, union_code, mauza_code, geo_code, location_type, limit, offset))


@app.get("/api/labour", tags=["Economy"])
async def get_labour(district: Optional[str] = Query(None), upazila_code: Optional[int] = Query(None),
                      union_code: Optional[int] = Query(None), mauza_code: Optional[int] = Query(None),
                      geo_code: Optional[str] = Query(None), location_type: Optional[str] = Query(None),
                      limit: int = Query(2000, le=20000), offset: int = Query(0, ge=0)):
    return await repo.get_labour(**_kw(district, upazila_code, union_code, mauza_code, geo_code, location_type, limit, offset))


@app.get("/api/housing/structure", tags=["Housing"])
async def get_housing_structure(district: Optional[str] = Query(None), upazila_code: Optional[int] = Query(None),
                                 union_code: Optional[int] = Query(None), mauza_code: Optional[int] = Query(None),
                                 geo_code: Optional[str] = Query(None), location_type: Optional[str] = Query(None),
                                 limit: int = Query(2000, le=20000), offset: int = Query(0, ge=0)):
    return await repo.get_housing_structure(**_kw(district, upazila_code, union_code, mauza_code, geo_code, location_type, limit, offset))


@app.get("/api/housing/water", tags=["Housing"])
async def get_water(district: Optional[str] = Query(None), upazila_code: Optional[int] = Query(None),
                     union_code: Optional[int] = Query(None), mauza_code: Optional[int] = Query(None),
                     geo_code: Optional[str] = Query(None), location_type: Optional[str] = Query(None),
                     limit: int = Query(2000, le=20000), offset: int = Query(0, ge=0)):
    return await repo.get_water(**_kw(district, upazila_code, union_code, mauza_code, geo_code, location_type, limit, offset))


@app.get("/api/housing/toilet", tags=["Housing"])
async def get_toilet(district: Optional[str] = Query(None), upazila_code: Optional[int] = Query(None),
                      union_code: Optional[int] = Query(None), mauza_code: Optional[int] = Query(None),
                      geo_code: Optional[str] = Query(None), location_type: Optional[str] = Query(None),
                      limit: int = Query(2000, le=20000), offset: int = Query(0, ge=0)):
    return await repo.get_toilet(**_kw(district, upazila_code, union_code, mauza_code, geo_code, location_type, limit, offset))


@app.get("/api/housing/electricity", tags=["Housing"])
async def get_electricity(district: Optional[str] = Query(None), upazila_code: Optional[int] = Query(None),
                           union_code: Optional[int] = Query(None), mauza_code: Optional[int] = Query(None),
                           geo_code: Optional[str] = Query(None), location_type: Optional[str] = Query(None),
                           limit: int = Query(2000, le=20000), offset: int = Query(0, ge=0)):
    return await repo.get_electricity(**_kw(district, upazila_code, union_code, mauza_code, geo_code, location_type, limit, offset))


@app.get("/api/digital", tags=["Digital Inclusion"])
async def get_digital(district: Optional[str] = Query(None), upazila_code: Optional[int] = Query(None),
                       union_code: Optional[int] = Query(None), mauza_code: Optional[int] = Query(None),
                       geo_code: Optional[str] = Query(None), location_type: Optional[str] = Query(None),
                       limit: int = Query(2000, le=20000), offset: int = Query(0, ge=0)):
    return await repo.get_digital(**_kw(district, upazila_code, union_code, mauza_code, geo_code, location_type, limit, offset))


# ── Raw ───────────────────────────────────────────────────────────────────────

@app.get("/api/data", tags=["Raw"])
async def get_raw(
    district: Optional[str] = Query(None), upazila_code: Optional[int] = Query(None),
    union_code: Optional[int] = Query(None), mauza_code: Optional[int] = Query(None),
    geo_code: Optional[str] = Query(None), location_type: Optional[str] = Query(None),
    columns: Optional[str] = Query(None, description="Comma-separated column names"),
    limit: int = Query(2000, le=20000), offset: int = Query(0, ge=0),
):
    return await repo.get_raw(district, upazila_code, union_code, mauza_code,
                               geo_code, location_type, columns, limit, offset)
