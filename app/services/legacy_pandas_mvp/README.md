# Legacy: pandas/xlsx in-memory MVP

`loader.py` and `transform.py` here are the pre-Postgres data layer — they
read the mauza xlsx straight into a pandas DataFrame and served every request
from memory. They worked fine for the 99-row Barguna sample, but at 60,000+
rows the design breaks down in a few ways:

- `pd.read_excel()` via openpyxl gets slow and memory-heavy well before
  national scale, and every `--reload` in development re-parses the whole
  workbook.
- No indexing — every filter is a full in-memory pandas scan. Fine at 99
  rows, still fine at 61k (~tested, low milliseconds), but doesn't get you
  the sub-millisecond geo_code-prefix lookups or GIN-indexed name search
  Postgres gives for free.
- No natural place to join in the mauza boundary geometries once the
  shapefile arrives — PostGIS is built for exactly that.

They're kept here (not deleted) because the column-aliasing and grouping
logic was already worked out and cross-checked against the real BBS
workbook — useful reference if the SQL layer in `repository.py` ever needs
to be cross-validated, or if a future contributor wants a pure-Python
fallback path that doesn't need a database running.

Superseded by:
- `app/database.py` + `app/services/repository.py` (live queries)
- `scripts/ingest_mauza_census.py` (loads the xlsx into Postgres once)
