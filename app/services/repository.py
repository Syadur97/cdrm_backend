"""
idrms-backend · app/services/repository.py

All query logic against the `mauza` table. Central idea: GEO_CODE prefix
length maps 1:1 to admin level (2=division, 4=district, 8=upazila,
13=union/ward, 16=mauza), so a single `geo_code` filter — typed or picked
from /api/search — drives every endpoint at whatever level the user selected,
instead of needing separate district/upazila/union parameters for each step
of a drill-down UI.
"""
from __future__ import annotations
from typing import Optional
from app.database import pool

HIERARCHY_SELECT = ("geo_code, division_name, district_name, upazila_name, "
                     "union_name, mcpl_name, mauza_name, location_type")

_LEVEL_BY_LEN = {0: "national", 2: "division", 4: "district", 8: "upazila", 13: "union", 16: "mauza"}


def detect_level(geo_code: Optional[str]) -> str:
    n = len(geo_code) if geo_code else 0
    if n not in _LEVEL_BY_LEN:
        raise ValueError("geo_code must be 2 (division), 4 (district), 8 (upazila), "
                          "13 (union/ward) or 16 (mauza) digits.")
    return _LEVEL_BY_LEN[n]


def _where(district=None, upazila_code=None, union_code=None, mauza_code=None,
           geo_code=None, location_type=None, start=1):
    clauses, params = [], []
    idx = start
    if geo_code:
        clauses.append(f"geo_code LIKE ${idx}"); params.append(geo_code + "%"); idx += 1
    if district:
        clauses.append(f"district_name ILIKE ${idx}"); params.append(district); idx += 1
    if upazila_code is not None:
        clauses.append(f"upazila_code = ${idx}"); params.append(upazila_code); idx += 1
    if union_code is not None:
        clauses.append(f"union_code = ${idx}"); params.append(union_code); idx += 1
    if mauza_code is not None:
        clauses.append(f"mauza_code = ${idx}"); params.append(mauza_code); idx += 1
    if location_type:
        clauses.append(f"location_type ILIKE ${idx}"); params.append(location_type); idx += 1
    sql = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return sql, params, idx


# ── Metadata ──────────────────────────────────────────────────────────────────

async def get_dictionary(table: Optional[str] = None) -> list[dict]:
    if table:
        rows = await pool().fetch(
            "SELECT column_code, table_code, description, grp FROM indicator_dictionary "
            "WHERE table_code ILIKE $1 ORDER BY column_code", table)
    else:
        rows = await pool().fetch(
            "SELECT column_code, table_code, description, grp FROM indicator_dictionary "
            "ORDER BY table_code, column_code")
    return [dict(r) for r in rows]


async def get_meta() -> dict:
    p = pool()
    total = await p.fetchval("SELECT count(*) FROM mauza")
    districts = await p.fetch("SELECT DISTINCT district_name FROM mauza ORDER BY district_name")
    loc_types = await p.fetch("SELECT DISTINCT location_type FROM mauza ORDER BY location_type")
    tables = await p.fetch("SELECT DISTINCT table_code FROM indicator_dictionary ORDER BY table_code")
    n_ind = await p.fetchval("SELECT count(*) FROM indicator_dictionary")
    return {
        "hierarchy": ["Division", "District", "Upazila", "Union/Paurashava", "Mauza"],
        "geo_code_prefix_lengths": {"division": 2, "district": 4, "upazila": 8,
                                     "union": 13, "mauza": 16},
        "location_types": [r["location_type"] for r in loc_types],
        "districts": [r["district_name"] for r in districts],
        "tables": [r["table_code"] for r in tables],
        "indicator_count": n_ind,
        "total_mauzas": total,
    }


# ── Catalogue ─────────────────────────────────────────────────────────────────

async def list_divisions() -> list[dict]:
    rows = await pool().fetch(
        "SELECT DISTINCT LEFT(geo_code,2) AS geo_code, division_name FROM mauza ORDER BY division_name")
    return [dict(r) for r in rows]


async def list_districts() -> list[dict]:
    rows = await pool().fetch(
        "SELECT DISTINCT LEFT(geo_code,4) AS geo_code, division_name, district_name "
        "FROM mauza ORDER BY district_name")
    return [dict(r) for r in rows]


async def list_upazilas(district: Optional[str] = None) -> list[dict]:
    where, params, _ = _where(district=district)
    rows = await pool().fetch(
        f"SELECT DISTINCT LEFT(geo_code,8) AS geo_code, district_name, upazila_name "
        f"FROM mauza{where} ORDER BY upazila_name", *params)
    return [dict(r) for r in rows]


