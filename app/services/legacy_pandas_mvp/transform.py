"""
idrms-backend · app/services/transform.py

Pure data-transformation functions over the mauza-level BBS PHC 2022 workbook.
Administrative hierarchy: Division → District → Upazila → Union/Paurashava → Mauza.

Design note: the old MVP's bbs.csv stored several granularities in one flat
table (Upazila-aggregate rows AND Union rows, disambiguated by a Location_Type
column) so district/national summaries could just filter to
Location_Type == "Upazila" and sum. The new workbook is mauza-only — every
row is the smallest unit — so summaries here always aggregate up from mauza
rows instead of relying on a pre-aggregated row.
"""
from __future__ import annotations
from typing import Optional
import pandas as pd
from app.services.loader import safe_records, get_indicator_dictionary, HIERARCHY_COLS


# ── Filtering ─────────────────────────────────────────────────────────────────

def _f(df: pd.DataFrame,
       district: Optional[str]      = None,
       upazila_code: Optional[int]  = None,
       union_code: Optional[int]    = None,
       mauza_code: Optional[int]    = None,
       geo_code: Optional[str]      = None,
       location_type: Optional[str] = None) -> pd.DataFrame:
    if district:
        df = df[df["DIST_N"].str.lower() == district.lower()]
    if upazila_code is not None:
        df = df[df["UPZ_CO"] == upazila_code]
    if union_code is not None:
        df = df[df["UNION_CODE"] == union_code]
    if mauza_code is not None:
        df = df[df["MAUZA_CODE"] == mauza_code]
    if geo_code:
        df = df[df["GEO_CODE"] == str(geo_code).zfill(16)]
    if location_type:
        df = df[df["Location_Type"].str.lower() == location_type.lower()]
    return df


def _select(df: pd.DataFrame, cols: list[str], rename: Optional[dict] = None) -> list[dict]:
    keep = [c for c in HIERARCHY_COLS + cols if c in df.columns]
    out = df[keep]
    if rename:
        out = out.rename(columns=rename)
    return safe_records(out)


# ── Catalogue ─────────────────────────────────────────────────────────────────

def build_divisions(df: pd.DataFrame) -> list[dict]:
    rows = (df[["DIV_C", "DIV_N"]].drop_duplicates().sort_values("DIV_N"))
    return safe_records(rows)


def build_districts(df: pd.DataFrame) -> list[dict]:
    rows = (df[["DIV_C", "DIV_N", "DIST_C", "DIST_N"]]
            .drop_duplicates().sort_values("DIST_N"))
    return safe_records(rows)


def build_upazilas(df: pd.DataFrame, district: Optional[str] = None) -> list[dict]:
    src = df if not district else df[df["DIST_N"].str.lower() == district.lower()]
    return safe_records(src[["DIST_N", "UPZ_CO", "UPZ_NA"]]
                        .drop_duplicates().sort_values("UPZ_NA"))


def build_unions(df: pd.DataFrame,
                 district: Optional[str]      = None,
                 upazila_code: Optional[int]  = None) -> list[dict]:
    """Union / Paurashava-ward catalogue (the mauza's immediate parent)."""
    src = _f(df, district=district, upazila_code=upazila_code)
    return safe_records(
        src[["DIST_N", "UPZ_CO", "UPZ_NA", "UNION_CODE", "UNION_NAME",
             "MCPL_N", "Location_Type"]]
        .drop_duplicates().sort_values(["UPZ_NA", "UNION_NAME"])
    )


def build_mauzas(df: pd.DataFrame,
                 district: Optional[str]      = None,
                 upazila_code: Optional[int]  = None,
                 union_code: Optional[int]    = None) -> list[dict]:
    src = _f(df, district=district, upazila_code=upazila_code, union_code=union_code)
    return safe_records(
        src[["GEO_CODE", "DIST_N", "UPZ_NA", "UNION_NAME", "MCPL_N",
             "MAUZA_CODE", "MAUZA_NAME", "Location_Type"]]
        .sort_values("MAUZA_NAME")
    )


