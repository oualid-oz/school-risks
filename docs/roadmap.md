# Roadmap

Two-week sprints. ✅ done · 🚧 in progress · ⏭ planned. "Still open" lists what a sprint left for later.

## Done

### ✅ Sprint 0: Setup
Project structure, settings (`SRT_*` env vars), data store, tests, Swagger docs.

### ✅ Sprint 1: Read-only API
`/colleges`, `/colleges/{uai}`, `/historique`, `/secteurs`, filters and search, over the 70 schools and 59 sectors.

### ✅ Sprint 2: Risk scoring
Score per hazard (0–100), weighted global score, level (`faible` / `moyen` / `eleve`), main hazard, DICRIM alert.
- **Still open:** review the thresholds with a risk referent; unit tests for each hazard function (→ Sprint 14).

### ✅ Sprint 3: Action tracking
CRUD on follow-up actions (hazard, priority, status, owner, due date) with an overdue flag. Alembic migrations came in Sprint 6.
- **Still open:** action history and comments (→ Sprint 15).

### ✅ Sprint 4: Map and dashboard
Static frontend served by FastAPI: Leaflet map of sectors and schools, school page with hazard radar, DNB/IPS history and actions.

### ✅ Sprint 5: Users and security
JWT login (OAuth2 password flow), roles `admin` / `referent` (own schools) / `lecteur` (read only), audit log, rate limiting, CORS limited to known origins. A password change revokes earlier tokens.

### ✅ Sprint 6: Data pipeline, snapshots, PostGIS
- `python -m app.pipeline build`: rebuilds the parquet files from data.education.gouv.fr, Géorisques and IGN, validates them and writes a change report before publishing.
- Yearly score snapshots.
- Postgres + PostGIS with Alembic: reference tables with spatial indexes, `ST_Contains` (sector of a point) and `ST_DWithin` (schools nearby).
- **Still open:** TMD, flood-marker distance and sectors are carried over (no public source found); scheduled refresh (→ Sprint 14).

### ✅ Sprint 7: Deployment
- Docker image and compose stack (PostGIS, Redis rate limits, migrations, API, daily backups with restore).
- GitHub Actions CI: lint, tests on SQLite and PostGIS, and a Docker smoke test.
- `/health` and `/health/ready`, JSON logs with request ids.
- Vercel deployment: serverless function, Neon Postgres, build step that migrates and loads the reference data.

### ✅ Sprint 8: Priority weighted by pupils exposed
Priority index `score_global × effectif_total / 100`, `?tri=priorite`, ranking toggle and bubble map, stored in the snapshots.
- **Still open:** the risk referent to agree with the new top 10.

### ✅ Sprint 9: PPMS tracking
PPMS updates and drills entered in the app, computed status (`a_jour` / `ancien` / `absent`), alerts, dashboard card, `?ppms=` filter.
- **Still open:** confirm the rules (12 months, counted drill types, 90-day grace) with whoever is responsible for safety plans.

### ✅ Sprint 10: Email alerts
Weekly digest for opted-in admins and referents: overdue actions, actions due soon, PPMS to update. SMTP or log backend. At most one per person and week (`notification_log`). Vercel Cron and a compose scheduler.
- **Still open:** configure a real provider and check a real inbox.

### ✅ Sprint 11: Exports
CSV for French Excel (schools, actions), protected against formula injection. One-page PDF "fiche risques" per school (fpdf2, bundled DejaVu fonts). Same access rules as the API; every export is audited.

## Next

### ✅ Sprint 12: Actions and accounts in the interface
- School page: create, edit, close and delete actions (open ones first, overdue first; closed ones folded), for admins and the school's referents.
- "Mon compte": email alerts and own password change (`POST /auth/me/password`: checks the current password, rate limited like the login, returns a new token since older ones are revoked).
- Admin page (`#admin`): users table, create and edit (role, schools with a filterable picker, email, alerts, active, password reset).
- API error messages shown in the interface are now in French.
- Fixed: a referent could not open the page of a school they do not follow (the page also loaded that school's actions, which answer 403); the page now loads and says the actions are reserved.
- Checked in a headless browser (Playwright): admin creates a referent, action create/edit/close/delete, password change, referent restrictions, no JavaScript errors.

### ✅ Sprint 13: Browser tests and the map
- `tests/e2e`: 16 Playwright tests against a real server on a fresh PostGIS database, in a new CI job (screenshots and server log attached on failure). They fail on any JavaScript error.
- Map: click a point or search an address (Base adresse nationale, Loiret only) to see its sector, the school(s) serving it and the schools within 5 km.
- Accessibility: axe-core audit in CI (no serious or critical WCAG 2.1 AA issue on login, dashboard, school, admin, account). 15 text colours darkened to reach 4.5:1. Skip link, visible keyboard focus, the focus moves to the new page's title after navigation, and keyboard-only use is tested.
- PNG `apple-touch-icon` rendered from the favicon.
- Fixed: the sector polygons' popup read a field that does not exist and always showed "Secteur {id}"; it is now a hover tooltip with the sector name.

### ⏭ Sprint 14: Score review and data completeness
- Unit tests for each hazard function.
- A "what-if" view: how levels and the top 10 change with other thresholds or weights, for the review with the risk referent. Decide the clay map question (previous classes or the newer Géorisques exposure map).
- Find sources for the TMD, flood-marker and sector data still carried over.
- Monthly GitHub Actions job that runs the pipeline and opens a pull request with the change report.
- **Done when:** the risk referent signs off the ranking, and the data refreshes without manual steps.

### ⏭ Sprint 15: Action history and comments
- Timeline per action (who changed what, when): today the audit log only records the field names.
- Comments on actions; optional email to the person in charge when an action is assigned.
- Last history entries in the PDF fiche.
- **Done when:** a referent can see why an action is late without asking anyone.

### Operations (when convenient)
- Error alerting (Sentry or a log-based alert) and an external uptime check on `/health/ready`.
- Shared rate-limit store on Vercel (Upstash Redis): without it each function instance counts requests on its own.

## Order
Sprint 14 depends on the risk referent's availability and can move. Sprint 15 builds on the action screens of Sprint 12.
