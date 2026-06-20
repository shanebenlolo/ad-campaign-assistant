from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .settings import Settings
from .utils import as_list, enum_name, gaql_date_filter, micros_to_currency, normalize_customer_id


def _enum_value(client: Any, enum_container_name: str, value: str) -> Any:
    container = getattr(client.enums, enum_container_name)
    normalized = str(value).strip().upper()
    direct = getattr(container, normalized, None)
    if direct is not None:
        return direct

    nested_name = enum_container_name.removesuffix("Enum")
    nested = getattr(container, nested_name, None)
    nested_value = getattr(nested, normalized, None) if nested is not None else None
    if nested_value is not None:
        return nested_value

    for attr_name in dir(container):
        attr = getattr(container, attr_name)
        nested_value = getattr(attr, normalized, None)
        if nested_value is not None:
            return nested_value

    raise AttributeError(f"{enum_container_name} has no value {normalized}")


def build_google_ads_client(settings: Settings):
    settings.require_google_ads()
    from google.ads.googleads.client import GoogleAdsClient

    config: dict[str, Any] = {
        "developer_token": settings.google_ads_developer_token,
        "client_id": settings.google_ads_client_id,
        "client_secret": settings.google_ads_client_secret,
        "refresh_token": settings.google_ads_refresh_token,
        "use_proto_plus": settings.google_ads_use_proto_plus,
    }
    if settings.google_ads_login_customer_id:
        config["login_customer_id"] = normalize_customer_id(
            settings.google_ads_login_customer_id
        )

    version = settings.google_ads_api_version or None
    return GoogleAdsClient.load_from_dict(config, version=version)


def _repeated_text_assets(assets: Any) -> list[str]:
    texts: list[str] = []
    for asset in as_list(assets):
        text = getattr(asset, "text", None)
        if text:
            texts.append(str(text))
    return texts


def _final_urls(value: Any) -> list[str]:
    return [str(item) for item in as_list(value) if str(item)]


def _metrics_dict(metrics: Any) -> dict[str, Any]:
    cost_micros = int(getattr(metrics, "cost_micros", 0) or 0)
    return {
        "impressions": int(getattr(metrics, "impressions", 0) or 0),
        "clicks": int(getattr(metrics, "clicks", 0) or 0),
        "cost_micros": cost_micros,
        "cost": micros_to_currency(cost_micros),
        "conversions": float(getattr(metrics, "conversions", 0.0) or 0.0),
        "conversions_value": float(getattr(metrics, "conversions_value", 0.0) or 0.0),
        "ctr": float(getattr(metrics, "ctr", 0.0) or 0.0),
        "average_cpc_micros": int(getattr(metrics, "average_cpc", 0) or 0),
        "cost_per_conversion_micros": int(
            getattr(metrics, "cost_per_conversion", 0) or 0
        ),
        "search_impression_share": float(
            getattr(metrics, "search_impression_share", 0.0) or 0.0
        ),
        "search_budget_lost_impression_share": float(
            getattr(metrics, "search_budget_lost_impression_share", 0.0) or 0.0
        ),
        "search_rank_lost_impression_share": float(
            getattr(metrics, "search_rank_lost_impression_share", 0.0) or 0.0
        ),
        "search_top_impression_share": float(
            getattr(metrics, "search_top_impression_share", 0.0) or 0.0
        ),
        "search_absolute_top_impression_share": float(
            getattr(metrics, "search_absolute_top_impression_share", 0.0) or 0.0
        ),
    }


@dataclass(frozen=True)
class QueryResult:
    name: str
    rows: list[dict[str, Any]]
    error: str | None = None


