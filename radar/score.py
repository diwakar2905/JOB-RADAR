"""Fit scoring engine evaluating candidate-job alignment (0-100) with citation enforcement."""

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from radar.budget import is_budget_exceeded, record_cost
from radar.db import DEFAULT_DB_PATH, count_feedback_entries, get_feedback_examples
from radar.llm import LLMClient

MIN_FEEDBACK_FOR_CALIBRATION = 5


@dataclass
class ScoreResult:
    score: int
    reason: str
    sources: list[str]


def compute_heuristic_score(
    profile: dict[str, Any],
    config: dict[str, Any],
    opening_title: str,
    opening_location: str | None,
    is_remote: bool,
    company_name: str,
    company_research: dict[str, Any],
    apply_url: str,
    opening_description: str | None = None,
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
    # Skill/stack matching looks at the description too, not just the title —
    # most postings name their stack in the body, not the job title.
    combined_lower = f"{title_lower} {(opening_description or '').lower()}"
    profile_skills = [s.lower() for s in profile.get("skills", [])]
    profile_roles = [r.lower() for r in profile.get("roles_sought", [])]

    # Title match bonus. Requires every word in the target role phrase to appear
    # in the title (not just any one of them, and not just the generic word
    # "engineer"), so "Software Engineer, Billing" doesn't get credited as a
    # match for "AI Engineer" merely because both happen to say "engineer".
    for r in profile_roles:
        terms = r.split()
        if terms and all(t in title_lower for t in terms):
            score += 15
            reasons.append(f"Direct match with target role '{r}'")
            break

    # Seniority bonus
    if any(s in title_lower for s in ["intern", "fresher", "junior", "associate", "graduate"]):
        score += 12
        reasons.append("Exact seniority alignment for early-career builder")

    # Stack match bonus
    company_summary_lower = (company_research.get("summary") or "").lower()
    skill_hits = []
    for skill in profile_skills:
        if skill in combined_lower or skill in company_summary_lower:
            skill_hits.append(skill)
    if skill_hits:
        score += min(15, len(skill_hits) * 5)
        reasons.append(f"Stack overlap in {', '.join(skill_hits[:3])}")

    # Project relevance bonus: does a project's own stack overlap with what
    # this opening is asking for? Surfaces the strongest matching project by
    # name instead of only citing raw skill keywords.
    for project in profile.get("best_projects", []):
        if not isinstance(project, dict):
            continue
        project_stack = [s.lower() for s in project.get("stack", []) if isinstance(s, str)]
        if project_stack and any(s in combined_lower for s in project_stack):
            score += 6
            reasons.append(f"Project '{project.get('name', 'a past project')}' uses a similar stack")
            break

    # Location / Remote bonus
    if is_remote:
        score += 8
        reasons.append("Remote flexibility aligns with candidate targets")
    elif opening_location and "india" in opening_location.lower():
        score += 5
        reasons.append("Located in India")

    # Stage alignment
    stage = (company_research.get("stage") or "").lower()
    target_stages = [s.lower() for s in config.get("targets", {}).get("stages", [])]
    if any(ts in stage for ts in target_stages):
        score += 5
        reasons.append(f"Stage ({company_research.get('stage')}) matches target growth profile")

    # Cap score between 10 and 98
    final_score = max(10, min(98, score))
    summary_reason = "; ".join(reasons) if reasons else f"Role at {company_name} aligns with software engineering fundamentals."

    return ScoreResult(score=final_score, reason=summary_reason[:250], sources=sources[:3])


class FitScorer:
    """Scores candidate fit (0-100) using Claude or Ollama with mandatory citation URLs."""

    def __init__(self, llm_client: LLMClient | None = None):
        self.llm = llm_client or LLMClient()
        self._calibration_prompt_block: str | None = None

    def _get_calibration_block(self, db_path: Path) -> str:
        """Builds a few-shot calibration block from past 👍/👎 feedback, once per run.

        No model training — just recent good/bad examples fed back into the prompt,
        per SPEC.md §9.5. No-ops below MIN_FEEDBACK_FOR_CALIBRATION entries.
        """
        if self._calibration_prompt_block is not None:
            return self._calibration_prompt_block

        if count_feedback_entries(db_path=db_path) < MIN_FEEDBACK_FOR_CALIBRATION:
            self._calibration_prompt_block = ""
            return self._calibration_prompt_block

        examples = get_feedback_examples(limit=10, db_path=db_path)
        lines = [
            f'- {"GOOD" if ex["feedback"] == "good" else "BAD"} fit: "{ex["title"]}" at {ex["company_name"]} '
            f"(scored {ex['score']}) — {ex['reason']}"
            for ex in examples
        ]
        self._calibration_prompt_block = (
            "\nCalibration — how I judged similar past matches (learn from these, don't repeat mistakes):\n"
            + "\n".join(lines)
            + "\n"
        )
        return self._calibration_prompt_block

    def score_fit(
        self,
        profile: dict[str, Any],
        config: dict[str, Any],
        opening: dict[str, Any],
        company: dict[str, Any],
        db_path=DEFAULT_DB_PATH,
    ) -> ScoreResult:
        apply_url = opening.get("apply_url", "")
        available_sources = [apply_url]
        for s in company.get("sources", []):
            if s and s not in available_sources:
                available_sources.append(s)

        # If budget exceeded, fall back to heuristic
        if is_budget_exceeded(config, db_path=db_path):
            return compute_heuristic_score(
                profile,
                config,
                opening.get("title", ""),
                opening.get("location"),
                opening.get("remote", False),
                company.get("name", ""),
                company,
                apply_url,
                opening.get("description"),
            )

        prompt = f"""You are the Job Fit Evaluator for Job Radar. Evaluate the candidate fit for this opening on a 0-100 scale.

Candidate Profile:
Name: {profile.get("name")}
Headline: {profile.get("headline")}
Target Roles: {profile.get("roles_sought")}
Seniority: {profile.get("seniority")}
Skills: {profile.get("skills")}
Key Projects: {json.dumps(profile.get("best_projects", []))}
Proof Points: {json.dumps(profile.get("proof_points", []))}

Target Preferences (from config.yaml):
Priorities: {config.get("priorities")}
Target Locations: {config.get("targets", {}).get("locations")}

Opening Details:
Company: {company.get("name")} ({company.get("domain")})
Title: {opening.get("title")}
Location: {opening.get("location")} (Remote: {opening.get("remote")})
Job Description Snippet: {opening.get("description", "N/A")}
Apply Link: {apply_url}

Company Context:
Stage: {company.get("stage")} | Funding: {company.get("funding")}
Summary: {company.get("summary")}
Available Source URLs: {available_sources}
{self._get_calibration_block(db_path)}
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

                return ScoreResult(score=max(0, min(100, score)), reason=reason.strip(), sources=valid_sources)
            except Exception:
                pass

        # Heuristic fallback if LLM response couldn't be parsed or was unavailable
        return compute_heuristic_score(
            profile,
            config,
            opening.get("title", ""),
            opening.get("location"),
            opening.get("remote", False),
            company.get("name", ""),
            company,
            apply_url,
            opening.get("description"),
        )