# ── Metadata ──────────────────────────────────────────────────────────────────

def build_meta(df: pd.DataFrame) -> dict:
    dictionary = get_indicator_dictionary()
    tables = sorted({d["table"] for d in dictionary})
    return {
        "hierarchy":       ["Division", "District", "Upazila", "Union/Paurashava", "Mauza"],
        "location_types":  sorted(df["Location_Type"].dropna().unique().tolist()),
        "districts":       sorted(df["DIST_N"].dropna().unique().tolist()),
        "tables":          tables,
        "indicator_count": len(dictionary),
        "total_mauzas":    len(df),
    }


def build_dictionary(table: Optional[str] = None) -> list[dict]:
    """Full BBS indicator data dictionary, optionally scoped to one table (e.g. 'C05')."""
    rows = get_indicator_dictionary()
    if table:
        rows = [r for r in rows if r["table"].lower() == table.lower()]
    return rows


# ── Generic table access (metadata-driven) ─────────────────────────────────────

def build_table(df: pd.DataFrame, table: str,
                district: Optional[str]      = None,
                upazila_code: Optional[int]  = None,
                union_code: Optional[int]    = None,
                mauza_code: Optional[int]    = None,
                location_type: Optional[str] = None) -> list[dict]:
    """Row-level (mauza granularity) view of any BBS table C01–C18, columns
    resolved from the data dictionary rather than hard-coded."""
    cols = [r["column"] for r in get_indicator_dictionary() if r["table"].lower() == table.lower()]
    src = _f(df, district, upazila_code, union_code, mauza_code, location_type=location_type)
    return _select(src, cols)


# ── Summary (aggregated up from mauza rows) ────────────────────────────────────

def _weighted_avg(df: pd.DataFrame, value_col: str, weight_col: str) -> Optional[float]:
    w = df[weight_col].sum()
    if not w:
        return None
    return round(float((df[value_col] * df[weight_col]).sum() / w), 2)


def _summary_row(src: pd.DataFrame) -> dict:
    male = int(src["C01MPOP"].sum())
    fem  = int(src["C01FPOP"].sum())
    hh   = int(src["C01HHTOT"].sum())
    return {
        "total_population":   int(src["C01TOTPOP"].sum()),
        "total_male":         male,
        "total_female":       fem,
        "total_hijra":        int(src["C01HPOP"].sum()),
        "total_households":   hh,
        # weighted by each mauza's household count, rather than a naive mean
        # across mauzas of very different sizes
        "avg_household_size": _weighted_avg(src, "C18HHAVG", "C01HHTOT"),
        "overall_sex_ratio":  round(male / fem * 100, 2) if fem else None,
        "literacy_rate_15plus": _weighted_avg(src, "C05LR15AT", "C01TOTPOP"),
        "electricity_coverage_pct": _weighted_avg(src, "C17ELECP", "C01HHTOT"),
        "mauza_count":        int(len(src)),
    }


def build_national_summary(df: pd.DataFrame) -> dict:
    out = _summary_row(df)
    out["district_count"] = int(df["DIST_N"].nunique())
    out["upazila_count"]  = int(df["UPZ_CO"].nunique())
    return out


def build_district_summaries(df: pd.DataFrame) -> list[dict]:
    rows = []
    for district, grp in df.groupby("DIST_N"):
        row = _summary_row(grp)
        row["district"] = district
        row["upazila_count"] = int(grp["UPZ_CO"].nunique())
        rows.append(row)
    rows.sort(key=lambda r: r["district"])
    return rows


def build_district_detail(df: pd.DataFrame, district: str) -> dict:
    src = _f(df, district=district)
    if src.empty:
        return {}
    out = _summary_row(src)
    out["district"] = district
    out["upazila_count"] = int(src["UPZ_CO"].nunique())
    return out


