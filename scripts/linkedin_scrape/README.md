# LinkedIn Scrape Helpers

This folder contains reusable scripts extracted from `private-tmp-archive-20260305` for
long-lived ad-hoc LinkedIn search scraping workflows.

Requirements:
- `cmux` CLI available in `PATH`
- `jq`
- `bash` 4+ with `pipefail`

## Scripts

- `scrape_waterloo_first_degree.sh`  
  Scrapes Waterloo alumni search results from the first-degree query used in the archive and writes a CSV.
- `scrape_insurance_ops.sh`  
  Scrapes insurance-operations related people from a fixed LinkedIn search query with 2nd-degree filtering.
- `extractors/waterloo_search_results.js`  
  DOM extractor for Waterloo Alumni result cards.
- `extractors/insurance_ops_results.js`  
  DOM extractor with role-match heuristics for the insurance-operations query.

Both scrape scripts keep data in a deduplicated shape:
`name`, `connection_degree`, `title`, `org`, `linkedin_url`, `named_mutuals`, `mutual_total`

`insurance_ops_results.js` additionally returns:
`role_match`, `role_flags` when used for filtering/QA.
