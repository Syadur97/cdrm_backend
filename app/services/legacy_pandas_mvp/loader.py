"""
idrms-backend · app/services/loader.py
Loads and caches mauza_census.xlsx (Main Data + Metadata sheets) + boundary.geojson.
"""
from __future__ import annotations
import json, logging, re
from functools import lru_cache
from pathlib import Path
import numpy as np
import pandas as pd
from app.config import settings

logger = logging.getLogger(__name__)

# Zero-width / invisible characters observed in the source workbook (e.g. a
# U+200C ZERO WIDTH NON-JOINER embedded in one union name). Same class of
# problem as the \x86 bytes previously found in the district-level JIAF CSV —
# BBS exports routinely carry stray control/formatting characters that don't
# show up until something does an exact string match against them.
_INVISIBLE_CHARS = re.compile("[\u200b\u200c\u200d\ufeff]")

# Hierarchy / label columns as they appear in the "Main Data" sheet, in
# admin-level order. Everything else in the sheet is a census indicator.
HIERARCHY_COLS = [
    "GEO_CODE",
    "DIV_C", "DIV_N",
    "DIST_C", "DIST_N",
    "CITY_CODE", "CITY_NAME",
    "UPZ_CO", "UPZ_NA",
    "MCPL_CODE", "MCPL_N",
    "UNION_CODE", "UNION_NAME",
    "MAUZA_CODE", "MAUZA_NAME", "MZ_Name_XL",
]

_STRING_COLS = ["DIV_N", "DIST_N", "CITY_NAME", "UPZ_NA", "MCPL_N",
                 "UNION_NAME", "MAUZA_NAME", "MZ_Name_XL"]


def _clean_str(value):
    if pd.isna(value):
        return value
    return _INVISIBLE_CHARS.sub("", str(value)).strip()


@lru_cache(maxsize=1)
def get_dataframe() -> pd.DataFrame:
    path: Path = settings.MAUZA_XLSX
    if not path.exists():
        raise FileNotFoundError(f"Mauza census workbook not found at {path}.")
    logger.info("Loading mauza census data from %s [%s]", path, settings.MAUZA_SHEET)
    df = pd.read_excel(path, sheet_name=settings.MAUZA_SHEET, engine="openpyxl")

    for col in _STRING_COLS:
        if col in df.columns:
            df[col] = df[col].map(_clean_str)

    # GEO_CODE is a 16-digit BBS code (DIV2+DIST2+CITY2+UPZ2+MCPL2+UNION3+MAUZA3).
    # Keep it as a zero-padded string — casting to int risks losing leading
    # zeros on the component fields (e.g. a "04" district segment).
    if "GEO_CODE" in df.columns:
        df["GEO_CODE"] = df["GEO_CODE"].astype("int64").astype(str).str.zfill(16)

    # The new workbook is mauza-only — one row per mauza, no pre-aggregated
    # Upazila-level rows like the old bbs.csv had. Synthesise the parent
    # container type instead, since downstream filters/consumers expect it.
    def _location_type(row):
        if pd.notna(row.get("CITY_NAME")) and str(row.get("CITY_NAME")).strip():
            return "City Corporation"
        if pd.notna(row.get("MCPL_N")) and str(row.get("MCPL_N")).strip():
            return "Paurashava"
        return "Union"

    df["Location_Type"] = df.apply(_location_type, axis=1)
    df = df.copy()  # defragment after the column insert above

    logger.info("Loaded %d mauza rows × %d columns", *df.shape)
    return df


@lru_cache(maxsize=1)
def get_indicator_dictionary() -> list[dict]:
    """Data dictionary from the 'Metadata' sheet: column code → description/table/group."""
    path: Path = settings.MAUZA_XLSX
    if not path.exists():
        raise FileNotFoundError(f"Mauza census workbook not found at {path}.")
    meta = pd.read_excel(path, sheet_name=settings.METADATA_SHEET, engine="openpyxl")
    meta = meta.rename(columns={
        "Table Name": "table",
        "Column Name": "column",
        "Description": "description",
        "Group": "group",
    })
    for col in ["table", "column", "description", "group"]:
        if col in meta.columns:
            meta[col] = meta[col].map(_clean_str)
    return safe_records(meta)


@lru_cache(maxsize=1)
def get_geojson() -> dict | None:
    path: Path = settings.BOUNDARY_GEOJSON
    if not path.exists():
        logger.warning("boundary.geojson not found at %s", path)
        return None
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def safe_records(df: pd.DataFrame) -> list[dict]:
    return df.replace({np.nan: None}).to_dict(orient="records")


def reload_cache() -> None:
    get_dataframe.cache_clear()
    get_indicator_dictionary.cache_clear()
    get_geojson.cache_clear()