# ── Population (C01) + age pyramid (C02) ───────────────────────────────────────

_POP_ALIAS = {
    "C01TOTPOP": "Pop_Total", "C01MPOP": "Pop_Male", "C01FPOP": "Pop_Female",
    "C01HPOP": "Pop_Hijra", "C01SXRATIO": "Sex_Ratio",
    "C01HHTOT": "HH_Total", "C01HHGEN": "HH_General", "C01HHINST": "HH_Institutional",
    "C18HHAVG": "Avg_HH_Size",
}

def build_population(df: pd.DataFrame,
                     district: Optional[str]      = None,
                     upazila_code: Optional[int]  = None,
                     union_code: Optional[int]    = None,
                     mauza_code: Optional[int]    = None,
                     location_type: Optional[str] = None) -> list[dict]:
    src = _f(df, district, upazila_code, union_code, mauza_code, location_type=location_type)
    cols = ["C01TOTPOP", "C01MPOP", "C01FPOP", "C01HPOP", "C01SXRATIO",
            "C01HHTOT", "C01HHGEN", "C01HHINST", "C18HHAVG"]
    return _select(src, cols, rename=_POP_ALIAS)


_AGE_COLS = ["C02AG04", "C02AG59", "C02AG1014", "C02AG1519", "C02AG2024",
             "C02AG2529", "C02AG3034", "C02AG3539", "C02AG4044", "C02AG4549",
             "C02AG5054", "C02AG5559", "C02AG6064", "C02AG6569", "C02AG7074",
             "C02AG7579", "C02AG80PLS"]

_AGE_LABELS = {
    "C02AG04": "0-4", "C02AG59": "5-9", "C02AG1014": "10-14", "C02AG1519": "15-19",
    "C02AG2024": "20-24", "C02AG2529": "25-29", "C02AG3034": "30-34", "C02AG3539": "35-39",
    "C02AG4044": "40-44", "C02AG4549": "45-49", "C02AG5054": "50-54", "C02AG5559": "55-59",
    "C02AG6064": "60-64", "C02AG6569": "65-69", "C02AG7074": "70-74", "C02AG7579": "75-79",
    "C02AG80PLS": "80+",
}

def build_age_pyramid(df: pd.DataFrame,
                      district: Optional[str]      = None,
                      upazila_code: Optional[int]  = None,
                      union_code: Optional[int]    = None,
                      mauza_code: Optional[int]    = None,
                      location_type: Optional[str] = None) -> list[dict]:
    src       = _f(df, district, upazila_code, union_code, mauza_code, location_type=location_type)
    totals    = src[_AGE_COLS].sum()
    total_pop = totals.sum()
    return [
        {
            "age_group": _AGE_LABELS[c],
            "count":     int(totals[c]),
            "pct":       round(float(totals[c] / total_pop * 100), 2) if total_pop else 0,
        }
        for c in _AGE_COLS
    ]


# ── Religion (C03) ──────────────────────────────────────────────────────────────

_REL_ALIAS = {
    "C03TOTREL": "Religion_Total", "C03MUSLIM": "Muslim", "C03HINDU": "Hindu",
    "C03CHRISTN": "Christian", "C03BUDHIST": "Buddhist", "C03OTHREL": "Religion_Others",
}

def build_religion(df: pd.DataFrame,
                   district: Optional[str]      = None,
                   upazila_code: Optional[int]  = None,
                   union_code: Optional[int]    = None,
                   mauza_code: Optional[int]    = None,
                   location_type: Optional[str] = None) -> list[dict]:
    src = _f(df, district, upazila_code, union_code, mauza_code, location_type=location_type)
    return _select(src, list(_REL_ALIAS.keys()), rename=_REL_ALIAS)


# ── Marital status (C04) — NEW, no equivalent in the old MVP ────────────────────

