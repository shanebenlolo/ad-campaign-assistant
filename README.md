# Ad Campaign Assistant

A locally hosted, containerized command-line assistant for Google Ads management.
It has two commands:

- `plan`: collect Google Ads, Keyword Planner, auction-visibility proxy, and current ad copy data, then ask Anthropic to produce a reviewable JSON execution plan.
- `apply`: read a previously generated JSON plan and apply only actions that are explicitly marked `"approved": true`.

The AI planning layer never mutates Google Ads. It only writes recommendations to a local JSON file.

## Quick Start

```bash
cp .env.example .env
# edit .env with Google Ads and Anthropic credentials

docker compose build
docker compose run --rm ads-assistant plan --output plans/execution-plan.json
```

Review `plans/execution-plan.json`. Every generated action is forced to:

```json
"approved": false
```

Set individual actions to `true` only after review, then validate or apply:

```bash
docker compose run --rm ads-assistant apply --plan plans/execution-plan.json --dry-run
docker compose run --rm ads-assistant apply --plan plans/execution-plan.json --validate-only
docker compose run --rm ads-assistant apply --plan plans/execution-plan.json
```

Each `apply` run writes a JSONL log under `logs/`.

## Local Python Usage

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .

ads-assistant plan --customer-id 1234567890 --output plans/execution-plan.json
ads-assistant apply --plan plans/execution-plan.json --dry-run
```

Global option:

```bash
ads-assistant --env-file .env plan
```

## Plan Command

The `plan` command collects:

- Campaign performance
- Ad group performance
- Keyword performance
- Geographic performance
- Search-term data
- Current ad copy and landing URLs
- Keyword Planner ideas
- Auction visibility proxy metrics from Google Ads impression-share fields

Useful options:

```bash
ads-assistant plan \
  --customer-id 1234567890 \
  --date-range LAST_30_DAYS \
  --keyword-seed "emergency plumber" \
  --keyword-seed "same day plumbing" \
  --location-id 2840 \
  --language-id 1000 \
  --limit 150 \
  --output plans/plumbing-plan.json \
  --save-input-snapshot
```

Custom date ranges are also supported:

```bash
ads-assistant plan --date-range 2026-05-01,2026-05-31
```

## Apply Command Safeguards

`apply` only considers actions found in the plan file. By default:

- Unapproved actions are skipped.
- Unsupported action types are skipped.
- Every action is logged to JSONL.
- `--dry-run` logs what would happen without calling Google Ads.
- `--validate-only` asks Google Ads to validate mutations without applying them.

Supported automated action types:

- `SET_CAMPAIGN_BUDGET`
- `SET_CAMPAIGN_STATUS`
- `PAUSE_AD_GROUP`
- `PAUSE_KEYWORD`
- `SET_KEYWORD_CPC_BID`
- `ADD_AD_GROUP_NEGATIVE_KEYWORD`
- `ADD_CAMPAIGN_NEGATIVE_KEYWORD`
- `CREATE_RESPONSIVE_SEARCH_AD`

Recommendations outside those types are written under `unsupported_recommendations` for manual review.

## Plan File Shape

The generated file is an envelope around the planner output:

```json
{
  "schema_version": "1.0",
  "created_at": "2026-06-20T12:00:00+00:00",
  "customer_id": "1234567890",
  "date_range": "LAST_30_DAYS",
  "planner": {
    "provider": "anthropic",
    "model": "claude-sonnet-4-6",
    "max_tokens": 8000,
    "temperature": 0.2,
    "usage": {
      "input_tokens": 1234,
      "output_tokens": 1234
    }
  },
  "data_sources": {},
  "execution_plan": {
    "summary": "...",
    "diagnostics": [],
    "actions": [],
    "unsupported_recommendations": []
  }
}
```

## Notes On Data Availability

Google Ads does not expose every Auction Insights UI field through the public API.
This app captures available campaign and keyword impression-share metrics as auction-visibility proxies.
No external competitor-signal API is required.

## Configuration References

- [Google Ads Python client configuration](https://developers.google.com/google-ads/api/docs/client-libs/python/configuration)
- [Google Ads API field reference](https://developers.google.com/google-ads/api/fields/v24/overview)
- [Anthropic Messages API](https://docs.anthropic.com/en/api/messages)