async def list_unions(district: Optional[str] = None, upazila_code: Optional[int] = None) -> list[dict]:
    where, params, _ = _where(district=district, upazila_code=upazila_code)
    rows = await pool().fetch(
        f"SELECT DISTINCT LEFT(geo_code,13) AS geo_code, district_name, upazila_name, "
        f"union_name, mcpl_name, location_type FROM mauza{where} "
        f"ORDER BY upazila_name, union_name", *params)
    return [dict(r) for r in rows]


async def list_mauzas(district=None, upazila_code=None, union_code=None,
                       limit: int = 2000, offset: int = 0) -> list[dict]:
    where, params, idx = _where(district=district, upazila_code=upazila_code, union_code=union_code)
    params += [limit, offset]
    rows = await pool().fetch(
        f"SELECT geo_code, district_name, upazila_name, union_name, mcpl_name, "
        f"mauza_name, location_type FROM mauza{where} ORDER BY geo_code "
        f"LIMIT ${idx} OFFSET ${idx+1}", *params)
    return [dict(r) for r in rows]


# ── Search + hierarchy drill-down ───────────────────────────────────────────────

async def search(q: str, level: Optional[str] = None, limit: int = 10) -> list[dict]:
    """Name search across one or all admin levels — the entry point for a
    frontend search box that resolves free text to a geo_code."""
    like = f"%{q}%"
    p = pool()
    queries = {
        "division": ("SELECT DISTINCT LEFT(geo_code,2) AS geo_code, division_name AS name, "
                      "'division' AS level, NULL AS parent FROM mauza WHERE division_name ILIKE $1"),
        "district": ("SELECT DISTINCT LEFT(geo_code,4) AS geo_code, district_name AS name, "
                      "'district' AS level, division_name AS parent FROM mauza WHERE district_name ILIKE $1"),
        "upazila":  ("SELECT DISTINCT LEFT(geo_code,8) AS geo_code, upazila_name AS name, "
                      "'upazila' AS level, district_name AS parent FROM mauza WHERE upazila_name ILIKE $1"),
        "union":    ("SELECT DISTINCT LEFT(geo_code,13) AS geo_code, union_name AS name, "
                      "'union' AS level, upazila_name AS parent FROM mauza WHERE union_name ILIKE $1"),
        "mauza":    ("SELECT geo_code, mauza_name AS name, "
                      "'mauza' AS level, union_name AS parent FROM mauza WHERE mauza_name ILIKE $1"),
    }
    levels = [level] if level and level in queries else queries.keys()
    results = []
    for lvl in levels:
        rows = await p.fetch(queries[lvl] + f" ORDER BY name LIMIT {int(limit)}", like)
        results.extend(dict(r) for r in rows)
    return results


async def children(geo_code: Optional[str] = None) -> list[dict]:
    """Immediate children of a geo_code — divisions if geo_code is omitted.
    Powers cascading dropdowns (pick division -> its districts -> ... -> mauzas)."""
    p = pool()
    if not geo_code:
        return await list_divisions()
    level = detect_level(geo_code)
    where = "WHERE geo_code LIKE $1"
    param = geo_code + "%"
    if level == "division":
        rows = await p.fetch(
            f"SELECT DISTINCT LEFT(geo_code,4) AS geo_code, district_name AS name "
            f"FROM mauza {where} ORDER BY name", param)
    elif level == "district":
        rows = await p.fetch(
            f"SELECT DISTINCT LEFT(geo_code,8) AS geo_code, upazila_name AS name "
            f"FROM mauza {where} ORDER BY name", param)
    elif level == "upazila":
        rows = await p.fetch(
            f"SELECT DISTINCT LEFT(geo_code,13) AS geo_code, union_name AS name, "
            f"location_type FROM mauza {where} ORDER BY name", param)
    elif level == "union":
        rows = await p.fetch(
            f"SELECT geo_code, mauza_name AS name FROM mauza {where} ORDER BY name", param)
    else:  # mauza — no children
        return []
    return [dict(r) for r in rows]


# ── Summary (weighted aggregation, any level via geo_code prefix) ──────────────

_SUMMARY_SQL = """
    SELECT
        count(*)                                              AS mauza_count,
        SUM(c01totpop)                                         AS total_population,
        SUM(c01mpop)                                           AS total_male,
        SUM(c01fpop)                                           AS total_female,
        SUM(c01hpop)                                           AS total_hijra,
        SUM(c01hhtot)                                          AS total_households,
        CASE WHEN SUM(c01fpop) > 0
             THEN ROUND(SUM(c01mpop)::numeric / SUM(c01fpop) * 100, 2) END AS overall_sex_ratio,
        CASE WHEN SUM(c01hhtot) > 0
             THEN ROUND(SUM(c18hhavg * c01hhtot) / SUM(c01hhtot), 2) END   AS avg_household_size,
        CASE WHEN SUM(c01totpop) > 0
             THEN ROUND(SUM(c05lr15at * c01totpop) / SUM(c01totpop), 2) END AS literacy_rate_15plus,
        CASE WHEN SUM(c14hht) > 0
             THEN ROUND(SUM(c17elecp * c14hht) / SUM(c14hht), 2) END       AS electricity_coverage_pct
    FROM mauza
"""