def build_marital_status(df: pd.DataFrame,
                         district: Optional[str]      = None,
                         upazila_code: Optional[int]  = None,
                         union_code: Optional[int]    = None,
                         mauza_code: Optional[int]    = None,
                         location_type: Optional[str] = None) -> list[dict]:
    """Percent never-married / currently-married / widowed / divorced / separated,
    population aged 10+, by sex. Values are BBS percentages, not counts."""
    src = _f(df, district, upazila_code, union_code, mauza_code, location_type=location_type)
    cols = [r["column"] for r in get_indicator_dictionary() if r["table"] == "C04"]
    return _select(src, cols)


# ── Literacy (C05) ──────────────────────────────────────────────────────────────

_LIT_COLS = ["C05LR5AT", "C05LR5AM", "C05LR5AF", "C05LR7AT", "C05LR7AM", "C05LR7AF",
             "C05LR15AT", "C05LR15AM", "C05LR15AF"]
_LIT_ALIAS = {
    "C05LR5AT": "Literacy_5Plus_Total", "C05LR5AM": "Literacy_5Plus_Male", "C05LR5AF": "Literacy_5Plus_Female",
    "C05LR7AT": "Literacy_7Plus_Total", "C05LR7AM": "Literacy_7Plus_Male", "C05LR7AF": "Literacy_7Plus_Female",
    "C05LR15AT": "Literacy_15Plus_Total", "C05LR15AM": "Literacy_15Plus_Male", "C05LR15AF": "Literacy_15Plus_Female",
}

def build_literacy(df: pd.DataFrame,
                   district: Optional[str]      = None,
                   upazila_code: Optional[int]  = None,
                   union_code: Optional[int]    = None,
                   mauza_code: Optional[int]    = None,
                   location_type: Optional[str] = None) -> list[dict]:
    """Literacy rates (%), not counts."""
    src = _f(df, district, upazila_code, union_code, mauza_code, location_type=location_type)
    return _select(src, _LIT_COLS, rename=_LIT_ALIAS)


# ── Education levels (C06) ───────────────────────────────────────────────────────

_EDU_MAP = {
    "Pre-Primary": "C06EDUPLY",
    "Class I":     "C06EDUCL1",
    "Class 1-5":   "C06EDUCL15",
    "Class 6-9":   "C06EDUCL69",
    "SSC":         "C06EDUSSC",
    "HSC":         "C06EDUHSC",
    "Diploma":     "C06EDUDIP",
    "Graduate+":   "C06EDUGRAD",
    "Non-Formal":  "C06EDUNONO",
}

def build_education_levels(df: pd.DataFrame,
                           district: Optional[str]      = None,
                           upazila_code: Optional[int]  = None,
                           union_code: Optional[int]    = None,
                           mauza_code: Optional[int]    = None,
                           location_type: Optional[str] = None) -> list[dict]:
    src = _f(df, district, upazila_code, union_code, mauza_code, location_type=location_type)
    return [{"level": lbl, "count": int(src[col].sum())} for lbl, col in _EDU_MAP.items()]


# ── Students (C07) — NEW ─────────────────────────────────────────────────────────

def build_students(df: pd.DataFrame,
                   district: Optional[str]      = None,
                   upazila_code: Optional[int]  = None,
                   union_code: Optional[int]    = None,
                   mauza_code: Optional[int]    = None,
                   location_type: Optional[str] = None) -> list[dict]:
    """Currently-enrolled students aged 5-29, by 5-year age band and sex. Counts."""
    src = _f(df, district, upazila_code, union_code, mauza_code, location_type=location_type)
    cols = [r["column"] for r in get_indicator_dictionary() if r["table"] == "C07"]
    return _select(src, cols)


# ── Labour: working status (C08) + employment by sector (C09) ──────────────────

