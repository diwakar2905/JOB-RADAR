"""Public ATS job feeds source: Greenhouse, Lever, and Ashby."""

import httpx
from typing import List, Dict, Any, Optional, Tuple
from radar.sources.base import JobSource, RawOpening


class ATSSource(JobSource):
    """Fetches job postings directly from public ATS JSON endpoints."""

    def __init__(self, watchlist_config: Optional[Dict[str, List[str]]] = None):
        self.watchlist = watchlist_config or {
            "greenhouse": ["postman", "browserstack", "perplexity", "mistral", "replicate"],
            "lever": ["hasura"],
            "ashby": ["cursor", "modal", "elevenlabs", "ramp"]
        }

    @property
    def name(self) -> str:
        return "ats"

    def discover(self, cursor: Optional[str] = None) -> Tuple[List[RawOpening], Optional[str]]:
        openings: List[RawOpening] = []
        headers = {"User-Agent": "JobRadar/1.0 (Job Aggregator; personal use)"}

        with httpx.Client(timeout=12.0, headers=headers) as client:
            # 1. Greenhouse
            for slug in self.watchlist.get("greenhouse", []):
                try:
                    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=false"
                    res = client.get(url)
                    if res.status_code == 200:
                        data = res.json()
                        company_domain = f"{slug}.com"
                        for job in data.get("jobs", []):
                            location_str = ""
                            if isinstance(job.get("location"), dict):
                                location_str = job["location"].get("name", "")
                            elif isinstance(job.get("location"), str):
                                location_str = job.get("location", "")
                            
                            is_remote = "remote" in location_str.lower() or "remote" in job.get("title", "").lower()
                            
                            openings.append(RawOpening(
                                company_name=slug.capitalize(),
                                company_domain=company_domain,
                                title=job.get("title", ""),
                                apply_url=job.get("absolute_url") or f"https://boards.greenhouse.io/{slug}/jobs/{job.get('id')}",
                                source="greenhouse",
                                location=location_str,
                                remote=is_remote,
                                posted_at=job.get("updated_at"),
                                extra={"ats_id": job.get("id"), "ats_slug": slug}
                            ))
                except Exception:
                    continue

            # 2. Lever
            for slug in self.watchlist.get("lever", []):
                try:
                    url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
                    res = client.get(url)
                    if res.status_code == 200:
                        jobs = res.json()
                        company_domain = f"{slug}.com"
                        for job in jobs:
                            categories = job.get("categories") or {}
                            loc = categories.get("location") or ""
                            workplace = job.get("workplaceType", "").lower()
                            is_remote = workplace == "remote" or "remote" in loc.lower() or "remote" in job.get("text", "").lower()

                            openings.append(RawOpening(
                                company_name=slug.capitalize(),
                                company_domain=company_domain,
                                title=job.get("text", ""),
                                apply_url=job.get("applyUrl") or job.get("hostedUrl"),
                                source="lever",
                                location=loc,
                                remote=is_remote,
                                posted_at=str(job.get("createdAt")),
                                extra={"ats_id": job.get("id"), "ats_slug": slug}
                            ))
                except Exception:
                    continue

            # 3. Ashby
            for slug in self.watchlist.get("ashby", []):
                try:
                    url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
                    res = client.get(url)
                    if res.status_code == 200:
                        data = res.json()
                        company_domain = f"{slug}.com"
                        for job in data.get("jobs", []):
                            is_remote = job.get("isRemote", False) or "remote" in job.get("title", "").lower()
                            openings.append(RawOpening(
                                company_name=slug.capitalize(),
                                company_domain=company_domain,
                                title=job.get("title", ""),
                                apply_url=job.get("jobUrl") or f"https://jobs.ashbyhq.com/{slug}/{job.get('id')}",
                                source="ashby",
                                location=job.get("location", ""),
                                remote=is_remote,
                                posted_at=job.get("publishedAt"),
                                extra={"ats_id": job.get("id"), "ats_slug": slug}
                            ))
                except Exception:
                    continue

        return openings, cursor
