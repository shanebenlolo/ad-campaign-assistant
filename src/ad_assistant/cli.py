from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from .google_ads import GoogleAdsDataCollector, GoogleAdsPlanApplier, build_google_ads_client
from .planner import AnthropicPlanner, summarize_data_sources
from .serpapi import SerpApiCollector
from .settings import Settings
from .utils import normalize_customer_id, read_json, utc_now_iso, write_json, write_jsonl


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ads-assistant",
        description="Local Google Ads management assistant with plan/apply commands.",
    )
    parser.add_argument(
        "--env-file",
        default=".env",
        help="Path to a dotenv file. Environment variables still take precedence.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan_parser = subparsers.add_parser(
        "plan", help="Collect Google Ads data and generate a reviewable JSON plan."
    )
    plan_parser.add_argument(
        "--customer-id",
        help="Google Ads customer ID. Defaults to GOOGLE_ADS_CUSTOMER_ID.",
    )
    plan_parser.add_argument(
        "--date-range",
        help=(
            "Google Ads date range constant like LAST_30_DAYS, or "
            "YYYY-MM-DD,YYYY-MM-DD."
        ),
    )
    plan_parser.add_argument(
        "--output",
        default="plans/execution-plan.json",
        help="Where to write the JSON execution plan.",
    )
    plan_parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Maximum rows per Google Ads report.",
    )
    plan_parser.add_argument(
        "--keyword-seed",
        action="append",
        default=[],
        help="Keyword seed for Keyword Planner and SERP competitor checks. Repeatable.",
    )
    plan_parser.add_argument(
        "--location-id",
        action="append",
        default=[],
        help="Keyword Planner geo target constant ID. Repeatable.",
    )
    plan_parser.add_argument(
        "--language-id",
        help="Keyword Planner language constant ID.",
    )
    plan_parser.add_argument(
        "--skip-serpapi",
        action="store_true",
        help="Skip SerpAPI competitor-signal collection even when configured.",
    )
    plan_parser.add_argument(
        "--save-input-snapshot",
        action="store_true",
        help="Also save the normalized planner input next to the plan file.",
    )
    plan_parser.set_defaults(func=run_plan)

    apply_parser = subparsers.add_parser(
        "apply", help="Apply approved actions from a previously generated plan file."
    )
    apply_parser.add_argument(
        "--plan",
        required=True,
        help="Path to a generated execution plan JSON file.",
    )
    apply_parser.add_argument(
        "--customer-id",
        help="Optional override for Google Ads customer ID. Defaults to plan/env.",
    )
    apply_parser.add_argument(
        "--log-file",
        help="JSONL action log path. Defaults to logs/apply-<timestamp>.jsonl.",
    )
    apply_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Read and log approved actions without calling Google Ads mutate APIs.",
    )
    apply_parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Ask Google Ads to validate approved changes without applying them.",
    )
    apply_parser.set_defaults(func=run_apply)
    return parser


def run_plan(args: argparse.Namespace) -> int:
    settings = Settings.from_env(args.env_file)
    customer_id = normalize_customer_id(
        args.customer_id or settings.google_ads_customer_id or ""
    )
    date_range = args.date_range or settings.default_date_range
    location_ids = args.location_id or settings.default_location_ids
    language_id = args.language_id or settings.default_language_id
    output_path = Path(args.output)

    print("Connecting to Google Ads and collecting reports...", flush=True)
    google_client = build_google_ads_client(settings)
    collector = GoogleAdsDataCollector(
        google_client, customer_id=customer_id, limit=args.limit
    )
    google_ads_data = collector.collect(
        date_range=date_range,
        keyword_seeds=args.keyword_seed,
        location_ids=location_ids,
        language_id=language_id,
    )

    serpapi_data = {"status": "skipped", "reason": "Skipped by --skip-serpapi."}
    if not args.skip_serpapi:
        serp_keywords = _keywords_for_serpapi(google_ads_data, args.keyword_seed)
        print(
            f"Collecting competitor SERP signals for {len(serp_keywords)} queries...",
            flush=True,
        )
        serpapi_data = SerpApiCollector(settings).collect(serp_keywords)

    collected_data = {
        "google_ads": google_ads_data,
        "serpapi": serpapi_data,
    }
    if args.save_input_snapshot:
        snapshot_path = output_path.with_suffix(".input.json")
        write_json(snapshot_path, collected_data)
        print(f"Saved normalized planner input to {snapshot_path}", flush=True)

    print("Sending normalized data to Anthropic planner...", flush=True)
    planner = AnthropicPlanner(settings)
    execution_plan, usage = planner.generate(collected_data)

    envelope = {
        "schema_version": "1.0",
        "created_at": utc_now_iso(),
        "customer_id": customer_id,
        "date_range": date_range,
        "planner": {
            "provider": "anthropic",
            "model": settings.anthropic_model,
            "max_tokens": settings.anthropic_max_tokens,
            "temperature": settings.anthropic_temperature,
            "usage": usage,
        },
        "data_sources": summarize_data_sources(collected_data),
        "execution_plan": execution_plan,
    }
    write_json(output_path, envelope)

    actions = execution_plan.get("actions", [])
    unsupported = execution_plan.get("unsupported_recommendations", [])
    print(f"Wrote plan to {output_path}")
    print(f"Proposed actions: {len(actions)}")
    print(f"Unsupported/manual recommendations: {len(unsupported)}")
    print("All proposed actions are marked approved=false until you review the JSON.")
    return 0