_LAB_COLS = ["C08WSPOPT", "C08WSPOPM", "C08WSPOPF",
             "C09EMPPOPT", "C09EMPPOPM", "C09EMPPOPF",
             "C09EMPAGRT", "C09EMPAGRM", "C09EMPAGRF",
             "C09EMPINDT", "C09EMPINDM", "C09EMPINDF",
             "C09EMPSRVT", "C09EMPSRVM", "C09EMPSRVF",
             "C08WSLFWT", "C08WSLFWM", "C08WSLFWF"]
_LAB_ALIAS = {
    "C08WSPOPT": "Labor_Pop_Total", "C08WSPOPM": "Labor_Pop_Male", "C08WSPOPF": "Labor_Pop_Female",
    "C09EMPPOPT": "Employed_Total", "C09EMPPOPM": "Employed_Male", "C09EMPPOPF": "Employed_Female",
    "C09EMPAGRT": "Agri_Total", "C09EMPAGRM": "Agri_Male", "C09EMPAGRF": "Agri_Female",
    "C09EMPINDT": "Industry_Total", "C09EMPINDM": "Industry_Male", "C09EMPINDF": "Industry_Female",
    "C09EMPSRVT": "Service_Total", "C09EMPSRVM": "Service_Male", "C09EMPSRVF": "Service_Female",
    "C08WSLFWT": "LookingForWork_Total", "C08WSLFWM": "LookingForWork_Male", "C08WSLFWF": "LookingForWork_Female",
}

def build_labour(df: pd.DataFrame,
                 district: Optional[str]      = None,
                 upazila_code: Optional[int]  = None,
                 union_code: Optional[int]    = None,
                 mauza_code: Optional[int]    = None,
                 location_type: Optional[str] = None) -> list[dict]:
    """Counts (not percentages)."""
    src = _f(df, district, upazila_code, union_code, mauza_code, location_type=location_type)
    return _select(src, _LAB_COLS, rename=_LAB_ALIAS)


# ── NEET (C10) — NEW ──────────────────────────────────────────────────────────────

def build_neet(df: pd.DataFrame,
               district: Optional[str]      = None,
               upazila_code: Optional[int]  = None,
               union_code: Optional[int]    = None,
               mauza_code: Optional[int]    = None,
               location_type: Optional[str] = None) -> list[dict]:
    """Not in Education, Employment or Training, aged 15-24 (%), by sex and age band."""
    src = _f(df, district, upazila_code, union_code, mauza_code, location_type=location_type)
    cols = [r["column"] for r in get_indicator_dictionary() if r["table"] == "C10"]
    return _select(src, cols)


# ── Digital: mobile + internet (C11) ─────────────────────────────────────────────

_DIG_COLS = ["C11MOB5TP", "C11MOB5MP", "C11MOB5FP", "C11MOB15TP", "C11MOB15MP", "C11MOB15FP",
             "C11INT5TP", "C11INT5MP", "C11INT5FP", "C11INT15TP", "C11INT15MP", "C11INT15FP"]
_DIG_ALIAS = {
    "C11MOB5TP": "Mobile_5Plus_Total_Pct", "C11MOB5MP": "Mobile_5Plus_Male_Pct", "C11MOB5FP": "Mobile_5Plus_Female_Pct",
    "C11MOB15TP": "Mobile_15Plus_Total_Pct", "C11MOB15MP": "Mobile_15Plus_Male_Pct", "C11MOB15FP": "Mobile_15Plus_Female_Pct",
    "C11INT5TP": "Internet_5Plus_Total_Pct", "C11INT5MP": "Internet_5Plus_Male_Pct", "C11INT5FP": "Internet_5Plus_Female_Pct",
    "C11INT15TP": "Internet_15Plus_Total_Pct", "C11INT15MP": "Internet_15Plus_Male_Pct", "C11INT15FP": "Internet_15Plus_Female_Pct",
}

