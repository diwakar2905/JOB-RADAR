"""Y Combinator & early-stage startup public discovery source."""

import httpx
from typing import List, Optional, Tuple
from radar.sources.base import JobSource, RawOpening


class YCStartupSource(JobSource):
    """Discovers openings at YC companies from public feeds and directories."""

    @property
    def name(self) -> str:
        return "yc"

    def discover(self, cursor: Optional[str] = None) -> Tuple[List[RawOpening], Optional[str]]:
        openings: List[RawOpening] = []
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json"
        }

        # YC Work at a Startup public search API
        try:
            with httpx.Client(timeout=12.0, headers=headers) as client:
                url = "https://www.workatastartup.com/api/jobs"
                # Public filter parameters for engineering / intern / junior
                res = client.get(url, params={"roles": "Engineering", "limit": 20})
                if res.status_code == 200:
                    data = res.json()
                    jobs = data.get("jobs", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
                    for j in jobs[:20]:
                        company = j.get("company", {}) or {}
                        company_name = company.get("name", "YC Startup")
                        domain = company.get("website", f"{company_name.lower().replace(' ', '')}.com")
                        title = j.get("title", "")
                        job_id = j.get("id")
                        apply_url = j.get("apply_url") or f"https://www.workatastartup.com/jobs/{job_id}"
                        
                        loc = j.get("location", "")
                        remote = j.get("remote", False) or "remote" in loc.lower()

                        openings.append(RawOpening(
                            company_name=company_name,
                            company_domain=domain,
                            title=title,
                            apply_url=apply_url,
                            source="yc",
                            location=loc,
                            remote=remote,
                            posted_at=j.get("created_at"),
                            description=j.get("description", "")[:500],
                            extra={"batch": company.get("batch")}
                        ))
        except Exception:
            pass

        return openings, cursor