async def summary(geo_code: Optional[str] = None) -> dict:
    level = detect_level(geo_code)
    where = " WHERE geo_code LIKE $1" if geo_code else ""
    params = [geo_code + "%"] if geo_code else []
    row = await pool().fetchrow(_SUMMARY_SQL + where, *params)
    out = dict(row)
    out["level"] = level
    out["geo_code"] = geo_code
    return out


async def summary_breakdown(geo_code: Optional[str] = None) -> list[dict]:
    """Aggregated summary for every *immediate child* of geo_code (e.g. one row
    per district when geo_code is a division) — the 'summary table' view."""
    level = detect_level(geo_code)
    child_level = {"national": "division", "division": "district", "district": "upazila",
                    "upazila": "union", "union": "mauza"}[level]
    prefix_len = {"division": 2, "district": 4, "upazila": 8, "union": 13, "mauza": 16}[child_level]
    name_col = {"division": "division_name", "district": "district_name",
                "upazila": "upazila_name", "union": "union_name", "mauza": "mauza_name"}[child_level]
    where = " WHERE geo_code LIKE $1" if geo_code else ""
    params = [geo_code + "%"] if geo_code else []
    sql = f"""
        SELECT LEFT(geo_code, {prefix_len}) AS geo_code, {name_col} AS name,
               count(*) AS mauza_count,
               SUM(c01totpop) AS total_population,
               SUM(c01hhtot) AS total_households,
               CASE WHEN SUM(c01hhtot) > 0
                    THEN ROUND(SUM(c18hhavg * c01hhtot) / SUM(c01hhtot), 2) END AS avg_household_size,
               CASE WHEN SUM(c01totpop) > 0
                    THEN ROUND(SUM(c05lr15at * c01totpop) / SUM(c01totpop), 2) END AS literacy_rate_15plus
        FROM mauza{where}
        GROUP BY LEFT(geo_code, {prefix_len}), {name_col}
        ORDER BY name
    """
    rows = await pool().fetch(sql, *params)
    return [dict(r) for r in rows]


# ── Generic, dictionary-driven table access ────────────────────────────────────

async def get_table(table: str, district=None, upazila_code=None, union_code=None,
                     mauza_code=None, geo_code=None, location_type=None,
                     limit: int = 2000, offset: int = 0) -> Optional[list[dict]]:
    dict_rows = await get_dictionary(table)
    if not dict_rows:
        return None
    cols = [r["column_code"].lower() for r in dict_rows]
    where, params, idx = _where(district, upazila_code, union_code, mauza_code, geo_code, location_type)
    params += [limit, offset]
    sql = (f"SELECT {HIERARCHY_SELECT}, {', '.join(cols)} FROM mauza{where} "
           f"ORDER BY geo_code LIMIT ${idx} OFFSET ${idx+1}")
    rows = await pool().fetch(sql, *params)
    return [dict(r) for r in rows]


async def _topic(alias_map: dict[str, str], district=None, upazila_code=None, union_code=None,
                  mauza_code=None, geo_code=None, location_type=None,
                  limit: int = 2000, offset: int = 0) -> list[dict]:
    where, params, idx = _where(district, upazila_code, union_code, mauza_code, geo_code, location_type)
    params += [limit, offset]
    select_cols = ", ".join(f'{col} AS "{alias}"' for col, alias in alias_map.items())
    sql = (f"SELECT {HIERARCHY_SELECT}, {select_cols} FROM mauza{where} "
           f"ORDER BY geo_code LIMIT ${idx} OFFSET ${idx+1}")
    rows = await pool().fetch(sql, *params)
    return [dict(r) for r in rows]


# ── Named topic wrappers (thin — same filters/pagination as get_table) ─────────

POP_ALIAS = {"c01totpop": "Pop_Total", "c01mpop": "Pop_Male", "c01fpop": "Pop_Female",
             "c01hpop": "Pop_Hijra", "c01sxratio": "Sex_Ratio", "c01hhtot": "HH_Total",
             "c01hhgen": "HH_General", "c01hhinst": "HH_Institutional", "c18hhavg": "Avg_HH_Size"}

REL_ALIAS = {"c03totrel": "Religion_Total", "c03muslim": "Muslim", "c03hindu": "Hindu",
             "c03christn": "Christian", "c03budhist": "Buddhist", "c03othrel": "Religion_Others"}

LIT_ALIAS = {"c05lr5at": "Literacy_5Plus_Total", "c05lr5am": "Literacy_5Plus_Male",
             "c05lr5af": "Literacy_5Plus_Female", "c05lr15at": "Literacy_15Plus_Total",
             "c05lr15am": "Literacy_15Plus_Male", "c05lr15af": "Literacy_15Plus_Female"}

