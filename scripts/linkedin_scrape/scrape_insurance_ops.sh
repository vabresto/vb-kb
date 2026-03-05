#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

TARGET="${1:-100}"
OUTPUT_NAME="${2:-insurance_second_degree_icp_results.csv}"
MAX_PAGES="${3:-120}"
PROJECT_ROOT_ARG="${4:-$PROJECT_ROOT}"
CMUX_SURFACE="${5:-surface:2}"

BASE_URL='https://www.linkedin.com/search/results/people/?keywords=insurance%20director%20VP%20claims%20operations%20policy%20administration%20service%20ops%20operations%20excellence%20transformation%20regulatory%20reporting%20operations&facetNetwork=%5B%22S%22%5D&origin=GLOBAL_SEARCH_HEADER'

OUT_FILE="${PROJECT_ROOT_ARG}/${OUTPUT_NAME}"
EXTRACT_SCRIPT="$(cat "${SCRIPT_DIR}/extractors/insurance_ops_results.js")"
RETRY_COUNT="${RETRY_COUNT:-3}"

mkdir -p "${PROJECT_ROOT_ARG}"

all_rows='[]'
empty_streak=0
for page in $(seq 1 "${MAX_PAGES}"); do
  cmux browser goto --surface "${CMUX_SURFACE}" "${BASE_URL}&page=${page}" >/dev/null
  cmux browser wait --surface "${CMUX_SURFACE}" --load-state complete --timeout-ms 30000 >/dev/null

  page_rows='[]'
  for _attempt in $(seq 1 "${RETRY_COUNT}"); do
    candidate="$(cmux browser eval --surface "${CMUX_SURFACE}" "$EXTRACT_SCRIPT")"
    if jq -e 'type == "array"' <<< "$candidate" >/dev/null; then
      page_count="$(jq 'length' <<< "$candidate")"
      if (( page_count > 0 )); then
        page_rows="$(echo "$candidate")"
        break
      fi
    fi
    sleep 1
  done

  if [[ "$page_rows" == '[]' ]]; then
    empty_streak=$((empty_streak + 1))
  else
    empty_streak=0
  fi

  matched="$(jq '[.[] | select(.connection_degree == "2nd" and .role_match == true)]' <<< "$page_rows")"
  all_rows="$(jq -s '.[0] + .[1] | unique_by(.linkedin_url)' <(echo "$all_rows") <(echo "$matched"))"

  raw_count="$(jq 'length' <<< "$page_rows")"
  match_count="$(jq 'length' <<< "$all_rows")"
  echo "page=${page} raw=${raw_count} matched=${match_count}"

  if (( empty_streak >= 6 )); then
    echo "No parseable profiles for ${empty_streak} consecutive pages; stopping."
    break
  fi

  if (( match_count >= TARGET )); then
    break
  fi
done

printf 'name,connection_degree,title,org,linkedin_url,named_mutuals,mutual_total\n' > "${OUT_FILE}"
jq -r '.[] | [.name, .connection_degree, .title, .org, .linkedin_url, .named_mutuals, .mutual_total] | @csv' <<< "$all_rows" >> "${OUT_FILE}"

echo "WROTE=${OUT_FILE}"
echo "ROWS=${TARGET:-0}/${match_count:-0}"
echo "UNIQ=${match_count:-0}"
