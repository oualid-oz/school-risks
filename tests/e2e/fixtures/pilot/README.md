Pilot data for the browser tests: Loiret (45), Charente-Maritime (17) and Haute-Savoie (74),
taken from the all-of-France dry run of 25/11/2026 (`python -m app.pipeline build
--departements all`, bulk mode), with the hazards of Sprint 19 (earthquake, radon, wildfire,
coast, avalanche) and the sectors of Sprint 20 (Loiret: the department's map;
Haute-Savoie: the national school map; Charente-Maritime: none, it is missing from the school
map). The 136 schools outside Loiret have no TMD or flood-marker data (no public
source), so their scores are minima: do not publish this as is.
