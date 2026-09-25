"""Fit scoring engine evaluating candidate-job alignment (0-100) with citation enforcement."""

import json
import re
from typing import Dict, Any, List, Optional
from dataclasses import dataclass
from radar.llm import LLMClient
from radar.budget import is_budget_exceeded, record_cost
from radar.db import DEFAULT_DB_PATH


@dataclass
class ScoreResult:
    score: int
    reason: str
    sources: List[str]


def compute_heuristic_score(
    profile: Dict[str, Any],
    config: Dict[str, Any],
    opening_title: str,
    opening_location: Optional[str],
    is_remote: bool,
    company_name: str,
    company_research: Dict[str, Any],
    apply_url: str
) -> ScoreResult:
    """Heuristic scoring when external LLM is offline or budget exceeded."""
    score = 60
    reasons = []
    sources = [apply_url]

    # Add company research sources if available
    for src in company_research.get("sources", []):
        if src not in sources:
            sources.append(src)

    title_lower = opening_title.lower()
    profile_skills = [s.lower() for s in profile.get("skills", [])]
    profile_roles = [r.lower() for r in profile.get("roles_sought", [])]

    # Title match bonus
    matched_role = False
    for r in profile_roles:
        terms = r.split()
        if any(t in title_lower for t in terms if len(t) > 2):
            score += 15
            reasons.append(f"Direct match with target role '{r}'")
            matched_role = True
            break

    # Seniority bonus
    if any(s in title_lower for s in ["intern", "fresher", "junior", "associate", "graduate"]):
        score += 12
        reasons.append("Exact seniority alignment for early-career builder")

    # Stack match bonus
    skill_hits = []
    for skill in profile_skills:
        if skill in title_lower or (company_research.get("summary") and skill in company_research.get("summary", "").lower()):
            skill_hits.append(skill)
    if skill_hits:
        score += min(15, len(skill_hits) * 5)
        reasons.append(f"Stack overlap in {', '.join(skill_hits[:3])}")

    # Location / Remote bonus
    if is_remote:
        score += 8
        reasons.append("Remote flexibility aligns with candidate targets")
    elif opening_location and "india" in opening_location.lower():
        score += 5
        reasons.append("Located in India")

    # Stage alignment
    stage = company_research.get("stage", "").lower()
    target_stages = [s.lower() for s in config.get("targets", {}).get("stages", [])]
    if any(ts in stage for ts in target_stages):
        score += 5
        reasons.append(f"Stage ({company_research.get('stage')}) matches target growth profile")

    # Cap score between 10 and 98
    final_score = max(10, min(98, score))
    summary_reason = "; ".join(reasons) if reasons else f"Role at {company_name} aligns with software engineering fundamentals."

    return ScoreResult(
        score=final_score,
        reason=summary_reason[:250],
        sources=sources[:3]
    )


class FitScorer:
    """Scores candidate fit (0-100) using Claude or Ollama with mandatory citation URLs."""

    def __init__(self, llm_client: Optional[LLMClient] = None):
        self.llm = llm_client or LLMClient()

    def score_fit(
        self,
        profile: Dict[str, Any],
        config: Dict[str, Any],
        opening: Dict[str, Any],
        company: Dict[str, Any],
        db_path=DEFAULT_DB_PATH
    ) -> ScoreResult:
        apply_url = opening.get("apply_url", "")
        available_sources = [apply_url]
        for s in company.get("sources", []):
            if s and s not in available_sources:
                available_sources.append(s)

        # If budget exceeded, fall back to heuristic
        if is_budget_exceeded(config, db_path=db_path):
            return compute_heuristic_score(
                profile, config,
                opening.get("title", ""),
                opening.get("location"),
                opening.get("remote", False),
                company.get("name", ""),
                company,
                apply_url
            )

        prompt = f"""You are the Job Fit Evaluator for Job Radar. Evaluate the candidate fit for this opening on a 0-100 scale.

Candidate Profile:
Name: {profile.get('name')}
Headline: {profile.get('headline')}
Target Roles: {profile.get('roles_sought')}
Seniority: {profile.get('seniority')}
Skills: {profile.get('skills')}
Key Projects: {json.dumps(profile.get('best_projects', []))}

Target Preferences (from config.yaml):
Priorities: {config.get('priorities')}
Target Locations: {config.get('targets', {}).get('locations')}

Opening Details:
Company: {company.get('name')} ({company.get('domain')})
Title: {opening.get('title')}
Location: {opening.get('location')} (Remote: {opening.get('remote')})
Job Description Snippet: {opening.get('description', 'N/A')}
Apply Link: {apply_url}

Company Context:
Stage: {company.get('stage')} | Funding: {company.get('funding')}
Summary: {company.get('summary')}
Available Source URLs: {available_sources}

Guardrail Rules:
1. Score from 0 to 100 based on role relevance, tech stack fit, and builder potential.
2. Provide a crisp 1-2 line reason explaining specifically why this role fits or does not fit.
3. CRITICAL: Every claim MUST be grounded. Return 1 to 3 verified source URLs chosen strictly from the Available Source URLs list.

Output ONLY valid JSON in this exact schema:
{{
  "score": 85,
  "reason": "1-2 lines explaining fit",
  "sources": ["{apply_url}"]
}}"""

        res_text, provider = self.llm.complete(prompt, prefer_quality=True)
        if res_text:
            if provider == "claude":
                record_cost("anthropic", "claude-3-5-sonnet", 0.4, db_path=db_path)
            try:
                clean_json = re.sub(r"^```json\s*", "", res_text.strip(), flags=re.IGNORECASE)
                clean_json = re.sub(r"```$", "", clean_json.strip())
                data = json.loads(clean_json)
                score = int(data.get("score", 60))
                reason = data.get("reason", "Good match with candidate stack.")
                sources = data.get("sources", [apply_url])
                # Ensure all sources are valid strings
                valid_sources = [s for s in sources if isinstance(s, str) and s.startswith("http")]
                if not valid_sources:
                    valid_sources = [apply_url]

                return ScoreResult(
                    score=max(0, min(100, score)),
                    reason=reason.strip(),
                    sources=valid_sources
                )
            except Exception:
                pass

        # Heuristic fallback if LLM response couldn't be parsed or was unavailable
        return compute_heuristic_score(
            profile, config,
            opening.get("title", ""),
            opening.get("location"),
            opening.get("remote", False),
            company.get("name", ""),
            company,
            apply_url
        )
