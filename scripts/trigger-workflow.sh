#!/usr/bin/env bash
# Trigger the Twitter Bot workflow via workflow_dispatch (bypasses sluggish GitHub cron).
# Used by external schedulers (cron-job.org) and for manual smoke tests.
set -euo pipefail

REPO="${GITHUB_REPOSITORY:-gliu-nova/twitter-bot}"
WORKFLOW_ID="${WORKFLOW_ID:-299012131}"
REF="${WORKFLOW_REF:-main}"
SOURCE="${WORKFLOW_SOURCE:-external-cron}"
TOKEN="${GH_DISPATCH_TOKEN:-${GITHUB_TOKEN:-}}"

if [[ -z "${TOKEN}" ]]; then
  echo "Set GH_DISPATCH_TOKEN (or GITHUB_TOKEN) to a PAT with actions:write on ${REPO}" >&2
  exit 1
fi

BODY=$(printf '{"ref":"%s","inputs":{"source":"%s"}}' "${REF}" "${SOURCE}")

HTTP_CODE=$(curl -sS -o /tmp/gh-dispatch.json -w "%{http_code}" -X POST \
  -H "Accept: application/vnd.github+json" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  "https://api.github.com/repos/${REPO}/actions/workflows/${WORKFLOW_ID}/dispatches" \
  -d "${BODY}")

if [[ "${HTTP_CODE}" == "204" ]]; then
  echo "Dispatched ${REPO} workflow ${WORKFLOW_ID} (ref=${REF}, source=${SOURCE})"
  exit 0
fi

echo "Dispatch failed (HTTP ${HTTP_CODE}):" >&2
cat /tmp/gh-dispatch.json >&2
exit 1