LAB_ALIAS = {"c08wspopt": "Labor_Pop_Total", "c09emppopt": "Employed_Total",
             "c09empagrt": "Agri_Total", "c09empindt": "Industry_Total", "c09empsrvt": "Service_Total"}

STRUCT_ALIAS = {"c14hht": "Structure_Total_HH", "c14hhpucp": "Structure_Pucca_Pct",
                 "c14hhspucp": "Structure_SemiPucca_Pct", "c14hhkanp": "Structure_Kancha_Pct",
                 "c14hhjhup": "Structure_Jhupri_Pct"}

WATER_ALIAS = {"c15dwt": "Water_Total_HH", "c15dwtapp": "Water_TapPipe_Pct",
                "c15dwtubep": "Water_TubeWell_Pct", "c15dwbotp": "Water_BottledJar_Pct",
                "c15dwwellp": "Water_Well_Pct", "c15dwpondp": "Water_PondRiverCanal_Pct"}

TOILET_ALIAS = {"c16toilt": "Toilet_Total_HH", "c16tlsafep": "Toilet_SafeFlush_Pct",
                 "c16tlpitsp": "Toilet_PitWithSlab_Pct", "c16tlpitop": "Toilet_PitNoSlab_Pct",
                 "c16tlhangp": "Toilet_OpenHanging_Pct", "c16tlopenp": "Toilet_OpenDefecation_Pct"}

DIGITAL_ALIAS = {"c11mob15tp": "Mobile_15Plus_Total_Pct", "c11int15tp": "Internet_15Plus_Total_Pct"}


async def get_population(**kw): return await _topic(POP_ALIAS, **kw)
async def get_religion(**kw): return await _topic(REL_ALIAS, **kw)
async def get_literacy(**kw): return await _topic(LIT_ALIAS, **kw)
async def get_labour(**kw): return await _topic(LAB_ALIAS, **kw)
async def get_housing_structure(**kw): return await _topic(STRUCT_ALIAS, **kw)
async def get_water(**kw): return await _topic(WATER_ALIAS, **kw)
async def get_toilet(**kw): return await _topic(TOILET_ALIAS, **kw)
async def get_digital(**kw): return await _topic(DIGITAL_ALIAS, **kw)
async def get_electricity(**kw):
    return await _topic({"c17elecp": "Electricity_Coverage_Pct"}, **kw)


async def get_age_pyramid(district=None, upazila_code=None, union_code=None,
                           mauza_code=None, geo_code=None, location_type=None) -> list[dict]:
    where, params, _ = _where(district, upazila_code, union_code, mauza_code, geo_code, location_type)
    age_cols = ["c02ag04", "c02ag59", "c02ag1014", "c02ag1519", "c02ag2024", "c02ag2529",
                "c02ag3034", "c02ag3539", "c02ag4044", "c02ag4549", "c02ag5054", "c02ag5559",
                "c02ag6064", "c02ag6569", "c02ag7074", "c02ag7579", "c02ag80pls"]
    labels = ["0-4", "5-9", "10-14", "15-19", "20-24", "25-29", "30-34", "35-39", "40-44",
              "45-49", "50-54", "55-59", "60-64", "65-69", "70-74", "75-79", "80+"]
    sums_sql = ", ".join(f"SUM({c}) AS {c}" for c in age_cols)
    row = await pool().fetchrow(f"SELECT {sums_sql} FROM mauza{where}", *params)
    d = dict(row)
    total = sum(v or 0 for v in d.values()) or 1
    return [{"age_group": lbl, "count": int(d[c] or 0),
              "pct": round((d[c] or 0) / total * 100, 2)} for c, lbl in zip(age_cols, labels)]


# ── Raw / flexible ────────────────────────────────────────────────────────────

async def get_raw(district=None, upazila_code=None, union_code=None, mauza_code=None,
                   geo_code=None, location_type=None, columns: Optional[str] = None,
                   limit: int = 2000, offset: int = 0) -> list[dict]:
    where, params, idx = _where(district, upazila_code, union_code, mauza_code, geo_code, location_type)
    params += [limit, offset]
    if columns:
        # whitelist against actual table columns to avoid arbitrary SQL injection via column names
        valid_cols = await pool().fetch(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'mauza'")
        valid = {r["column_name"] for r in valid_cols}
        select_cols = ", ".join(c.strip() for c in columns.split(",") if c.strip().lower() in valid)
        if not select_cols:
            select_cols = "geo_code"
    else:
        select_cols = "*"
    sql = f"SELECT {select_cols} FROM mauza{where} ORDER BY geo_code LIMIT ${idx} OFFSET ${idx+1}"
    rows = await pool().fetch(sql, *params)
    return [dict(r) for r in rows]
