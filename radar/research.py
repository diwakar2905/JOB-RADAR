"""Company research agent with 14-day SQLite caching and source URL requirements."""

import os
import re
import json
import httpx
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, List
from radar.db import get_company_by_domain, update_company_research, get_or_create_company, DEFAULT_DB_PATH
from radar.llm import LLMClient
from radar.budget import is_budget_exceeded, record_cost

CACHE_TTL_DAYS = 14


def is_research_fresh(researched_at_iso: Optional[str]) -> bool:
    """Returns True if researched within CACHE_TTL_DAYS."""
    if not researched_at_iso:
        return False
    try:
        researched_dt = datetime.fromisoformat(researched_at_iso)
        now = datetime.now(timezone.utc)
        return (now - researched_dt) < timedelta(days=CACHE_TTL_DAYS)
    except Exception:
        return False


class CompanyResearcher:
    """Researches company stage, funding, founders, and product with local SQLite caching."""

    def __init__(self, llm_client: Optional[LLMClient] = None, tavily_key: Optional[str] = None):
        self.llm = llm_client or LLMClient()
        self.tavily_key = tavily_key or os.getenv("TAVILY_API_KEY")

    def research_company(self, company_name: str, domain: str, db_path=DEFAULT_DB_PATH) -> Dict[str, Any]:
        """
        Retrieves company research. Returns cached data if fresh (<= 14 days).
        Otherwise fetches fresh intelligence and updates SQLite.
        """
        # 1. Check local cache
        existing = get_company_by_domain(domain, db_path)
        if existing and is_research_fresh(existing.get("researched_at")):
            return existing

        company_id = get_or_create_company(company_name, domain, db_path=db_path)

        # 2. Gather public search context if Tavily is available and under budget
        search_snippets = []
        sources = [f"https://{domain}"]

        if self.tavily_key and not is_budget_exceeded(db_path=db_path):
            try:
                headers = {"Content-Type": "application/json"}
                query = f"{company_name} startup funding stage founders what they do"
                payload = {
                    "api_key": self.tavily_key,
                    "query": query,
                    "search_depth": "basic",
                    "max_results": 3
                }
                with httpx.Client(timeout=10.0) as client:
                    res = client.post("https://api.tavily.com/search", json=payload, headers=headers)
                    if res.status_code == 200:
                        data = res.json()
                        for r in data.get("results", []):
                            search_snippets.append(r.get("content", ""))
                            if r.get("url") and r["url"] not in sources:
                                sources.append(r["url"])
                        record_cost("tavily", "search", 0.5, db_path=db_path) # ~0.5 cents per search
            except Exception:
                pass

        context_text = "\n".join(search_snippets)

        # 3. Extract structured research using LLM
        stage = "Early-stage"
        funding = "Undisclosed"
        founders = "Undisclosed"
        summary = f"{company_name} develops technology solutions."

        if context_text and not is_budget_exceeded(db_path=db_path):
            prompt = f"""Extract company intelligence for '{company_name}' ({domain}) from the context below.
Context:
{context_text}

Return a valid JSON object ONLY with the following keys:
{{
  "stage": "e.g. Seed / Series A / Series B / Growth / Public",
  "funding": "e.g. $5M or Undisclosed",
  "founders": "names of founders or Undisclosed",
  "summary": "1-2 sentences explaining what the company builds"
}}"""
            res_text, provider = self.llm.complete(prompt, prefer_quality=False)
            if res_text:
                if provider == "claude":
                    record_cost("anthropic", "claude-3-5-sonnet", 0.3, db_path=db_path)
                try:
                    # Clean markdown codeblocks if any
                    clean_json = re.sub(r"^```json\s*", "", res_text.strip(), flags=re.IGNORECASE)
                    clean_json = re.sub(r"```$", "", clean_json.strip())
                    data = json.loads(clean_json)
                    stage = data.get("stage") or stage
                    funding = data.get("funding") or funding
                    founders = data.get("founders") or founders
                    summary = data.get("summary") or summary
                except Exception:
                    pass

        # 4. Save to SQLite cache
        update_company_research(
            company_id=company_id,
            stage=stage,
            funding=funding,
            founders=founders,
            summary=summary,
            sources=sources,
            db_path=db_path
        )

        return {
            "id": company_id,
            "name": company_name,
            "domain": domain,
            "stage": stage,
            "funding": funding,
            "founders": founders,
            "summary": summary,
            "sources": sources,
            "researched_at": datetime.now(timezone.utc).isoformat()
        }
