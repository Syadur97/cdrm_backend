"""
idrms-backend · config.py
Centralised application settings loaded from environment variables / .env file.
"""

from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


BASE_DIR = Path(__file__).resolve().parent          # app/
DATA_DIR = BASE_DIR / "data"


class Settings(BaseSettings):
    # ── API metadata ────────────────────────────────────────────
    APP_TITLE: str = "IDRMS — Integrated Disaster Risk Management System"
    APP_VERSION: str = "2.0.0"
    APP_DESCRIPTION: str = (
        "RESTful API serving Mauza-level Population & Housing Census 2022 data "
        "(Bangladesh Bureau of Statistics) with full Division → District → "
        "Upazila → Union/Paurashava → Mauza administrative hierarchy."
    )

    # ── Data files ───────────────────────────────────────────────
    # MAUZA_XLSX supersedes the old upazila/union-level BBS_CSV MVP file.
    # "Main Data" sheet = one row per mauza; "Metadata" sheet = data dictionary
    # (column code → description → BBS table/group), which the loader turns
    # into the indicator dictionary served at /api/meta/dictionary.
    MAUZA_XLSX: Path = DATA_DIR / "mauza_census.xlsx"
    MAUZA_SHEET: str = "Main Data"
    METADATA_SHEET: str = "Metadata"

    # Mauza-level boundary polygons, keyed by GEO_CODE (16-digit BBS geocode).
    # Drop a GeoJSON export of the matching shapefile here — /api/boundary
    # returns 404 until this file is present. If you only have a .shp, convert
    # first: `ogr2ogr -f GeoJSON boundary.geojson your_file.shp`
    BOUNDARY_GEOJSON: Path = DATA_DIR / "boundary.geojson"

    # Legacy MVP file (district/upazila/union level), kept only for reference
    # during the cutover — no longer loaded by default.
    LEGACY_BBS_CSV: Path = DATA_DIR / "bbs.csv"

    # ── Database (PostgreSQL + PostGIS) ─────────────────────────
    # At 60,000+ mauza rows the in-memory pandas/xlsx MVP is retired in favour
    # of Postgres: asyncpg for the live API, psycopg2 for the sync ingestion
    # script. Override these via .env — defaults match the local dev instance.
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_DB: str = "idrms"
    POSTGRES_USER: str = "postgres"
    POSTGRES_PASSWORD: str = "postgres"

    # "disable" for local Docker Postgres. Managed providers that require TLS
    # on every connection (Neon, Render's managed Postgres, etc.) need this
    # set to "require" — asyncpg and psycopg2 both accept sslmode-style
    # values natively, no extra SSL context wiring needed either way.
    POSTGRES_SSLMODE: str = "disable"

    @property
    def ASYNC_DATABASE_URL(self) -> str:
        url = (f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
               f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}")
        return url if self.POSTGRES_SSLMODE == "disable" else f"{url}?ssl={self.POSTGRES_SSLMODE}"

    @property
    def SYNC_DATABASE_URL(self) -> str:
        url = (f"postgresql://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
               f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}")
        return url if self.POSTGRES_SSLMODE == "disable" else f"{url}?sslmode={self.POSTGRES_SSLMODE}"

    # ── CORS ─────────────────────────────────────────────────────
    # No trailing slash on origins — CORS matching is exact.
    # The wildcard "*.app.github.dev" is NOT supported by browsers;
    # list the exact Codespaces subdomain instead.
    CORS_ORIGINS: list[str] = [
        "http://localhost:3000",
        "http://localhost:3001",
        "http://127.0.0.1:3000",
        # Codespaces frontend (port 3000) — no trailing slash
        "https://scaling-rotary-phone-56pj7w6gpw7c7xg7-3000.app.github.dev",
    ]

    # ── Misc ─────────────────────────────────────────────────────
    DEBUG: bool = False

    model_config = SettingsConfigDict(
        env_file=BASE_DIR.parent / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


settings = Settings()