def build_digital(df: pd.DataFrame,
                  district: Optional[str]      = None,
                  upazila_code: Optional[int]  = None,
                  union_code: Optional[int]    = None,
                  mauza_code: Optional[int]    = None,
                  location_type: Optional[str] = None) -> list[dict]:
    """Percentages, not counts."""
    src = _f(df, district, upazila_code, union_code, mauza_code, location_type=location_type)
    return _select(src, _DIG_COLS, rename=_DIG_ALIAS)


# ── Financial inclusion (C12) — NEW ──────────────────────────────────────────────

def build_financial_inclusion(df: pd.DataFrame,
                              district: Optional[str]      = None,
                              upazila_code: Optional[int]  = None,
                              union_code: Optional[int]    = None,
                              mauza_code: Optional[int]    = None,
                              location_type: Optional[str] = None) -> list[dict]:
    """Bank/financial-institution account + mobile banking, pop 15+ (%), by sex."""
    src = _f(df, district, upazila_code, union_code, mauza_code, location_type=location_type)
    cols = [r["column"] for r in get_indicator_dictionary() if r["table"] == "C12"]
    return _select(src, cols)


# ── Housing structure (C14) ──────────────────────────────────────────────────────

_STRUCT_COLS = ["C14HHT", "C14HHPUCP", "C14HHSPUCP", "C14HHKANP", "C14HHJHUP",
                "C14HHOWNP", "C14HHRTODP", "C14HHRTNOP", "C14HHRFOWP", "C14HHRFNOP"]
_STRUCT_ALIAS = {
    "C14HHT": "Structure_Total_HH", "C14HHPUCP": "Structure_Pucca_Pct",
    "C14HHSPUCP": "Structure_SemiPucca_Pct", "C14HHKANP": "Structure_Kancha_Pct",
    "C14HHJHUP": "Structure_Jhupri_Pct", "C14HHOWNP": "Tenure_Own_Pct",
    "C14HHRTODP": "Tenure_RentedOwnElsewhere_Pct", "C14HHRTNOP": "Tenure_RentedNoOwnElsewhere_Pct",
    "C14HHRFOWP": "Tenure_RentFreeOwnElsewhere_Pct", "C14HHRFNOP": "Tenure_RentFreeNoOwnElsewhere_Pct",
}

def build_housing_structure(df: pd.DataFrame,
                            district: Optional[str]      = None,
                            upazila_code: Optional[int]  = None,
                            union_code: Optional[int]    = None,
                            mauza_code: Optional[int]    = None,
                            location_type: Optional[str] = None) -> list[dict]:
    """*_Pct fields are % of households; Structure_Total_HH is the raw household count."""
    src = _f(df, district, upazila_code, union_code, mauza_code, location_type=location_type)
    return _select(src, _STRUCT_COLS, rename=_STRUCT_ALIAS)


# ── Drinking water (C15) ─────────────────────────────────────────────────────────

_WATER_COLS = ["C15DWT", "C15DWTAPP", "C15DWTUBEP", "C15DWBOTP", "C15DWWELLP",
               "C15DWPONDP", "C15DWSPRP", "C15DWRAINP", "C15DWOTHP"]
_WATER_ALIAS = {
    "C15DWT": "Water_Total_HH", "C15DWTAPP": "Water_TapPipe_Pct", "C15DWTUBEP": "Water_TubeWell_Pct",
    "C15DWBOTP": "Water_BottledJar_Pct", "C15DWWELLP": "Water_Well_Pct",
    "C15DWPONDP": "Water_PondRiverCanal_Pct", "C15DWSPRP": "Water_Spring_Pct",
    "C15DWRAINP": "Water_Rainwater_Pct", "C15DWOTHP": "Water_Other_Pct",
}

def build_water(df: pd.DataFrame,
                district: Optional[str]      = None,
                upazila_code: Optional[int]  = None,
                union_code: Optional[int]    = None,
                mauza_code: Optional[int]    = None,
                location_type: Optional[str] = None) -> list[dict]:
    """*_Pct fields are % of households; Water_Total_HH is the raw household count."""
    src = _f(df, district, upazila_code, union_code, mauza_code, location_type=location_type)
    return _select(src, _WATER_COLS, rename=_WATER_ALIAS)


