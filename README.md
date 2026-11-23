# School Risk Tracker (FastAPI)

An API to track natural and technological risks for the **70 collèges in Loiret**.
It also tracks the follow-up actions taken for each school.

| Data file | Content |
|---|---|
| `data/colleges_risques.parquet` | 1 row per school: clay soils, flood plan (PPRI), cavities, ICPE/Seveso sites, hazardous-materials transport (TMD), nuclear plant, CatNat declarations, DICRIM year |
| `data/colleges.parquet` | 10 school years (2016 → 2025): enrolment, IPS, DNB results |
| `data/colleges_secteurs.geojson` | 59 catchment areas (sectors) as polygons |

## Run it

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]" -c requirements.lock   # app + pyarrow + pytest + ruff, tested versions
uvicorn app.main:app --reload
```

Without `SRT_DATABASE_URL` the app uses SQLite (`tracker.db`) and reads the data files directly: fine for a quick look and for the tests.

### With Postgres + PostGIS (recommended)

```bash
docker compose up -d db                     # PostGIS 17 on 127.0.0.1:5433
echo 'SRT_DATABASE_URL=postgresql+psycopg://srt:srt-dev-password@127.0.0.1:5433/school_risks' >> .env
alembic upgrade head                        # create / update the schema
python -m app.manage load-reference         # data/*.parquet + sectors -> PostGIS
python -m app.manage copy-sqlite            # optional: users, actions, audit log, snapshots from tracker.db
uvicorn app.main:app --reload
```

On Postgres the schema belongs to Alembic: after pulling code with a new migration, run `alembic upgrade head` (the API refuses to start on an outdated schema; `python -m app.manage check` shows the revisions).
To change a model, edit it and run `alembic revision --autogenerate -m "what changed"`, then review the generated file.

Open http://127.0.0.1:8000/docs for the interactive Swagger UI.
Open http://127.0.0.1:8000/ for the risk dashboard and sector map.
To run the tests: `pytest -q`. The PostGIS tests run only when `SRT_TEST_DATABASE_URL` points to a disposable database (it is wiped):

```bash
psql -h 127.0.0.1 -p 5433 -U srt school_risks -c "CREATE DATABASE school_risks_test"
SRT_TEST_DATABASE_URL=postgresql+psycopg://srt:srt-dev-password@127.0.0.1:5433/school_risks_test pytest -q
```

## Deployment (Docker)

```bash
cp .env.example .env        # set SRT_JWT_SECRET_KEY, SRT_DB_PASSWORD, and a first admin
docker compose up -d --build
```

| Service | Role |
|---|---|
| `db` | PostGIS 17 (data in the `pgdata` volume) |
| `redis` | Rate-limit counters, shared by all API workers |
| `migrate` | One-shot: `alembic upgrade head`, then loads the reference data shipped in the image. The API starts only if it succeeds |
| `api` | uvicorn, `WEB_CONCURRENCY` workers (default 2), non-root, read-only filesystem, published on `127.0.0.1:8000` |
| `backup` | `pg_dump` at startup and then daily into `./backups`, keeps 14 days |

The API port is bound to `127.0.0.1`: put a reverse proxy with TLS in front (Caddy, nginx, Traefik), add the public URL to `SRT_CORS_ORIGINS`, and the proxy's IP to `SRT_TRUSTED_PROXIES` so rate limits and the audit log see real client IPs.
Image tag and `/health` version: `BUILD_VERSION=$(git describe --always) docker compose up -d --build`.
To refresh the reference data: run the pipeline on a workstation, commit the new `data/*.parquet`, rebuild and redeploy (`migrate` reloads them).
Yearly snapshot inside the stack: `docker compose run --rm api python -m app.pipeline snapshot`.

## Deploying to Vercel

Vercel runs the API as a serverless Python function (`[tool.vercel]` in `pyproject.toml`, `vercel.json`).
There is no persistent disk there, so it needs an external **Postgres with PostGIS** (Neon, Supabase, …), and the Docker services (`backup`, `redis`, `migrate`) do not exist.

**One-time setup**
1. Create the database, for example with the Neon integration in the Vercel dashboard (it sets `DATABASE_URL`; Neon supports PostGIS).
2. Optional, from your machine, to bring your existing users, actions and audit log
   (use the database's direct, non-pooled URL):
   ```bash
   export SRT_DATABASE_URL='postgresql://…'   # postgres:// URLs are accepted as is
   alembic upgrade head
   python -m app.manage copy-sqlite           # from tracker.db
   ```
3. Project environment variables in Vercel: `SRT_JWT_SECRET_KEY`, `SRT_BOOTSTRAP_ADMIN_USERNAME` / `SRT_BOOTSTRAP_ADMIN_PASSWORD`, and `DATABASE_URL` (the pooled URL is fine). Recommended: `SRT_RATE_LIMIT_STORAGE_URI=rediss://…` (Upstash Redis), otherwise each function instance counts requests on its own.
4. Push: Vercel installs `pyproject.toml` (without the `parquet` and `dev` extras: ~285 MB of the 500 MB limit), then runs `python -m app.manage deploy-check`. That runs `alembic upgrade head` and, when the reference tables are empty (new database, or a fresh Neon branch for a preview), loads `data/*.parquet` into them (with fastparquet, as pyarrow is not installed there).

**What the app does by itself on Vercel** (`VERCEL=1`; any value you set explicitly wins):
- refuses to start on SQLite (read-only filesystem);
- trusts `X-Forwarded-For` (`SRT_TRUSTED_PROXIES=["*"]`): Vercel's edge overwrites it with the real client IP;
- logs JSON, and `/health` reports the commit (`VERCEL_GIT_COMMIT_SHA`);
- connects without server-side prepared statements, so a transaction-mode pooler (Neon `-pooler` host, PgBouncer) works.

**Preview deployments** run the same build step. With the Neon integration each preview gets its own database branch, which the build migrates and fills. If previews share the production database, set `SRT_MIGRATE_ON_BUILD=0` for the Preview environment: the build then never writes to the database.
Only the **production branch** set in Vercel (Settings → Git) deploys to production: pushes to any other branch are previews.
**Data refresh:** run the pipeline on your machine with `SRT_DATABASE_URL` pointing to the production database: `build --publish` reloads the PostGIS tables. New function instances pick the data up; redeploy to refresh the running ones.
**Backups:** use the provider's (Neon keeps a point-in-time history), or `pg_dump` from your machine.

### Monitoring

- `GET /health`: liveness (process up) and version.
- `GET /health/ready`: database reachable, schema up to date, reference data loaded. Answers 503 otherwise. Docker's `HEALTHCHECK` uses it, so `docker compose ps` shows `unhealthy`.
- Logs: one JSON object per line on stdout (`SRT_LOG_FORMAT=json`, the default in the image; `text` locally). Every request is logged with `method`, `path`, `status`, `duration_ms`, `client` and a `request_id`, also returned in the `X-Request-ID` header (a proxy's own `X-Request-ID` is reused). Docker rotates logs at 5 × 10 MB per container.
  `docker compose logs -f api | jq 'select(.status >= 500)'`

### Backups and restore

```bash
docker compose exec backup /bin/sh /backup.sh once                        # backup now
docker compose stop api
docker compose exec backup /bin/sh /backup.sh restore /backups/school_risks_<stamp>.dump
docker compose start api
```

Backups are `pg_dump` custom-format files of the whole database (users, actions, audit log, snapshots and reference tables). Copy `./backups` off the server (rsync, object storage): a backup on the same disk does not survive losing that disk.

### CI

`.github/workflows/ci.yml` runs on each push to `main` and each pull request:
1. **lint**: `ruff check`
2. **test**: pytest on SQLite and on a PostGIS service container, then `alembic check` (models and migrations agree)
3. **docker**: builds the image, starts the whole stack with `docker compose up --wait`, logs in, calls a PostGIS endpoint and takes a backup

Locally: `pip install -e ".[dev]" -c requirements.lock`, then `ruff check .` and `pytest -q`.

### Dependencies

- `pyproject.toml` declares them: `[project] dependencies` (everything the API needs; parquet files are read with fastparquet), the `parquet` extra (pyarrow: faster, used by the data pipeline) and the `dev` extra (parquet + pytest + ruff).
- `requirements.lock` pins the exact runtime versions that were tested. Docker installs only from it, CI uses it as constraints.
- After adding or upgrading a dependency in `pyproject.toml`, regenerate the lock in a clean virtualenv:

```bash
python -m venv /tmp/lockenv && /tmp/lockenv/bin/pip install ".[parquet]"   # Docker needs pyarrow
( echo "# Generated from pyproject.toml with pip freeze (runtime only). Do not edit by hand."
  /tmp/lockenv/bin/pip freeze --exclude school-risk-tracker ) > requirements.lock
```

## Security settings (env vars, prefix `SRT_`)

| Variable | Purpose |
|---|---|
| `SRT_JWT_SECRET_KEY` | **Required**, at least 32 bytes. Signs access tokens. |
| `SRT_BOOTSTRAP_ADMIN_USERNAME` / `SRT_BOOTSTRAP_ADMIN_PASSWORD` | Creates the first admin at startup if it does not exist (password ≥ 12 chars). |
| `SRT_CORS_ORIGINS` | JSON list of allowed browser origins, e.g. `'["https://risques.example.fr"]'`. |
| `SRT_RATE_LIMIT_DEFAULT` / `SRT_RATE_LIMIT_LOGIN` | Per-IP limits, default `120/minute` and `5/minute`. |
| `SRT_RATE_LIMIT_STORAGE_URI` | `memory://` by default (per process, reset on restart). Use `redis://host:6379` with several workers (`pip install redis`). |
| `SRT_TRUSTED_PROXIES` | JSON list of reverse-proxy IPs allowed to set `X-Forwarded-For`, e.g. `'["10.0.0.2"]'`. Needed behind a proxy, otherwise every user shares the proxy's IP for rate limiting and the audit log. |

Changing a user's password revokes every token issued before the change.
Logins (successful and failed), action changes and user changes are written to the audit log (`GET /auth/audit`, admin only).

## Refreshing the data

```bash
python -m app.pipeline build                          # dry run: download, rebuild, validate, report
python -m app.pipeline build --publish --snapshot     # replace data/*.parquet, then store this year's scores
python -m app.pipeline build --keep argiles_classe    # refresh everything except that column
python -m app.pipeline snapshot --year 2026           # store the current scores for a year
```

Every run writes the rebuilt files and a `report.md` to `data/staging/<timestamp>/`. The report lists level changes, the biggest score changes and every changed value.
Nothing is published if validation fails (schema, school list, empty or out-of-range values).
Publishing copies the previous files to `data/archive/<timestamp>/` first. To roll back, copy them back.
Raw API responses are cached in `data/raw/<date>/`, so a re-run the same day (or `--offline`) needs no network.
On Postgres, publishing also reloads the PostGIS reference tables. Restart the API after publishing: it loads the data once at startup.

| Column(s) | Source |
|---|---|
| statut, history (enrolment, REP/REP+, IPS, DNB, value added) | data.education.gouv.fr: annuaire, effectifs, IPS (3 datasets), DNB par établissement, IVAC |
| argiles_classe, cavities, ground movements, CatNat, DICRIM, ICPE / Seveso | Géorisques API v1 |
| dans_ppri, ppri | IGN GPU API (servitude PM1) |
| distance_centrale_km, centrale_proche | Static plant coordinates in `app/pipeline/geo.py` |
| secteur_id + sector polygons, TMD columns, distance_repere_crue_km | **Not refreshed**: no public point API, copied from the published file |

The list of schools, their names and positions come from the published file (the roster). The report flags schools that are new or closed in the directory, and positions that differ by more than 200 m.

## Structure

```
app/
├── main.py              # FastAPI app, lifespan (loads data + checks DB), middleware, routers
├── core/logging.py      # JSON / text logs, request id middleware
├── core/config.py       # Settings (env vars prefixed SRT_), score weights
├── db.py                # engine + session, SQLite or Postgres, migration check
├── manage.py            # python -m app.manage: check, load-reference, copy-sqlite
├── data/store.py        # Loads parquet/geojson once, filtering, stats
├── services/scoring.py  # 0–100 score per hazard + global score + alerts
├── services/snapshots.py # yearly score snapshots
├── services/spatial.py  # PostGIS queries (ST_Contains, ST_DWithin)
├── services/reference_db.py # data files <-> PostGIS reference tables
├── pipeline/            # rebuilds data/*.parquet from the source APIs (python -m app.pipeline)
├── models/
│   ├── schemas.py       # Pydantic response models (read-only data)
│   ├── tracking.py      # Action table + create/update/read schemas
│   ├── security.py      # users, school assignments, audit log
│   ├── snapshots.py     # score_snapshot table
│   └── reference.py     # PostGIS tables: college, college_annee, secteur
migrations/              # Alembic (Postgres schema)
pyproject.toml           # dependencies, Vercel entrypoint + build step, ruff / pytest settings
vercel.json              # Vercel function settings (duration, excluded files)
requirements.lock        # exact tested versions (Docker, CI)
└── api/routes/
    ├── colleges.py      # /colleges, /colleges/{uai}, /risques, /historique
    ├── secteurs.py      # /secteurs (GeoJSON with scores attached)
    ├── actions.py       # CRUD on follow-up actions
    └── stats.py         # dashboard numbers
tests/                   # pytest + TestClient, in-memory SQLite
```

## Endpoints (prefix `/api/v1`)

| Method | Path | What it does |
|---|---|---|
| GET | `/colleges?commune=&statut=&secteur_id=&niveau=&q=&tri=` | List of schools, highest first: `tri=score` (default) or `tri=priorite` (pupils exposed × risk) |
| GET | `/colleges/proches?lat=&lon=&rayon_km=5` | Schools within a radius, nearest first (PostGIS) |
| GET | `/colleges/{uai}` | Full risk data and computed profile for one school |
| GET | `/colleges/{uai}/risques` | Score per hazard, global score, level, alerts |
| GET | `/colleges/{uai}/historique` | Enrolment, IPS and DNB results by year |
| GET | `/colleges/{uai}/scores-annuels` | Risk scores stored by the yearly snapshots |
| GET | `/secteurs?with_scores=true` | GeoJSON of the sectors, with each school's score attached |
| GET | `/secteurs/localiser?lat=&lon=` | Sector containing a point, and its schools (PostGIS) |
| GET | `/secteurs/{secteur_id}` | One sector polygon |
| GET/POST | `/actions` | List actions (filter by `uai`, `alea`, `statut`, `priorite`, `en_retard`) or create one |
| GET/PATCH/DELETE | `/actions/{id}` | Read, update or delete one action |
| GET | `/stats` | Risk distribution and action progress |

## Risk score (to validate)

Each hazard gets a score from 0 to 100, computed in `services/scoring.py`:

- **inondation** (flood): inside a PPRI, CatNat flood declarations, distance to a flood marker
- **argiles** (clay soils): clay class plus CatNat drought declarations
- **mouvements_terrain** (ground movement): cavities and ground movements within 500 m
- **industriel** (industrial): distance to a Seveso site, number of ICPE sites within 1 km
- **tmd** (hazardous-materials transport): distance to the route, heavier for motorways and railways
- **nucleaire** (nuclear): distance to the nearest plant

The global score is a weighted average. You can change the weights with env vars, for example `SRT_WEIGHT_INONDATION=2`.
The level is `faible` below 30, `moyen` from 30 to 49 and `eleve` from 50.
With the current thresholds the 70 schools split into 30 faible, 29 moyen and 11 eleve.

### Priority index (pupils exposed)

The score says how dangerous a school's location is. The **priority index** says how many pupils that danger concerns:
`indice_priorite = score_global × effectif_total / 100`, i.e. pupils weighted by risk.
A school scoring 60 with 800 pupils (index 480) ranks above one scoring 70 with 150 pupils (index 105).
The score and the level (`faible` / `moyen` / `eleve`) do not change.
A school without an enrolment figure gets no index (not 0) and the alert "Effectif inconnu", and comes last when sorting by priority.
With the current data the index goes from 31 to 582. Compared with the score ranking, large schools with a medium score move up (Condorcet, Montabuzard), and small schools with a high score move down.
The dashboard can rank the list by score or by pupils exposed. In "élèves exposés" mode the map dots are sized by the index (area ∝ index), and their colour stays the risk level.
Yearly snapshots store the pupil count and the index from Sprint 8 on. Older snapshots leave them empty.

⚠️ These thresholds are a heuristic for setting priorities. They are not an official classification. Sprint 2 is where you review them.

---

## Next sprints

Order: Sprint 8 is small and changes how priorities are read. Sprint 9 creates the PPMS data that Sprint 10 sends alerts about. Sprint 11 does not depend on the others and can move earlier if people ask for reports first.

### ✅ Sprint 8: Priority weighted by pupils exposed
- Keep `score_global` as it is (how dangerous the location is) and add a separate **priority index**: `score_global × effectif_total / 100`, i.e. "pupils exposed, weighted by risk". A score of 60 with 800 pupils then ranks above 70 with 150 pupils.
- Schools without an `effectif_total` get no index and are flagged, instead of being treated as 0.
- API: `GET /colleges?tri=priorite`, the index in the summaries and in `/stats` (pupils per risk level). It is stored in the yearly snapshots too (migration).
- Frontend: sort by "priorité" in the list, and a map toggle score / priority.
- **Done when:** the risk referent agrees with the new top 10 (**still to do**: review with them), and tests cover the missing-enrolment case (done).

### ⏭ Sprint 9: PPMS (safety plan) tracking
- No open dataset exists, so the data is entered in the app: a `ppms` table (school, last update date, last drills by type: risques majeurs, intrusion, incendie, who entered it), keeping the history.
- Rules (configurable): PPMS not updated for more than `SRT_PPMS_MAX_AGE_MONTHS` (default 12), or no drill this school year. They are shown as alerts in the risk profile, like the DICRIM alert.
- Permissions follow actions: referents edit their own schools, lecteurs read, every change is audited.
- Dashboard: how many schools have an outdated PPMS, and a filter `GET /colleges?ppms=a_jour|ancien|absent`.
- **Done when:** a referent can enter a PPMS and the drills for their school, and the outdated ones are visible on the dashboard.

### ⏭ Sprint 10: Email alerts
- Users get an `email` (migration) and an opt-in for alerts.
- Weekly digest per referent for their schools: overdue actions, actions due within 7 days, outdated PPMS (Sprint 9). Admins get a department-wide summary.
- Sending through SMTP settings (any provider). Locally, a "log only" mode writes the emails to the logs instead of sending them.
- Scheduling: an internal endpoint protected by a secret, called by **Vercel Cron** (`vercel.json` `crons`, `CRON_SECRET`) or by a small cron service in docker compose.
- A `notification_log` table makes every run idempotent: running twice in the same week sends nothing twice.
- **Done when:** a referent receives the digest in a real inbox, a second run sends nothing, and the whole run fits in the Vercel function time limit.

### ⏭ Sprint 11: Exports (CSV and PDF)
- CSV: the list of schools with their scores (same filters as the API) and the list of actions. Referents only export their own schools. The files open correctly in French Excel (UTF-8 with BOM, `;` separator).
- PDF "fiche risques" per school: identity, score per hazard, alerts, PPMS status, open actions and score history. Built with **fpdf2** (pure Python, works on Vercel; WeasyPrint needs system libraries Vercel does not have).
- Download buttons on the school page and the dashboard. Exports are written to the audit log.
- **Done when:** a referent downloads the fiche for one of their schools and gets a 403 for any other school.

### Carried over from earlier sprints
- Sprint 2: review the risk thresholds with a risk referent, and add unit tests for each hazard function.
- Sprint 3: action history and comments (the audit log only records who changed which fields).
- Sprint 6: schedule the data refresh (for example a monthly GitHub Actions job that runs the pipeline and opens a pull request with the report).
- Frontend for the spatial endpoints: click the map to see the sector and its schools, and "schools near me".
