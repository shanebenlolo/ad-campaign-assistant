from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

import requests

from .settings import Settings
from .utils import compact_domain


class SerpApiCollector:
    def __init__(self, settings: Settings, timeout_seconds: int = 30) -> None:
        self.settings = settings
        self.timeout_seconds = timeout_seconds

    def collect(self, keywords: list[str]) -> dict[str, Any]:
        if not self.settings.serpapi_api_key:
            return {
                "status": "skipped",
                "reason": "SERPAPI_API_KEY is not configured.",
                "search_results": [],
                "keyword_overlap": [],
                "transparency_center": [],
            }

        limited_keywords = list(dict.fromkeys(keywords))[: self.settings.serpapi_max_queries]
        search_results = [self._collect_google_search(keyword) for keyword in limited_keywords]
        transparency = self._collect_transparency_center()
        return {
            "status": "ok",
            "search_results": search_results,
            "keyword_overlap": self._keyword_overlap(search_results),
            "transparency_center": transparency,
        }

    def _base_params(self) -> dict[str, Any]:
        params: dict[str, Any] = {
            "api_key": self.settings.serpapi_api_key,
            "google_domain": self.settings.serpapi_google_domain,
            "gl": self.settings.serpapi_gl,
            "hl": self.settings.serpapi_hl,
            "device": self.settings.serpapi_device,
        }
        if self.settings.serpapi_location:
            params["location"] = self.settings.serpapi_location
        return params

    def _collect_google_search(self, keyword: str) -> dict[str, Any]:
        params = self._base_params()
        params.update({"engine": "google", "q": keyword})
        try:
            response = requests.get(
                "https://serpapi.com/search.json",
                params=params,
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            return {"keyword": keyword, "status": "error", "message": str(exc)}

        ad_results = []
        for block_name in ["ads", "top_ads", "bottom_ads"]:
            for index, ad in enumerate(payload.get(block_name, []) or [], start=1):
                link = ad.get("link") or ad.get("tracking_link") or ad.get("displayed_link")
                ad_results.append(
                    {
                        "keyword": keyword,
                        "block": block_name,
                        "position": ad.get("position") or index,
                        "title": ad.get("title"),
                        "snippet": ad.get("snippet") or ad.get("description"),
                        "link": link,
                        "displayed_link": ad.get("displayed_link"),
                        "domain": compact_domain(link or ad.get("displayed_link")),
                    }
                )

        organic_results = []
        for index, result in enumerate(payload.get("organic_results", []) or [], start=1):
            link = result.get("link")
            organic_results.append(
                {
                    "keyword": keyword,
                    "position": result.get("position") or index,
                    "title": result.get("title"),
                    "snippet": result.get("snippet"),
                    "link": link,
                    "domain": compact_domain(link),
                }
            )

        return {
            "keyword": keyword,
            "status": "ok",
            "ads": ad_results,
            "organic_results": organic_results[:10],
            "search_metadata": {
                "id": payload.get("search_metadata", {}).get("id"),
                "google_url": payload.get("search_metadata", {}).get("google_url"),
            },
        }

    def _collect_transparency_center(self) -> list[dict[str, Any]]:
        lookups: list[dict[str, str]] = []
        for advertiser_id in self.settings.serpapi_advertiser_ids:
            lookups.append({"advertiser_id": advertiser_id})
        if self.settings.serpapi_transparency_text:
            lookups.append({"text": self.settings.serpapi_transparency_text})

        results: list[dict[str, Any]] = []
        for lookup in lookups:
            params: dict[str, Any] = {
                "engine": "google_ads_transparency_center",
                "api_key": self.settings.serpapi_api_key,
                **lookup,
            }
            if self.settings.serpapi_transparency_region:
                params["region"] = self.settings.serpapi_transparency_region
            try:
                response = requests.get(
                    "https://serpapi.com/search.json",
                    params=params,
                    timeout=self.timeout_seconds,
                )
                response.raise_for_status()
                payload = response.json()
                results.append(
                    {
                        "status": "ok",
                        "lookup": lookup,
                        "ads": self._normalize_transparency_ads(payload),
                    }
                )
            except Exception as exc:
                results.append({"status": "error", "lookup": lookup, "message": str(exc)})
        return results

    def _normalize_transparency_ads(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        raw_ads = (
            payload.get("ad_creatives")
            or payload.get("ads")
            or payload.get("advertiser_ad_creatives")
            or []
        )
        normalized = []
        for ad in raw_ads[:20]:
            link = ad.get("link") or ad.get("target_url") or ad.get("landing_page_url")
            normalized.append(
                {
                    "title": ad.get("title") or ad.get("headline"),
                    "body": ad.get("body") or ad.get("description") or ad.get("text"),
                    "link": link,
                    "domain": compact_domain(link),
                    "format": ad.get("format"),
                    "first_shown": ad.get("first_shown"),
                    "last_shown": ad.get("last_shown"),
                    "raw_id": ad.get("id") or ad.get("creative_id"),
                }
            )
        return normalized

    def _keyword_overlap(self, search_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
        competitor_domains = set(self.settings.serpapi_competitor_domains)
        business_domain = compact_domain(self.settings.business_domain)
        domain_counter: Counter[str] = Counter()
        keyword_map: dict[str, set[str]] = defaultdict(set)
        ad_copy_by_domain: dict[str, list[dict[str, Any]]] = defaultdict(list)

        for result in search_results:
            keyword = result.get("keyword")
            for ad in result.get("ads", []):
                domain = compact_domain(ad.get("domain"))
                if not domain or domain == business_domain:
                    continue
                if competitor_domains and domain not in competitor_domains:
                    continue
                domain_counter[domain] += 1
                keyword_map[domain].add(keyword)
                ad_copy_by_domain[domain].append(
                    {
                        "keyword": keyword,
                        "position": ad.get("position"),
                        "title": ad.get("title"),
                        "snippet": ad.get("snippet"),
                        "landing_page": ad.get("link"),
                    }
                )

        return [
            {
                "domain": domain,
                "paid_visibility_count": count,
                "overlapping_keywords": sorted(keyword_map[domain]),
                "sample_ads": ad_copy_by_domain[domain][:5],
            }
            for domain, count in domain_counter.most_common()
        ]