# ── Toilet facilities (C16) ──────────────────────────────────────────────────────

_TOILET_COLS = ["C16TOILT", "C16TLSAFEP", "C16TOILUD", "C16TLPITSP", "C16TLPITOP",
                 "C16TLHANGP", "C16TLOPENP"]
_TOILET_ALIAS = {
    "C16TOILT": "Toilet_Total_HH", "C16TLSAFEP": "Toilet_SafeFlush_Pct",
    "C16TOILUD": "Toilet_UnsafeFlush_Pct", "C16TLPITSP": "Toilet_PitWithSlab_Pct",
    "C16TLPITOP": "Toilet_PitNoSlab_Pct", "C16TLHANGP": "Toilet_OpenHanging_Pct",
    "C16TLOPENP": "Toilet_OpenDefecation_Pct",
}

def build_toilet(df: pd.DataFrame,
                 district: Optional[str]      = None,
                 upazila_code: Optional[int]  = None,
                 union_code: Optional[int]    = None,
                 mauza_code: Optional[int]    = None,
                 location_type: Optional[str] = None) -> list[dict]:
    """*_Pct fields are % of households; Toilet_Total_HH is the raw household count."""
    src = _f(df, district, upazila_code, union_code, mauza_code, location_type=location_type)
    return _select(src, _TOILET_COLS, rename=_TOILET_ALIAS)


# ── Cooking fuel (C17, minus electricity) — NEW ──────────────────────────────────

def build_cooking_fuel(df: pd.DataFrame,
                       district: Optional[str]      = None,
                       upazila_code: Optional[int]  = None,
                       union_code: Optional[int]    = None,
                       mauza_code: Optional[int]    = None,
                       location_type: Optional[str] = None) -> list[dict]:
    """% of households by main cooking fuel."""
    src = _f(df, district, upazila_code, union_code, mauza_code, location_type=location_type)
    cols = [r["column"] for r in get_indicator_dictionary()
            if r["table"] == "C17" and r["column"] != "C17ELECP"]
    return _select(src, cols)


def build_electricity(df: pd.DataFrame,
                      district: Optional[str]      = None,
                      upazila_code: Optional[int]  = None,
                      union_code: Optional[int]    = None,
                      mauza_code: Optional[int]    = None,
                      location_type: Optional[str] = None) -> list[dict]:
    src = _f(df, district, upazila_code, union_code, mauza_code, location_type=location_type)
    return _select(src, ["C17ELECP"], rename={"C17ELECP": "Electricity_Coverage_Pct"})


# ── Household size distribution (C18) — NEW ──────────────────────────────────────

def build_household_size(df: pd.DataFrame,
                         district: Optional[str]      = None,
                         upazila_code: Optional[int]  = None,
                         union_code: Optional[int]    = None,
                         mauza_code: Optional[int]    = None,
                         location_type: Optional[str] = None) -> list[dict]:
    src = _f(df, district, upazila_code, union_code, mauza_code, location_type=location_type)
    cols = [r["column"] for r in get_indicator_dictionary() if r["table"] == "C18"]
    return _select(src, cols)


# ── Raw / flexible ────────────────────────────────────────────────────────────

def build_raw(df: pd.DataFrame,
              district: Optional[str]      = None,
              upazila_code: Optional[int]  = None,
              union_code: Optional[int]    = None,
              mauza_code: Optional[int]    = None,
              location_type: Optional[str] = None,
              columns: Optional[str]       = None) -> list[dict]:
    src = _f(df, district, upazila_code, union_code, mauza_code, location_type=location_type)
    if columns:
        valid = [c.strip() for c in columns.split(",") if c.strip() in src.columns]
        src = src[valid]
    return safe_records(src)
