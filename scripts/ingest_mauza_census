"""
idrms-backend · scripts/ingest_mauza_census.py

Loads the mauza-level BBS PHC 2022 workbook into PostgreSQL/PostGIS.

  1. Reads the "Metadata" sheet -> populates indicator_dictionary,
     and drives the generated column list for the mauza table (so the
     table schema can never drift out of sync with the dictionary).
  2. Reads the "Main Data" sheet -> cleans it (same sanitisation as the
     old pandas loader: strips zero-width/invisible characters, synthesises
     Location_Type) -> bulk-loads into `mauza` via execute_values batches.
  3. Builds indexes AFTER the bulk load (much faster than indexing row by row).

Usage:
    python scripts/ingest_mauza_census.py [path/to/workbook.xlsx]
    (defaults to settings.MAUZA_XLSX)

Design note: reading is done with openpyxl in read_only mode via
pandas.read_excel(engine="openpyxl") — fine up to the low hundreds of
thousands of rows. For a truly large national file, switch the reader to
`openpyxl.load_workbook(read_only=True)` + `iter_rows()` and stream batches
straight into `load_rows()` below rather than materialising the whole sheet
in a DataFrame first. `load_dataframe()` is the actual bulk-load entrypoint
and doesn't care where the DataFrame came from — it's exercised directly
against a synthetic 60k+ row DataFrame in the load test, no Excel involved.
"""
from __future__ import annotations
import logging
import re
import sys
import time
from pathlib import Path

import pandas as pd
import psycopg2
import psycopg2.extras

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import settings  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("ingest")

_INVISIBLE_CHARS = re.compile("[\u200b\u200c\u200d\ufeff]")

HIERARCHY_DDL = """
    geo_code       VARCHAR(16) PRIMARY KEY,
    div_code       SMALLINT NOT NULL,
    division_name  TEXT NOT NULL,
    dist_code      SMALLINT NOT NULL,
    district_name  TEXT NOT NULL,
    city_code      SMALLINT,
    city_name      TEXT,
    upazila_code   SMALLINT NOT NULL,
    upazila_name   TEXT NOT NULL,
    mcpl_code      SMALLINT,
    mcpl_name      TEXT,
    union_code     SMALLINT NOT NULL,
    union_name     TEXT NOT NULL,
    mauza_code     SMALLINT NOT NULL,
    mauza_name     TEXT NOT NULL,
    location_type  TEXT NOT NULL,
    geom           GEOMETRY(MultiPolygon, 4326)
"""
HIERARCHY_COLS = ["geo_code", "div_code", "division_name", "dist_code", "district_name",
                   "city_code", "city_name", "upazila_code", "upazila_name",
                   "mcpl_code", "mcpl_name", "union_code", "union_name",
                   "mauza_code", "mauza_name", "location_type"]

_SRC_TO_DB = {
    "GEO_CODE": "geo_code", "DIV_C": "div_code", "DIV_N": "division_name",
    "DIST_C": "dist_code", "DIST_N": "district_name", "CITY_CODE": "city_code",
    "CITY_NAME": "city_name", "UPZ_CO": "upazila_code", "UPZ_NA": "upazila_name",
    "MCPL_CODE": "mcpl_code", "MCPL_N": "mcpl_name", "UNION_CODE": "union_code",
    "UNION_NAME": "union_name", "MAUZA_CODE": "mauza_code", "MAUZA_NAME": "mauza_name",
}


def _clean_str(value):
    if pd.isna(value):
        return None
    return _INVISIBLE_CHARS.sub("", str(value)).strip()


def _pg_conn():
    return psycopg2.connect(settings.SYNC_DATABASE_URL)


# ── Dictionary + schema ─────────────────────────────────────────────────────────

def load_dictionary(xlsx_path: Path) -> pd.DataFrame:
    meta = pd.read_excel(xlsx_path, sheet_name=settings.METADATA_SHEET, engine="openpyxl")
    meta = meta.rename(columns={"Table Name": "table_code", "Column Name": "column_code",
                                 "Description": "description", "Group": "grp"})
    for c in ["table_code", "column_code", "description", "grp"]:
        if c in meta.columns:
            meta[c] = meta[c].map(_clean_str)
    return meta


def _column_type(series: pd.Series) -> str:
    s = series.dropna()
    if len(s) and (s == s.round(0)).all() and s.abs().max() < 2_000_000_000:
        return "INTEGER"
    return "NUMERIC(10,2)"


