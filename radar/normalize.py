"""Normalization and deduplication utilities for job openings."""

import hashlib
import re


def clean_company_domain(domain: str) -> str:
    """Extract root domain without http://, www., or trailing paths."""
    domain = domain.lower().strip()
    domain = re.sub(r"^https?://", "", domain)
    domain = re.sub(r"^www\.", "", domain)
    domain = domain.split("/")[0].split(":")[0]
    return domain


def normalize_title(title: str) -> str:
    """Clean and normalize role title for matching and deduplication."""
    title = title.lower()
    # Remove common clutter words and brackets
    title = re.sub(r"[\(\[\{].*?[\)\]\}]", "", title)
    title = re.sub(r"[–—\-]", " ", title)
    title = re.sub(r"\s+", " ", title).strip()
    return title


def normalize_location(location: str | None, remote_hint: bool = False) -> tuple[str, bool]:
    """Normalize location string and determine remote flag."""
    if not location:
        return ("Remote", True) if remote_hint else ("Unspecified", False)

    loc_lower = location.lower()
    is_remote = remote_hint or "remote" in loc_lower or "anywhere" in loc_lower or "wfh" in loc_lower

    if "india" in loc_lower or any(
        c in loc_lower for c in ["bengaluru", "bangalore", "delhi", "hyderabad", "mumbai", "pune", "gurgaon", "noida"]
    ):
        normalized_loc = "India" if not any(c in loc_lower for c in ["bengaluru", "bangalore"]) else "Bengaluru, India"
    elif is_remote:
        normalized_loc = "Remote"
    else:
        normalized_loc = location.strip()

    return normalized_loc, is_remote


def infer_seniority(title: str, description: str | None = None) -> str:
    """Infer seniority from title and description."""
    text = (title + " " + (description or "")).lower()

    if any(k in text for k in ["intern", "internship", "co-op"]):
        return "intern"
    if any(k in text for k in ["fresher", "graduate", "new grad", "entry level", "associate"]):
        return "fresher"
    if any(k in text for k in ["junior", "jr", "software engineer 1", "sde 1", "sde-1", "sde i", "level 1"]):
        return "junior"
    if any(k in text for k in ["lead", "staff", "principal", "director", "head of", "architect", "vp"]):
        return "staff"
    if any(k in text for k in ["senior", "sr.", "sr ", "sde 3", "sde-3", "sde iii", "5+ years", "7+ years", "8+ years"]):
        return "senior"
    if any(k in text for k in ["mid", "sde 2", "sde-2", "sde ii", "intermediate"]):
        return "mid"
    return "early-career"


def compute_dedupe_hash(company_domain: str, title: str, location: str | None = None) -> str:
    """
    Generate SHA-256 dedupe hash based on domain + normalized title + normalized location.
    Per TRD: 'Dedupe on a hash of company domain + role title + location.'
    """
    clean_domain = clean_company_domain(company_domain)
    clean_t = normalize_title(title)
    clean_l = (location or "").lower().strip()
    key = f"{clean_domain}:{clean_t}:{clean_l}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()