def run_apply(args: argparse.Namespace) -> int:
    settings = Settings.from_env(args.env_file)
    plan_path = Path(args.plan)
    plan = read_json(plan_path)
    customer_id = normalize_customer_id(
        args.customer_id
        or plan.get("customer_id")
        or settings.google_ads_customer_id
        or ""
    )
    actions = _plan_actions(plan)
    log_path = (
        Path(args.log_file)
        if args.log_file
        else Path("logs") / f"apply-{utc_now_iso().replace(':', '').replace('+0000', 'Z')}.jsonl"
    )

    print(f"Reading approved actions from {plan_path}", flush=True)
    applier: GoogleAdsPlanApplier | None = None
    if not args.dry_run:
        google_client = build_google_ads_client(settings)
        applier = GoogleAdsPlanApplier(google_client, customer_id)

    counts = {"applied": 0, "validated": 0, "dry_run": 0, "skipped": 0, "failed": 0}
    for action in actions:
        record = {
            "timestamp": utc_now_iso(),
            "plan_file": str(plan_path),
            "action_id": action.get("id"),
            "action_type": action.get("type"),
            "approved": bool(action.get("approved")),
            "dry_run": bool(args.dry_run),
            "validate_only": bool(args.validate_only),
        }

        if not action.get("approved"):
            record.update(
                {
                    "status": "skipped",
                    "reason": "Action is not approved. Set approved=true in the plan file to apply.",
                }
            )
            counts["skipped"] += 1
            write_jsonl(log_path, record)
            continue

        if action.get("type") not in GoogleAdsPlanApplier.SUPPORTED_ACTIONS:
            record.update(
                {
                    "status": "skipped",
                    "reason": f"Unsupported action type: {action.get('type')}",
                }
            )
            counts["skipped"] += 1
            write_jsonl(log_path, record)
            continue

        if args.dry_run:
            record.update({"status": "dry_run", "reason": "No API call was made."})
            counts["dry_run"] += 1
            write_jsonl(log_path, record)
            continue

        try:
            assert applier is not None
            result = applier.apply_action(action, validate_only=args.validate_only)
            record.update(result)
            status = result.get("status", "applied")
            if status in counts:
                counts[status] += 1
            else:
                counts["skipped"] += 1
        except Exception as exc:
            record.update({"status": "failed", "message": str(exc)})
            counts["failed"] += 1
        write_jsonl(log_path, record)

    print(f"Wrote action log to {log_path}")
    print(
        "Summary: "
        + ", ".join(f"{name}={count}" for name, count in counts.items() if count)
    )
    return 1 if counts["failed"] else 0


def _plan_actions(plan: dict[str, Any]) -> list[dict[str, Any]]:
    execution_plan = plan.get("execution_plan", plan)
    actions = execution_plan.get("actions", [])
    if not isinstance(actions, list):
        raise ValueError("Plan file does not contain an execution_plan.actions list")
    return actions


def _keywords_for_serpapi(
    google_ads_data: dict[str, Any], explicit_seeds: list[str]
) -> list[str]:
    keywords: list[str] = []
    keywords.extend(explicit_seeds)

    for row in google_ads_data.get("reports", {}).get("search_terms", []):
        term = row.get("search_term")
        metrics = row.get("metrics", {})
        if term and (metrics.get("clicks", 0) or metrics.get("cost_micros", 0)):
            keywords.append(term)

    for row in google_ads_data.get("reports", {}).get("keyword_performance", []):
        text = row.get("keyword", {}).get("text")
        metrics = row.get("metrics", {})
        if text and (metrics.get("clicks", 0) or metrics.get("cost_micros", 0)):
            keywords.append(text)

    for idea in google_ads_data.get("keyword_planner", {}).get("ideas", []):
        text = idea.get("text")
        if text:
            keywords.append(text)

    return list(dict.fromkeys(keywords))


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        raise SystemExit(args.func(args))
    except KeyboardInterrupt:
        raise SystemExit(130)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1)
