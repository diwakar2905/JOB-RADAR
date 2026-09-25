"""Tavily search API discovery source for web-wide hiring posts."""

import os
import re
from typing import Any

import httpx

from radar.sources.base import JobSource, RawOpening


class TavilySearchSource(JobSource):
    """Discovers hiring posts via Tavily Search API based on targeting config."""

    def __init__(self, api_key: str | None = None, config: dict[str, Any] | None = None):
        self.api_key = api_key or os.getenv("TAVILY_API_KEY")
        self.config = config or {}

    @property
    def name(self) -> str:
        return "tavily"

    ATS_DOMAINS = ["jobs.ashbyhq.com", "boards.greenhouse.io", "jobs.lever.co"]

    def _build_queries(self) -> list[tuple[str, list[str]]]:
        """Returns (query, include_domains) pairs. YC/Wellfound must only ever be
        reached through Tavily search results, never crawled directly (hard rule) —
        so a YC-scoped query with its own include_domains is added alongside the
        ATS-scoped ones."""
        targets = self.config.get("targets", {})
        roles = targets.get("roles", ["AI engineer", "backend engineer"])[:3]
        seniorities = targets.get("seniority", ["intern", "fresher"])[:2]
        locations = targets.get("locations", ["India", "remote"])[:2]
        seniority_a = seniorities[0] if seniorities else "intern"
        seniority_b = seniorities[1] if len(seniorities) > 1 else seniority_a

        queries: list[tuple[str, list[str]]] = []
        for role in roles[:2]:
            q = f'"{role}" ({seniority_a} OR {seniority_b}) ({" OR ".join(locations)}) (site:jobs.ashbyhq.com OR site:boards.greenhouse.io OR site:jobs.lever.co)'
            queries.append((q, self.ATS_DOMAINS))

        if roles:
            yc_query = f'site:ycombinator.com/companies "{roles[0]}" {seniority_a} jobs'
            queries.append((yc_query, ["ycombinator.com"]))

        return queries[:3]  # Keep queries low to stay well within free tier

    def discover(self, cursor: str | None = None) -> tuple[list[RawOpening], str | None]:
        if not self.api_key:
            return [], cursor

        openings: list[RawOpening] = []
        queries = self._build_queries()

        headers = {"Content-Type": "application/json"}
        with httpx.Client(timeout=15.0) as client:
            for q, include_domains in queries:
                try:
                    payload = {
                        "api_key": self.api_key,
                        "query": q,
                        "search_depth": "basic",
                        "include_domains": include_domains,
                        "max_results": 10,
                    }
                    res = client.post("https://api.tavily.com/search", json=payload, headers=headers)
                    if res.status_code == 200:
                        data = res.json()
                        for result in data.get("results", []):
                            url = result.get("url", "")
                            title = result.get("title", "")
                            snippet = result.get("content", "")

                            # Extract company name from URL / Title
                            # E.g. https://jobs.ashbyhq.com/company_name/uuid
                            company_name = "Tech Startup"
                            match_ashby = re.search(r"jobs\.ashbyhq\.com/([^/]+)", url)
                            match_gh = re.search(r"boards\.greenhouse\.io/([^/]+)", url)
                            match_lever = re.search(r"jobs\.lever\.co/([^/]+)", url)
                            match_yc = re.search(r"ycombinator\.com/companies/([^/]+)", url)

                            # Track the underlying ATS board so it can be added to the
                            # ATS sources' watchlist for future runs (auto-discovery growth loop).
                            discovered_platform: str | None = None
                            discovered_slug: str | None = None

                            if match_ashby:
                                discovered_platform, discovered_slug = "ashby", match_ashby.group(1)
                                company_name = discovered_slug.replace("-", " ").capitalize()
                            elif match_gh:
                                discovered_platform, discovered_slug = "greenhouse", match_gh.group(1)
                                company_name = discovered_slug.replace("-", " ").capitalize()
                            elif match_lever:
                                discovered_platform, discovered_slug = "lever", match_lever.group(1)
                                company_name = discovered_slug.replace("-", " ").capitalize()
                            elif match_yc:
                                # YC's own directory has no public ATS API to add to a watchlist —
                                # it stays reachable only via this Tavily search, per policy.
                                company_name = match_yc.group(1).replace("-", " ").capitalize()
                            elif " - " in title:
                                company_name = title.split(" - ")[-1].strip()

                            # Clean title
                            cleaned_title = title.split(" - ")[0].split(" | ")[0].strip()

                            domain_match = re.search(r"https?://(?:www\.)?([^/]+)", url)
                            domain = domain_match.group(1) if domain_match else f"{company_name.lower().replace(' ', '')}.com"

                            is_remote = "remote" in snippet.lower() or "remote" in title.lower()

                            extra = {"query": q}
                            if discovered_platform and discovered_slug:
                                extra["discovered_platform"] = discovered_platform
                                extra["discovered_slug"] = discovered_slug

                            openings.append(
                                RawOpening(
                                    company_name=company_name,
                                    company_domain=domain,
                                    title=cleaned_title,
                                    apply_url=url,
                                    source="tavily",
                                    remote=is_remote,
                                    description=snippet,
                                    extra=extra,
                                )
                            )
                except Exception:
                    continue

        return openings, cursor