class GoogleAdsDataCollector:
    def __init__(self, client: Any, customer_id: str, limit: int = 100) -> None:
        self.client = client
        self.customer_id = normalize_customer_id(customer_id)
        self.limit = limit
        self.google_ads_service = client.get_service("GoogleAdsService")

    def collect(
        self,
        date_range: str,
        keyword_seeds: list[str],
        location_ids: list[str],
        language_id: str,
    ) -> dict[str, Any]:
        data: dict[str, Any] = {
            "customer_id": self.customer_id,
            "date_range": date_range,
            "reports": {},
            "keyword_planner": {},
            "auction_insights": {},
            "availability_notes": [],
        }

        report_collectors = [
            ("campaign_performance", self.collect_campaign_performance),
            ("ad_group_performance", self.collect_ad_group_performance),
            ("keyword_performance", self.collect_keyword_performance),
            ("geographic_performance", self.collect_geographic_performance),
            ("search_terms", self.collect_search_terms),
            ("ad_copy", self.collect_ad_copy),
        ]

        for name, collector in report_collectors:
            result = collector(date_range)
            data["reports"][name] = result.rows
            if result.error:
                data["availability_notes"].append(
                    {"source": name, "level": "warning", "message": result.error}
                )

        planner_seeds = keyword_seeds or self._top_seed_terms(data)
        data["keyword_planner"] = self.collect_keyword_planner(
            planner_seeds, location_ids, language_id
        )
        data["auction_insights"] = self.collect_auction_insights_proxy(data)
        return data

    def _top_seed_terms(self, data: dict[str, Any]) -> list[str]:
        candidates: list[tuple[float, str]] = []
        for row in data.get("reports", {}).get("search_terms", []):
            term = row.get("search_term")
            metrics = row.get("metrics", {})
            if term:
                score = float(metrics.get("conversions") or 0) * 1000 + float(
                    metrics.get("clicks") or 0
                )
                candidates.append((score, term))
        for row in data.get("reports", {}).get("keyword_performance", []):
            keyword = row.get("keyword", {}).get("text")
            metrics = row.get("metrics", {})
            if keyword:
                score = float(metrics.get("conversions") or 0) * 1000 + float(
                    metrics.get("clicks") or 0
                )
                candidates.append((score, keyword))
        return [term for _, term in sorted(candidates, reverse=True)[:20]]

    def _search(self, query: str, mapper) -> QueryResult:
        rows: list[dict[str, Any]] = []
        try:
            stream = self.google_ads_service.search_stream(
                customer_id=self.customer_id, query=query
            )
            for batch in stream:
                for row in batch.results:
                    rows.append(mapper(row))
        except Exception as exc:  # Google Ads exceptions include nested request errors.
            return QueryResult(name="unknown", rows=rows, error=str(exc))
        return QueryResult(name="unknown", rows=rows)

    def collect_campaign_performance(self, date_range: str) -> QueryResult:
        date_filter = gaql_date_filter(date_range)
        query = f"""
            SELECT
              campaign.resource_name,
              campaign.id,
              campaign.name,
              campaign.status,
              campaign.advertising_channel_type,
              campaign.bidding_strategy_type,
              campaign_budget.resource_name,
              campaign_budget.id,
              campaign_budget.name,
              campaign_budget.amount_micros,
              metrics.impressions,
              metrics.clicks,
              metrics.cost_micros,
              metrics.conversions,
              metrics.conversions_value,
              metrics.ctr,
              metrics.average_cpc,
              metrics.cost_per_conversion,
              metrics.search_impression_share,
              metrics.search_budget_lost_impression_share,
              metrics.search_rank_lost_impression_share,
              metrics.search_top_impression_share,
              metrics.search_absolute_top_impression_share
            FROM campaign
            WHERE {date_filter}
              AND campaign.status != REMOVED
            ORDER BY metrics.cost_micros DESC
            LIMIT {self.limit}
        """

        def mapper(row: Any) -> dict[str, Any]:
            return {
                "campaign": {
                    "resource_name": row.campaign.resource_name,
                    "id": str(row.campaign.id),
                    "name": row.campaign.name,
                    "status": enum_name(row.campaign.status),
                    "advertising_channel_type": enum_name(
                        row.campaign.advertising_channel_type
                    ),
                    "bidding_strategy_type": enum_name(row.campaign.bidding_strategy_type),
                },
                "budget": {
                    "resource_name": row.campaign_budget.resource_name,
                    "id": str(row.campaign_budget.id),
                    "name": row.campaign_budget.name,
                    "amount_micros": int(row.campaign_budget.amount_micros or 0),
                    "amount": micros_to_currency(row.campaign_budget.amount_micros),
                },
                "metrics": _metrics_dict(row.metrics),
            }

        result = self._search(query, mapper)
        return QueryResult("campaign_performance", result.rows, result.error)

    def collect_ad_group_performance(self, date_range: str) -> QueryResult:
        date_filter = gaql_date_filter(date_range)
        query = f"""
            SELECT
              campaign.resource_name,
              campaign.id,
              campaign.name,
              ad_group.resource_name,
              ad_group.id,
              ad_group.name,
              ad_group.status,
              ad_group.cpc_bid_micros,
              metrics.impressions,
              metrics.clicks,
              metrics.cost_micros,
              metrics.conversions,
              metrics.conversions_value,
              metrics.ctr,
              metrics.average_cpc,
              metrics.cost_per_conversion
            FROM ad_group
            WHERE {date_filter}
              AND ad_group.status != REMOVED
            ORDER BY metrics.cost_micros DESC
            LIMIT {self.limit}
        """

        def mapper(row: Any) -> dict[str, Any]:
            return {
                "campaign": {
                    "resource_name": row.campaign.resource_name,
                    "id": str(row.campaign.id),
                    "name": row.campaign.name,
                },
                "ad_group": {
                    "resource_name": row.ad_group.resource_name,
                    "id": str(row.ad_group.id),
                    "name": row.ad_group.name,
                    "status": enum_name(row.ad_group.status),
                    "cpc_bid_micros": int(row.ad_group.cpc_bid_micros or 0),
                },
                "metrics": _metrics_dict(row.metrics),
            }

        result = self._search(query, mapper)
        return QueryResult("ad_group_performance", result.rows, result.error)

    def collect_keyword_performance(self, date_range: str) -> QueryResult:
        date_filter = gaql_date_filter(date_range)
        query = f"""
            SELECT
              campaign.resource_name,
              campaign.id,
              campaign.name,
              ad_group.resource_name,
              ad_group.id,
              ad_group.name,
              ad_group_criterion.resource_name,
              ad_group_criterion.criterion_id,
              ad_group_criterion.status,
              ad_group_criterion.negative,
              ad_group_criterion.cpc_bid_micros,
              ad_group_criterion.keyword.text,
              ad_group_criterion.keyword.match_type,
              metrics.impressions,
              metrics.clicks,
              metrics.cost_micros,
              metrics.conversions,
              metrics.conversions_value,
              metrics.ctr,
              metrics.average_cpc,
              metrics.cost_per_conversion,
              metrics.search_impression_share,
              metrics.search_rank_lost_impression_share,
              metrics.search_top_impression_share,
              metrics.search_absolute_top_impression_share
            FROM keyword_view
            WHERE {date_filter}
              AND ad_group_criterion.type = KEYWORD
              AND ad_group_criterion.status != REMOVED
            ORDER BY metrics.cost_micros DESC
            LIMIT {self.limit}
        """

        def mapper(row: Any) -> dict[str, Any]:
            return {
                "campaign": {
                    "resource_name": row.campaign.resource_name,
                    "id": str(row.campaign.id),
                    "name": row.campaign.name,
                },
                "ad_group": {
                    "resource_name": row.ad_group.resource_name,
                    "id": str(row.ad_group.id),
                    "name": row.ad_group.name,
                },
                "criterion": {
                    "resource_name": row.ad_group_criterion.resource_name,
                    "criterion_id": str(row.ad_group_criterion.criterion_id),
                    "status": enum_name(row.ad_group_criterion.status),
                    "negative": bool(row.ad_group_criterion.negative),
                    "cpc_bid_micros": int(row.ad_group_criterion.cpc_bid_micros or 0),
                },
                "keyword": {
                    "text": row.ad_group_criterion.keyword.text,
                    "match_type": enum_name(row.ad_group_criterion.keyword.match_type),
                },
                "metrics": _metrics_dict(row.metrics),
            }

        result = self._search(query, mapper)
        return QueryResult("keyword_performance", result.rows, result.error)

    def collect_geographic_performance(self, date_range: str) -> QueryResult:
        date_filter = gaql_date_filter(date_range)
        query = f"""
            SELECT
              campaign.resource_name,
              campaign.id,
              campaign.name,
              geographic_view.country_criterion_id,
              geographic_view.location_type,
              segments.geo_target_country,
              segments.geo_target_region,
              segments.geo_target_city,
              metrics.impressions,
              metrics.clicks,
              metrics.cost_micros,
              metrics.conversions,
              metrics.conversions_value,
              metrics.ctr,
              metrics.average_cpc,
              metrics.cost_per_conversion
            FROM geographic_view
            WHERE {date_filter}
            ORDER BY metrics.cost_micros DESC
            LIMIT {self.limit}
        """

        def mapper(row: Any) -> dict[str, Any]:
            return {
                "campaign": {
                    "resource_name": row.campaign.resource_name,
                    "id": str(row.campaign.id),
                    "name": row.campaign.name,
                },
                "geo": {
                    "country_criterion_id": str(
                        row.geographic_view.country_criterion_id
                    ),
                    "location_type": enum_name(row.geographic_view.location_type),
                    "country": row.segments.geo_target_country,
                    "region": row.segments.geo_target_region,
                    "city": row.segments.geo_target_city,
                },
                "metrics": _metrics_dict(row.metrics),
            }

        result = self._search(query, mapper)
        return QueryResult("geographic_performance", result.rows, result.error)

    def collect_search_terms(self, date_range: str) -> QueryResult:
        date_filter = gaql_date_filter(date_range)
        query = f"""
            SELECT
              campaign.resource_name,
              campaign.id,
              campaign.name,
              ad_group.resource_name,
              ad_group.id,
              ad_group.name,
              search_term_view.resource_name,
              search_term_view.search_term,
              search_term_view.status,
              segments.keyword.info.text,
              segments.keyword.info.match_type,
              metrics.impressions,
              metrics.clicks,
              metrics.cost_micros,
              metrics.conversions,
              metrics.conversions_value,
              metrics.ctr,
              metrics.average_cpc,
              metrics.cost_per_conversion
            FROM search_term_view
            WHERE {date_filter}
            ORDER BY metrics.cost_micros DESC
            LIMIT {self.limit}
        """

        def mapper(row: Any) -> dict[str, Any]:
            return {
                "campaign": {
                    "resource_name": row.campaign.resource_name,
                    "id": str(row.campaign.id),
                    "name": row.campaign.name,
                },
                "ad_group": {
                    "resource_name": row.ad_group.resource_name,
                    "id": str(row.ad_group.id),
                    "name": row.ad_group.name,
                },
                "resource_name": row.search_term_view.resource_name,
                "search_term": row.search_term_view.search_term,
                "status": enum_name(row.search_term_view.status),
                "matched_keyword": {
                    "text": row.segments.keyword.info.text,
                    "match_type": enum_name(row.segments.keyword.info.match_type),
                },
                "metrics": _metrics_dict(row.metrics),
            }

        result = self._search(query, mapper)
        return QueryResult("search_terms", result.rows, result.error)

    def collect_ad_copy(self, date_range: str) -> QueryResult:
        date_filter = gaql_date_filter(date_range)
        query = f"""
            SELECT
              campaign.resource_name,
              campaign.id,
              campaign.name,
              ad_group.resource_name,
              ad_group.id,
              ad_group.name,
              ad_group_ad.resource_name,
              ad_group_ad.status,
              ad_group_ad.ad.id,
              ad_group_ad.ad.type,
              ad_group_ad.ad.final_urls,
              ad_group_ad.ad.responsive_search_ad.headlines,
              ad_group_ad.ad.responsive_search_ad.descriptions,
              metrics.impressions,
              metrics.clicks,
              metrics.cost_micros,
              metrics.conversions,
              metrics.conversions_value,
              metrics.ctr
            FROM ad_group_ad
            WHERE {date_filter}
              AND ad_group_ad.status != REMOVED
            ORDER BY metrics.impressions DESC
            LIMIT {self.limit}
        """

        def mapper(row: Any) -> dict[str, Any]:
            ad = row.ad_group_ad.ad
            rsa = ad.responsive_search_ad
            return {
                "campaign": {
                    "resource_name": row.campaign.resource_name,
                    "id": str(row.campaign.id),
                    "name": row.campaign.name,
                },
                "ad_group": {
                    "resource_name": row.ad_group.resource_name,
                    "id": str(row.ad_group.id),
                    "name": row.ad_group.name,
                },
                "ad": {
                    "resource_name": row.ad_group_ad.resource_name,
                    "id": str(ad.id),
                    "status": enum_name(row.ad_group_ad.status),
                    "type": enum_name(ad.type),
                    "final_urls": _final_urls(ad.final_urls),
                    "headlines": _repeated_text_assets(rsa.headlines),
                    "descriptions": _repeated_text_assets(rsa.descriptions),
                },
                "metrics": _metrics_dict(row.metrics),
            }

        result = self._search(query, mapper)
        return QueryResult("ad_copy", result.rows, result.error)

    def collect_keyword_planner(
        self, keyword_seeds: list[str], location_ids: list[str], language_id: str
    ) -> dict[str, Any]:
        if not keyword_seeds:
            return {
                "status": "skipped",
                "reason": "No keyword seeds were available from CLI or account data.",
                "ideas": [],
            }

        try:
            service = self.client.get_service("KeywordPlanIdeaService")
            geo_service = self.client.get_service("GeoTargetConstantService")
            language_service = self.client.get_service("LanguageConstantService")
            request = self.client.get_type("GenerateKeywordIdeasRequest")
            request.customer_id = self.customer_id
            request.language = language_service.language_constant_path(language_id)
            for location_id in location_ids:
                request.geo_target_constants.append(
                    geo_service.geo_target_constant_path(location_id)
                )
            request.keyword_plan_network = _enum_value(
                self.client,
                "KeywordPlanNetworkEnum",
                "GOOGLE_SEARCH_AND_PARTNERS",
            )
            request.keyword_seed.keywords.extend(keyword_seeds[:20])

            ideas: list[dict[str, Any]] = []
            for idea in service.generate_keyword_ideas(request=request):
                metrics = idea.keyword_idea_metrics
                ideas.append(
                    {
                        "text": idea.text,
                        "avg_monthly_searches": int(
                            metrics.avg_monthly_searches or 0
                        ),
                        "competition": enum_name(metrics.competition),
                        "competition_index": int(metrics.competition_index or 0),
                        "low_top_of_page_bid_micros": int(
                            metrics.low_top_of_page_bid_micros or 0
                        ),
                        "high_top_of_page_bid_micros": int(
                            metrics.high_top_of_page_bid_micros or 0
                        ),
                    }
                )
                if len(ideas) >= self.limit:
                    break
            return {
                "status": "ok",
                "seed_keywords": keyword_seeds[:20],
                "location_ids": location_ids,
                "language_id": language_id,
                "ideas": ideas,
            }
        except Exception as exc:
            return {
                "status": "error",
                "message": str(exc),
                "seed_keywords": keyword_seeds[:20],
                "ideas": [],
            }

    def collect_auction_insights_proxy(self, data: dict[str, Any]) -> dict[str, Any]:
        campaign_rows = data.get("reports", {}).get("campaign_performance", [])
        keyword_rows = data.get("reports", {}).get("keyword_performance", [])
        return {
            "status": "limited",
            "source": "Google Ads API search impression-share metrics",
            "note": (
                "The public Google Ads API does not expose every Auction Insights UI "
                "field. This app captures available impression share, top impression "
                "share, absolute top impression share, and rank/budget lost share as "
                "auction-visibility proxies for the AI planner."
            ),
            "campaign_visibility": [
                {
                    "campaign": row.get("campaign", {}),
                    "metrics": {
                        key: row.get("metrics", {}).get(key)
                        for key in [
                            "search_impression_share",
                            "search_budget_lost_impression_share",
                            "search_rank_lost_impression_share",
                            "search_top_impression_share",
                            "search_absolute_top_impression_share",
                        ]
                    },
                }
                for row in campaign_rows
            ],
            "keyword_visibility": [
                {
                    "campaign": row.get("campaign", {}),
                    "ad_group": row.get("ad_group", {}),
                    "keyword": row.get("keyword", {}),
                    "criterion": row.get("criterion", {}),
                    "metrics": {
                        key: row.get("metrics", {}).get(key)
                        for key in [
                            "search_impression_share",
                            "search_rank_lost_impression_share",
                            "search_top_impression_share",
                            "search_absolute_top_impression_share",
                        ]
                    },
                }
                for row in keyword_rows[: self.limit]
            ],
        }


