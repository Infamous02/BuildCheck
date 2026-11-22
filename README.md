# BuildCheck

PC build configurator with a rules-driven hardware compatibility engine (CIT 347 capstone,
for Twin Creek Digital's custom PC build service).

| Folder | What it is |
|---|---|
| `ingest/` | Module 4 extraction: manufacturer PDF → staged JSON (`data/staged/`) |
| `db/` | Postgres schema, seed CSVs and loaders |
| `engine/` | Rules engine (17 rules), technician checklist, CLI |
| `engine/buildcheck.py` + `parts.json` | Module 2 prototype (JSON-based, kept for reference) |

## Setup

1. Install PostgreSQL and create a database:
   ```
   createdb buildcheck
   ```
2. Install Python packages:
   ```
   py -m pip install -r requirements.txt
   ```
   If importing psycopg fails with *"An Application Control policy has blocked this file"*
   (Windows Smart App Control blocking the `psycopg[binary]` DLL), either use a Python 3.12
   virtual environment, or install plain `psycopg` and add PostgreSQL's `bin` folder (which has `libpq.dll`) to `PATH`.
3. Create a `.env` file in the repo root. It's git-ignored.
   ```
   DATABASE_URL=postgresql://postgres:yourpassword@localhost:5432/buildcheck
   ```
4. Create the tables and load the seed data. This **drops and recreates every table**:
   ```
   py -m db.load_seed
   ```
   It prints a row count for each table.

### Seed data

`db/seed/*.csv` holds one CSV per table. Column names match `db/schema.sql`, and array columns use
Postgres syntax, e.g. `"{ATX,mATX,ITX}"`. Header-only templates are in `db/seed/templates/`.

The current rows are **placeholder sample data** (`brand = Sample`, `source_url = SAMPLE`). They
exist so the engine and tests have something to run against. The values aren't real product specs.
Replace them with verified manufacturer data and put the real `source_url` on each row.

The loader normalizes values as it loads: sockets become `LGA1851`/`AM5`, memory type becomes
`DDR4`/`DDR5`, speeds become integer MT/s (`DDR5-6400`, `6400MHz` and `6400 MT/s` all load as
`6400`) and form factors become `ATX`/`mATX`/`ITX`/`E-ATX`.

## Loading extracted data

```
py -m ingest.extract data/parsed/z890.json        # Module 4: writes data/staged/z890.json
py -m db.load_extracted                           # loads data/staged/*.json as needs_review
py -m engine.cli list motherboards                # find the new id
py -m engine.cli review --table motherboards --id 6 --approve    # or --reject
```

Extracted records always come in as `needs_review`, and checks only use `approved` rows. The
extractor doesn't read BIOS data, so fill in `shipped_bios`, `bios_flashback` and the board's
CPU support/QVL rows before approving. A rule that hits a missing value returns WARN instead of crashing.

## Checking a build

```
py -m engine.cli check --build 1                  # a saved build from the builds table
py -m engine.cli check --cpu 1 --board 1 --kit 1 --kit-qty 1 --gpu 1 --case 1 --psu 2 --cooler 1
```

`--gpu` and `--cooler` are optional. Leave them out for an iGPU-only build or a stock cooler. The
output lists every rule (ID, name, PASS/WARN/FAIL/SKIP, message), then the overall status and the
technician checklist. The command exits with code 1 when the overall status is FAIL.

Sample builds: 1 = PASS, 2 and 3 = FAIL, 4 = WARN.

## Tests

```
py -m pytest -v
```

The rules tests use in-memory fixtures (`tests/conftest.py`), so they don't need a database. To also
run the integration test, point `TEST_DATABASE_URL` at a **throwaway** database, because the test drops and reloads every table:

```
set TEST_DATABASE_URL=postgresql://postgres:yourpassword@localhost:5432/buildcheck_test
py -m pytest -v
```
