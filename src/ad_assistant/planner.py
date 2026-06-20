from __future__ import annotations

import json
import re
from copy import deepcopy
from typing import Any

from anthropic import Anthropic

from .settings import Settings


SYSTEM_PROMPT = """You are an expert Google Ads strategist.

You receive normalized account data, Keyword Planner data, auction visibility proxy data,
and current ad copy data from Google Ads. Produce a reviewable execution plan in strict JSON only.

You are not allowed to modify the account. Your only job is analysis and recommendation.
Every account change must be represented as an action object and must include enough
human-readable rationale and evidence for a person to review it.

Supported action types:
- SET_CAMPAIGN_BUDGET
- SET_CAMPAIGN_STATUS
- PAUSE_AD_GROUP
- PAUSE_KEYWORD
- SET_KEYWORD_CPC_BID
- ADD_AD_GROUP_NEGATIVE_KEYWORD
- ADD_CAMPAIGN_NEGATIVE_KEYWORD
- CREATE_RESPONSIVE_SEARCH_AD

Do not invent resource names. Only use resource names present in the input data.
If a useful recommendation cannot be represented by a supported action type, put it in
unsupported_recommendations instead of actions.

Return JSON with this shape:
{
  "summary": "short executive summary",
  "diagnostics": [
    {"theme": "wasted_spend|growth|geo|auction_visibility|creative", "finding": "...", "evidence": "..."}
  ],
  "actions": [
    {
      "id": "act_001",
      "type": "SUPPORTED_ACTION_TYPE",
      "approved": false,
      "risk_level": "low|medium|high",
      "resource": {"...": "resource names and ids required for the action"},
      "change": {"...": "exact change values"},
      "rationale": "why this change is recommended",
      "evidence": [{"source": "report name", "detail": "human readable evidence"}],
      "human_review_notes": "what the reviewer should check before approving"
    }
  ],
  "unsupported_recommendations": [
    {"recommendation": "...", "reason_not_automated": "...", "evidence": "..."}
  ]
}
"""


def _extract_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?", "", stripped, flags=re.IGNORECASE).strip()
        stripped = re.sub(r"```$", "", stripped).strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", stripped, flags=re.DOTALL)
        if not match:
            raise
        return json.loads(match.group(0))


def _force_review_required(plan: dict[str, Any]) -> dict[str, Any]:
    plan = deepcopy(plan)
    actions = plan.get("actions")
    if not isinstance(actions, list):
        plan["actions"] = []
    for index, action in enumerate(plan["actions"], start=1):
        if not action.get("id"):
            action["id"] = f"act_{index:03d}"
        action["approved"] = False
    plan.setdefault("diagnostics", [])
    plan.setdefault("unsupported_recommendations", [])
    plan.setdefault("summary", "")
    return plan


def summarize_data_sources(collected_data: dict[str, Any]) -> dict[str, Any]:
    reports = collected_data.get("google_ads", {}).get("reports", {})
    return {
        "google_ads_reports": {
            name: len(rows) if isinstance(rows, list) else 0 for name, rows in reports.items()
        },
        "keyword_planner": {
            "status": collected_data.get("google_ads", {})
            .get("keyword_planner", {})
            .get("status"),
            "ideas": len(
                collected_data.get("google_ads", {})
                .get("keyword_planner", {})
                .get("ideas", [])
            ),
        },
        "auction_insights": collected_data.get("google_ads", {})
        .get("auction_insights", {})
        .get("status"),
        "availability_notes": collected_data.get("google_ads", {}).get(
            "availability_notes", []
        ),
    }


class AnthropicPlanner:
    def __init__(self, settings: Settings) -> None:
        settings.require_anthropic()
        self.settings = settings
        self.client = Anthropic(api_key=settings.anthropic_api_key)

    def generate(self, collected_data: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        planner_input = self._trim_input(collected_data)
        response = self.client.messages.create(
            model=self.settings.anthropic_model,
            max_tokens=self.settings.anthropic_max_tokens,
            temperature=self.settings.anthropic_temperature,
            system=SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Analyze this data and produce the JSON execution plan only:\n"
                        + json.dumps(planner_input, indent=2, sort_keys=False)
                    ),
                }
            ],
        )
        text = "".join(
            block.text for block in response.content if getattr(block, "type", None) == "text"
        )
        parsed = _force_review_required(_extract_json_object(text))
        usage = {
            "input_tokens": getattr(response.usage, "input_tokens", None),
            "output_tokens": getattr(response.usage, "output_tokens", None),
        }
        return parsed, usage

    def _trim_input(self, collected_data: dict[str, Any]) -> dict[str, Any]:
        payload = deepcopy(collected_data)
        max_chars = self.settings.anthropic_max_input_chars
        serialized = json.dumps(payload, sort_keys=False)
        if len(serialized) <= max_chars:
            return payload

        reports = payload.get("google_ads", {}).get("reports", {})
        trim_order = [
            ("ad_copy", 30),
            ("search_terms", 60),
            ("keyword_performance", 60),
            ("geographic_performance", 60),
            ("ad_group_performance", 60),
            ("campaign_performance", 60),
        ]
        for report_name, keep in trim_order:
            if isinstance(reports.get(report_name), list):
                reports[report_name] = reports[report_name][:keep]
            serialized = json.dumps(payload, sort_keys=False)
            if len(serialized) <= max_chars:
                return payload

        keyword_planner = payload.get("google_ads", {}).get("keyword_planner", {})
        if isinstance(keyword_planner.get("ideas"), list):
            keyword_planner["ideas"] = keyword_planner["ideas"][:50]

        payload["truncation_note"] = (
            f"Input exceeded ANTHROPIC_MAX_INPUT_CHARS={max_chars}; lower-priority "
            "rows were trimmed before planning."
        )
        return payload
