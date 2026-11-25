#!/bin/sh
# Monthly data refresh (used by .github/workflows/data-refresh.yml, also runnable locally).
#
#   deploy/data_refresh.sh            rebuild + publish into data/, write the PR text, no git
#   PUSH=1 deploy/data_refresh.sh     ...then commit to data-refresh/YYYY-MM and open/update a PR
#
# KEEP="argiles_classe ..." keeps columns from the published files (pipeline --keep).
# WORKERS=8 sets the parallel per-school requests. With SRT_PIPELINE_DATABASE_URL set (a
# PostGIS database), the build runs in bulk mode; SRT_DEPARTEMENTS=all needs it.
# Exit codes: 0 done (changed or not), 1 validation failed (nothing published).
set -eu

month=$(date -u +%Y-%m)
keep_args=""
for column in ${KEEP:-}; do keep_args="$keep_args --keep $column"; done

# shellcheck disable=SC2086  # keep_args is a list of words on purpose
python -m app.pipeline build --publish --workers "${WORKERS:-4}" $keep_args

report=$(ls -1d data/staging/*/ | sort | tail -n 1)report.md
FILES="data/colleges.parquet data/colleges_risques.parquet data/colleges_secteurs.geojson"
# shellcheck disable=SC2086
if git diff --quiet -- $FILES; then
    echo "No change in the reference data this month."
    [ -n "${GITHUB_STEP_SUMMARY:-}" ] && { echo "No change in the reference data."; cat "$report"; } >> "$GITHUB_STEP_SUMMARY"
    exit 0
fi

body=data/staging/pr-body.md
{
    echo "Monthly refresh of the reference data (\`python -m app.pipeline build --publish\`)."
    echo
    echo "**Before merging:** read the level changes below. If schools change level, have the change"
    echo "checked by the risk referent (the Simulation page helps to discuss it)."
    echo "Merging deploys the new files: the Vercel build reloads them into PostGIS (their fingerprint"
    echo "changed); Docker reloads them at every deploy."
    [ -n "${KEEP:-}" ] && echo && echo "Columns kept from the previous files: \`${KEEP}\`."
    echo
    # GitHub refuses pull request bodies over 65,536 characters: a national report can be longer.
    if [ "$(wc -c < "$report")" -gt 60000 ]; then
        awk '{ n += length($0) + 1; if (n > 60000) exit; print }' "$report"  # whole lines only
        echo
        echo
        echo "_Report truncated: the full report.md is in the run's artifact._"
    else
        cat "$report"
    fi
} > "$body"
[ -n "${GITHUB_STEP_SUMMARY:-}" ] && cat "$body" >> "$GITHUB_STEP_SUMMARY"
echo "Data changed. Pull request text: $body"

[ "${PUSH:-0}" = "1" ] || exit 0

branch="data-refresh/$month"
title="Données de référence : actualisation $month"
git config user.name "school-risks data refresh"
git config user.email "data-refresh@users.noreply.github.com"
git switch -C "$branch"
# shellcheck disable=SC2086
git add $FILES
git commit -m "$title"
git push --force origin "$branch"  # a rerun in the same month replaces the branch
if gh pr view "$branch" --json state --jq .state 2>/dev/null | grep -q OPEN; then
    gh pr edit "$branch" --title "$title" --body-file "$body"
else
    gh pr create --head "$branch" --base "${BASE_BRANCH:-main}" --title "$title" --body-file "$body"
fi