class GoogleAdsPlanApplier:
    SUPPORTED_ACTIONS = {
        "SET_CAMPAIGN_BUDGET",
        "SET_CAMPAIGN_STATUS",
        "PAUSE_AD_GROUP",
        "PAUSE_KEYWORD",
        "SET_KEYWORD_CPC_BID",
        "ADD_AD_GROUP_NEGATIVE_KEYWORD",
        "ADD_CAMPAIGN_NEGATIVE_KEYWORD",
        "CREATE_RESPONSIVE_SEARCH_AD",
    }

    def __init__(self, client: Any, customer_id: str) -> None:
        self.client = client
        self.customer_id = normalize_customer_id(customer_id)

    def apply_action(self, action: dict[str, Any], validate_only: bool = False) -> dict[str, Any]:
        action_type = action.get("type")
        if action_type not in self.SUPPORTED_ACTIONS:
            return {
                "status": "skipped",
                "reason": f"Unsupported action type: {action_type}",
            }

        dispatch = {
            "SET_CAMPAIGN_BUDGET": self._set_campaign_budget,
            "SET_CAMPAIGN_STATUS": self._set_campaign_status,
            "PAUSE_AD_GROUP": self._pause_ad_group,
            "PAUSE_KEYWORD": self._pause_keyword,
            "SET_KEYWORD_CPC_BID": self._set_keyword_cpc_bid,
            "ADD_AD_GROUP_NEGATIVE_KEYWORD": self._add_ad_group_negative_keyword,
            "ADD_CAMPAIGN_NEGATIVE_KEYWORD": self._add_campaign_negative_keyword,
            "CREATE_RESPONSIVE_SEARCH_AD": self._create_responsive_search_ad,
        }
        return dispatch[action_type](action, validate_only=validate_only)

    def _enum(self, enum_name_: str, value: str) -> Any:
        return _enum_value(self.client, enum_name_, value)

    def _set_campaign_budget(
        self, action: dict[str, Any], validate_only: bool = False
    ) -> dict[str, Any]:
        resource = action.get("resource", {})
        change = action.get("change", {})
        budget_resource = resource["campaign_budget_resource_name"]
        amount_micros = int(change["amount_micros"])

        service = self.client.get_service("CampaignBudgetService")
        operation = self.client.get_type("CampaignBudgetOperation")
        budget = operation.update
        budget.resource_name = budget_resource
        budget.amount_micros = amount_micros
        operation.update_mask.paths.append("amount_micros")
        response = service.mutate_campaign_budgets(
            customer_id=self.customer_id,
            operations=[operation],
            validate_only=validate_only,
        )
        return {
            "status": "validated" if validate_only else "applied",
            "resource_names": [item.resource_name for item in response.results],
        }

    def _set_campaign_status(
        self, action: dict[str, Any], validate_only: bool = False
    ) -> dict[str, Any]:
        resource = action.get("resource", {})
        change = action.get("change", {})
        service = self.client.get_service("CampaignService")
        operation = self.client.get_type("CampaignOperation")
        campaign = operation.update
        campaign.resource_name = resource["campaign_resource_name"]
        campaign.status = self._enum("CampaignStatusEnum", change["status"])
        operation.update_mask.paths.append("status")
        response = service.mutate_campaigns(
            customer_id=self.customer_id,
            operations=[operation],
            validate_only=validate_only,
        )
        return {
            "status": "validated" if validate_only else "applied",
            "resource_names": [item.resource_name for item in response.results],
        }

    def _pause_ad_group(
        self, action: dict[str, Any], validate_only: bool = False
    ) -> dict[str, Any]:
        resource = action.get("resource", {})
        service = self.client.get_service("AdGroupService")
        operation = self.client.get_type("AdGroupOperation")
        ad_group = operation.update
        ad_group.resource_name = resource["ad_group_resource_name"]
        ad_group.status = self._enum("AdGroupStatusEnum", "PAUSED")
        operation.update_mask.paths.append("status")
        response = service.mutate_ad_groups(
            customer_id=self.customer_id,
            operations=[operation],
            validate_only=validate_only,
        )
        return {
            "status": "validated" if validate_only else "applied",
            "resource_names": [item.resource_name for item in response.results],
        }

    def _pause_keyword(
        self, action: dict[str, Any], validate_only: bool = False
    ) -> dict[str, Any]:
        resource = action.get("resource", {})
        service = self.client.get_service("AdGroupCriterionService")
        operation = self.client.get_type("AdGroupCriterionOperation")
        criterion = operation.update
        criterion.resource_name = resource["ad_group_criterion_resource_name"]
        criterion.status = self._enum("AdGroupCriterionStatusEnum", "PAUSED")
        operation.update_mask.paths.append("status")
        response = service.mutate_ad_group_criteria(
            customer_id=self.customer_id,
            operations=[operation],
            validate_only=validate_only,
        )
        return {
            "status": "validated" if validate_only else "applied",
            "resource_names": [item.resource_name for item in response.results],
        }

    def _set_keyword_cpc_bid(
        self, action: dict[str, Any], validate_only: bool = False
    ) -> dict[str, Any]:
        resource = action.get("resource", {})
        change = action.get("change", {})
        service = self.client.get_service("AdGroupCriterionService")
        operation = self.client.get_type("AdGroupCriterionOperation")
        criterion = operation.update
        criterion.resource_name = resource["ad_group_criterion_resource_name"]
        criterion.cpc_bid_micros = int(change["cpc_bid_micros"])
        operation.update_mask.paths.append("cpc_bid_micros")
        response = service.mutate_ad_group_criteria(
            customer_id=self.customer_id,
            operations=[operation],
            validate_only=validate_only,
        )
        return {
            "status": "validated" if validate_only else "applied",
            "resource_names": [item.resource_name for item in response.results],
        }

    def _add_ad_group_negative_keyword(
        self, action: dict[str, Any], validate_only: bool = False
    ) -> dict[str, Any]:
        resource = action.get("resource", {})
        change = action.get("change", {})
        service = self.client.get_service("AdGroupCriterionService")
        operation = self.client.get_type("AdGroupCriterionOperation")
        criterion = operation.create
        criterion.ad_group = resource["ad_group_resource_name"]
        criterion.status = self._enum("AdGroupCriterionStatusEnum", "ENABLED")
        criterion.negative = True
        criterion.keyword.text = change["text"]
        criterion.keyword.match_type = self._enum(
            "KeywordMatchTypeEnum", change.get("match_type", "PHRASE")
        )
        response = service.mutate_ad_group_criteria(
            customer_id=self.customer_id,
            operations=[operation],
            validate_only=validate_only,
        )
        return {
            "status": "validated" if validate_only else "applied",
            "resource_names": [item.resource_name for item in response.results],
        }

    def _add_campaign_negative_keyword(
        self, action: dict[str, Any], validate_only: bool = False
    ) -> dict[str, Any]:
        resource = action.get("resource", {})
        change = action.get("change", {})
        service = self.client.get_service("CampaignCriterionService")
        operation = self.client.get_type("CampaignCriterionOperation")
        criterion = operation.create
        criterion.campaign = resource["campaign_resource_name"]
        criterion.negative = True
        criterion.keyword.text = change["text"]
        criterion.keyword.match_type = self._enum(
            "KeywordMatchTypeEnum", change.get("match_type", "PHRASE")
        )
        response = service.mutate_campaign_criteria(
            customer_id=self.customer_id,
            operations=[operation],
            validate_only=validate_only,
        )
        return {
            "status": "validated" if validate_only else "applied",
            "resource_names": [item.resource_name for item in response.results],
        }

    def _create_responsive_search_ad(
        self, action: dict[str, Any], validate_only: bool = False
    ) -> dict[str, Any]:
        resource = action.get("resource", {})
        change = action.get("change", {})
        service = self.client.get_service("AdGroupAdService")
        operation = self.client.get_type("AdGroupAdOperation")
        ad_group_ad = operation.create
        ad_group_ad.ad_group = resource["ad_group_resource_name"]
        ad_group_ad.status = self._enum(
            "AdGroupAdStatusEnum", change.get("status", "PAUSED")
        )
        ad = ad_group_ad.ad
        ad.final_urls.extend(change["final_urls"])
        for text in change["headlines"]:
            asset = self.client.get_type("AdTextAsset")
            asset.text = text
            ad.responsive_search_ad.headlines.append(asset)
        for text in change["descriptions"]:
            asset = self.client.get_type("AdTextAsset")
            asset.text = text
            ad.responsive_search_ad.descriptions.append(asset)
        response = service.mutate_ad_group_ads(
            customer_id=self.customer_id,
            operations=[operation],
            validate_only=validate_only,
        )
        return {
            "status": "validated" if validate_only else "applied",
            "resource_names": [item.resource_name for item in response.results],
        }