def build_schema(conn, dictionary: pd.DataFrame, sample_df: pd.DataFrame) -> list[str]:
    """Returns the ordered list of indicator DB column names (lowercased codes)."""
    indicator_cols = dictionary["column_code"].tolist()
    col_defs = []
    for code in indicator_cols:
        pg_type = _column_type(sample_df[code]) if code in sample_df.columns else "NUMERIC(10,2)"
        col_defs.append(f'    {code.lower()} {pg_type}')

    ddl = f"""
    CREATE EXTENSION IF NOT EXISTS postgis;
    CREATE EXTENSION IF NOT EXISTS pg_trgm;

    DROP TABLE IF EXISTS indicator_dictionary;
    CREATE TABLE indicator_dictionary (
        column_code  TEXT PRIMARY KEY,
        table_code   TEXT NOT NULL,
        description  TEXT,
        grp          TEXT
    );

    DROP TABLE IF EXISTS mauza;
    CREATE TABLE mauza (
{HIERARCHY_DDL},
{(',' + chr(10)).join(col_defs)}
    );

    CREATE INDEX idx_mauza_district      ON mauza (district_name);
    CREATE INDEX idx_mauza_upazila       ON mauza (dist_code, upazila_code);
    CREATE INDEX idx_mauza_union         ON mauza (dist_code, upazila_code, union_code);
    CREATE INDEX idx_mauza_geocode_text  ON mauza (geo_code text_pattern_ops);
    CREATE INDEX idx_mauza_geom          ON mauza USING GIST (geom);
    CREATE INDEX idx_mauza_name_trgm     ON mauza USING GIN (mauza_name gin_trgm_ops);
    CREATE INDEX idx_mauza_union_trgm    ON mauza USING GIN (union_name gin_trgm_ops);
    CREATE INDEX idx_mauza_upazila_trgm  ON mauza USING GIN (upazila_name gin_trgm_ops);
    CREATE INDEX idx_mauza_district_trgm ON mauza USING GIN (district_name gin_trgm_ops);
    """
    with conn.cursor() as cur:
        cur.execute(ddl)
    conn.commit()
    return indicator_cols


def load_dictionary_rows(conn, dictionary: pd.DataFrame) -> None:
    rows = [(r.column_code, r.table_code, r.description, r.grp)
            for r in dictionary.itertuples(index=False)]
    with conn.cursor() as cur:
        psycopg2.extras.execute_values(
            cur,
            "INSERT INTO indicator_dictionary (column_code, table_code, description, grp) VALUES %s",
            rows,
        )
    conn.commit()


# ── Data cleaning + bulk load ────────────────────────────────────────────────────

def clean_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for src_col in ["DIV_N", "DIST_N", "CITY_NAME", "UPZ_NA", "MCPL_N", "UNION_NAME", "MAUZA_NAME"]:
        if src_col in df.columns:
            df[src_col] = df[src_col].map(_clean_str)
    df["GEO_CODE"] = df["GEO_CODE"].astype("int64").astype(str).str.zfill(16)

    def _loc_type(row):
        if row.get("CITY_NAME"):
            return "City Corporation"
        if row.get("MCPL_N"):
            return "Paurashava"
        return "Union"
    df["Location_Type"] = df.apply(_loc_type, axis=1)
    return df


def load_dataframe(conn, df: pd.DataFrame, indicator_cols: list[str], batch_size: int = 5000) -> int:
    """Bulk-loads a cleaned mauza DataFrame into the `mauza` table. Pure function
    of a DataFrame — used both by the real xlsx ingestion path and directly
    against synthetic data for load testing, with no Excel involved either way."""
    db_indicator_cols = [c.lower() for c in indicator_cols]
    all_db_cols = HIERARCHY_COLS + db_indicator_cols
    src_cols = (["GEO_CODE", "DIV_C", "DIV_N", "DIST_C", "DIST_N", "CITY_CODE", "CITY_NAME",
                 "UPZ_CO", "UPZ_NA", "MCPL_CODE", "MCPL_N", "UNION_CODE", "UNION_NAME",
                 "MAUZA_CODE", "MAUZA_NAME", "Location_Type"] + indicator_cols)

    insert_sql = f"INSERT INTO mauza ({', '.join(all_db_cols)}) VALUES %s"
    total = 0
    t0 = time.time()
    with conn.cursor() as cur:
        for start in range(0, len(df), batch_size):
            chunk = df.iloc[start:start + batch_size]
            values = [tuple(None if pd.isna(v) else v for v in row) for row in chunk[src_cols].itertuples(index=False)]
            psycopg2.extras.execute_values(cur, insert_sql, values, page_size=batch_size)
            total += len(chunk)
    conn.commit()
    logger.info("Loaded %d rows in %.2fs (%.0f rows/s)", total, time.time() - t0, total / max(time.time() - t0, 1e-6))
    return total


def run(xlsx_path: Path | None = None) -> None:
    xlsx_path = xlsx_path or settings.MAUZA_XLSX
    logger.info("Reading workbook: %s", xlsx_path)
    t0 = time.time()
    dictionary = load_dictionary(xlsx_path)
    raw = pd.read_excel(xlsx_path, sheet_name=settings.MAUZA_SHEET, engine="openpyxl")
    logger.info("Read %d rows in %.2fs", len(raw), time.time() - t0)

    df = clean_dataframe(raw)

    conn = _pg_conn()
    try:
        indicator_cols = build_schema(conn, dictionary, raw)
        load_dictionary_rows(conn, dictionary)
        load_dataframe(conn, df, indicator_cols)
    finally:
        conn.close()
    logger.info("Done.")


if __name__ == "__main__":
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    run(path)
