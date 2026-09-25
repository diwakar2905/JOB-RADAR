"""Tavily search API discovery source for web-wide hiring posts."""

import os
import re
import httpx
from typing import List, Optional, Tuple, Dict, Any
from radar.sources.base import JobSource, RawOpening


class TavilySearchSource(JobSource):
    """Discovers hiring posts via Tavily Search API based on targeting config."""

    def __init__(self, api_key: Optional[str] = None, config: Optional[Dict[str, Any]] = None):
        self.api_key = api_key or os.getenv("TAVILY_API_KEY")
        self.config = config or {}

    @property
    def name(self) -> str:
        return "tavily"

    def _build_queries(self) -> List[str]:
        targets = self.config.get("targets", {})
        roles = targets.get("roles", ["AI engineer", "backend engineer"])[:3]
        seniorities = targets.get("seniority", ["intern", "fresher"])[:2]
        locations = targets.get("locations", ["India", "remote"])[:2]

        queries = []
        for role in roles:
            # Query targeted at ATS job links
            q = f'"{role}" ({seniorities[0]} OR {seniorities[1] if len(seniorities)>1 else ""}) ({" OR ".join(locations)}) (site:jobs.ashbyhq.com OR site:boards.greenhouse.io OR site:jobs.lever.co)'
            queries.append(q)
        return queries[:2]  # Keep queries low to stay well within free tier

    def discover(self, cursor: Optional[str] = None) -> Tuple[List[RawOpening], Optional[str]]:
        if not self.api_key:
            return [], cursor

        openings: List[RawOpening] = []
        queries = self._build_queries()

        headers = {"Content-Type": "application/json"}
        with httpx.Client(timeout=15.0) as client:
            for q in queries:
                try:
                    payload = {
                        "api_key": self.api_key,
                        "query": q,
                        "search_depth": "basic",
                        "include_domains": ["jobs.ashbyhq.com", "boards.greenhouse.io", "jobs.lever.co"],
                        "max_results": 10
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

                            if match_ashby:
                                company_name = match_ashby.group(1).replace("-", " ").capitalize()
                            elif match_gh:
                                company_name = match_gh.group(1).replace("-", " ").capitalize()
                            elif match_lever:
                                company_name = match_lever.group(1).replace("-", " ").capitalize()
                            elif " - " in title:
                                company_name = title.split(" - ")[-1].strip()

                            # Clean title
                            cleaned_title = title.split(" - ")[0].split(" | ")[0].strip()

                            domain_match = re.search(r"https?://(?:www\.)?([^/]+)", url)
                            domain = domain_match.group(1) if domain_match else f"{company_name.lower().replace(' ', '')}.com"

                            is_remote = "remote" in snippet.lower() or "remote" in title.lower()

                            openings.append(RawOpening(
                                company_name=company_name,
                                company_domain=domain,
                                title=cleaned_title,
                                apply_url=url,
                                source="tavily",
                                remote=is_remote,
                                description=snippet,
                                extra={"query": q}
                            ))
                except Exception:
                    continue

        return openings, cursor
