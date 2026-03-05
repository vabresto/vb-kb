#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

OUTPUT_NAME="${1:-waterloo_first_degree_results.csv}"
MAX_PAGES="${2:-20}"
PROJECT_ROOT_ARG="${3:-$PROJECT_ROOT}"
CMUX_SURFACE="${4:-surface:2}"

OUT_FILE="${PROJECT_ROOT_ARG}/${OUTPUT_NAME}"
BASE_URL='https://www.linkedin.com/search/results/people/?geoUrn=%5B102571732%5D&keywords=waterloo%20alumni&network=%5B%22F%22%5D&origin=FACETED_SEARCH&page='

EXTRACT_SCRIPT="$(cat "${SCRIPT_DIR}/extractors/waterloo_search_results.js")"
STATE_SCRIPT="$(cat "${SCRIPT_DIR}/helpers/page_state.js")"
CLICK_SCRIPT="$(cat "${SCRIPT_DIR}/helpers/click_next.js")"

mkdir -p "${PROJECT_ROOT_ARG}"

page=0
all_rows='[]'

cmux browser goto --surface "${CMUX_SURFACE}" "${BASE_URL}1" >/dev/null
cmux browser wait --surface "${CMUX_SURFACE}" --load-state complete --timeout-ms 30000 >/dev/null

while (( page < MAX_PAGES )); do
  ((page++))

  page_rows="$(cmux browser eval --surface "${CMUX_SURFACE}" "$EXTRACT_SCRIPT")"
  if ! jq -e 'type == "array"' <<< "$page_rows" >/dev/null; then
    echo "WARN: page=${page} returned non-array payload, skipping row merge." >&2
    page_rows='[]'
  fi
  all_rows="$(jq -s '.[0] + .[1]' <(echo "$all_rows") <(echo "$page_rows"))"

  state_json="$(cmux browser eval --surface "${CMUX_SURFACE}" "$STATE_SCRIPT")"
  if ! jq -e 'type == "object"' <<< "$state_json" >/dev/null; then
    break
  fi
  has_next="$(jq -r '.hasNext // false' <<< "$state_json")"
  current_page="$(jq -r '.page // 0' <<< "$state_json")"
  total_pages="$(jq -r '.total // 1' <<< "$state_json")"

  if [[ "$has_next" != "true" ]] || (( current_page >= total_pages )); then
    break
  fi

  clicked="$(cmux browser eval --surface "${CMUX_SURFACE}" "$CLICK_SCRIPT")"
  if [[ "$clicked" != "true" ]]; then
    break
  fi
  cmux browser wait --surface "${CMUX_SURFACE}" --load-state complete --timeout-ms 30000 >/dev/null
done

printf 'name,connection_degree,title,org,linkedin_url,named_mutuals,mutual_total\n' > "${OUT_FILE}"
jq -r 'unique_by(.linkedin_url) | .[] | [.name, .connection_degree, .title, .org, .linkedin_url, .named_mutuals, .mutual_total] | @csv' <<< "$all_rows" >> "${OUT_FILE}"

echo "WROTE=${OUT_FILE}"
echo "PAGES=${page}"
echo "ROWS=$(jq 'length' <<< "$all_rows")"
echo "UNIQ_ROWS=$(jq 'unique_by(.linkedin_url) | length' <<< "$all_rows")"
