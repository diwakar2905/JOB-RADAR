"""Filter engine to drop dealbreakers, avoided companies, and mismatched roles."""

import re
from typing import Dict, Any, List, Optional
from dataclasses import dataclass
from radar.sources.base import RawOpening
from radar.normalize import clean_company_domain, infer_seniority


@dataclass
class FilterResult:
    passed: bool
    reason: str


def is_on_site_outside_india(location: Optional[str], remote: bool) -> bool:
    """Returns True if role is confirmed on-site outside India."""
    if remote or not location:
        return False
    loc = location.lower()
    # Check if India
    if any(k in loc for k in ["india", "bengaluru", "bangalore", "delhi", "hyderabad", "mumbai", "pune", "noida", "gurgaon"]):
        return False
    if "remote" in loc:
        return False
    
    # Locations known to be outside India
    foreign_keywords = [
        "usa", "united states", "san francisco", "new york", "london", "uk", 
        "germany", "berlin", "canada", "toronto", "singapore", "australia", 
        "sydney", "paris", "france", "austin", "seattle"
    ]
    return any(k in loc for k in foreign_keywords)


def matches_target_roles(title: str, target_roles: List[str]) -> bool:
    """Check if title matches any of the target roles, or if it's an excluded role."""
    title_lower = title.lower()

    # Immediate rejection for non-engineering / unrelated fields
    disallowed = [
        "account executive", "sales", "recruiter", "marketing", "hr ", "human resources", 
        "legal", "counsel", "finance", "accounting", "growth manager", "customer success",
        "graphic designer", "copywriter", "office manager"
    ]
    for d in disallowed:
        if d in title_lower:
            return False

    # Check engineering / technical keywords
    tech_keywords = [
        "engineer", "developer", "ai", "ml", "machine learning", "deep learning", 
        "data science", "backend", "frontend", "full stack", "fullstack", "software",
        "intern", "researcher", "nlp", "llm", "systems"
    ]
    if not any(k in title_lower for k in tech_keywords):
        return False

    # Check against specific target roles if provided
    if not target_roles:
        return True
    
    for r in target_roles:
        r_terms = r.lower().split()
        if all(term in title_lower for term in r_terms):
            return True

    return True


def apply_filters(opening: RawOpening, config: Dict[str, Any]) -> FilterResult:
    """
    Applies cheap rule-based filters first, verifying dealbreakers, avoided companies,
    seniority, and role alignment.
    """
    targets = config.get("targets", {})
    dealbreakers = [d.lower() for d in config.get("dealbreakers", [])]
    avoid_companies = [c.lower() for c in config.get("avoid_companies", [])]
    allowed_seniority = [s.lower() for s in targets.get("seniority", ["intern", "fresher", "junior", "early-career"])]

    # 1. Avoided companies
    comp_name = opening.company_name.lower()
    comp_domain = clean_company_domain(opening.company_domain)
    for avoided in avoid_companies:
        if avoided in comp_name or avoided in comp_domain:
            return FilterResult(passed=False, reason=f"Company '{opening.company_name}' is in avoid_companies list")

    # 2. Check title against target roles
    target_roles = targets.get("roles", [])
    if not matches_target_roles(opening.title, target_roles):
        return FilterResult(passed=False, reason=f"Title '{opening.title}' does not match target technical roles")

    # 3. Dealbreaker: Unpaid
    combined_text = f"{opening.title} {opening.description or ''}".lower()
    if any("unpaid" in d for d in dealbreakers):
        if "unpaid" in combined_text or "volunteer" in combined_text or "no stipend" in combined_text:
            return FilterResult(passed=False, reason="Role matches dealbreaker: unpaid")

    # 4. Seniority filter
    inferred_sen = opening.seniority or infer_seniority(opening.title, opening.description)
    if inferred_sen in ["senior", "staff", "principal"]:
        if not any(s in allowed_seniority for s in ["senior", "staff", "principal"]):
            return FilterResult(passed=False, reason=f"Role seniority ({inferred_sen}) is too senior for target profile")

    # 5. Dealbreaker: On-site outside India
    if any("outside india" in d for d in dealbreakers):
        if is_on_site_outside_india(opening.location, opening.remote):
            return FilterResult(passed=False, reason=f"Role location ({opening.location}) is on-site outside India")

    # 6. Experience requirement dealbreaker (e.g. 5+ years)
    if any("5+ years" in d for d in dealbreakers) or any("senior only" in d for d in dealbreakers):
        exp_match = re.search(r"(\d+)\+?\s*years?\s+(?:of\s+)?experience", combined_text)
        if exp_match:
            years = int(exp_match.group(1))
            if years >= 5 and "senior" not in allowed_seniority:
                return FilterResult(passed=False, reason=f"Role demands {years}+ years experience")

    return FilterResult(passed=True, reason="Passed all filters")
