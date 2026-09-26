"""Candidate Profile Extractor and Manager for Job Radar."""

import argparse
import json
import re
from pathlib import Path
from typing import Any

import httpx


def parse_resume_text(resume_path: str) -> str:
    """Extract plain text from PDF or text file."""
    path = Path(resume_path)
    if not path.exists():
        raise FileNotFoundError(f"Resume file not found at {resume_path}")

    if path.suffix.lower() == ".pdf":
        try:
            from pypdf import PdfReader

            reader = PdfReader(str(path))
            text = "\n".join(page.extract_text() or "" for page in reader.pages)
            return text.strip()
        except ImportError:
            # Fallback simple reading if pypdf not ready
            with open(path, "rb") as f:
                raw = f.read()
                # Extract ascii-like strings
                strings = re.findall(rb"[\x20-\x7E\t\r\n]{4,}", raw)
                return "\n".join(s.decode("latin1", errors="ignore") for s in strings)
    else:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read().strip()


def fetch_github_profile(username_or_url: str) -> dict[str, Any]:
    """Fetch public repos, stars, and languages from GitHub public API."""
    username = username_or_url.strip().rstrip("/").split("/")[-1]
    headers = {"User-Agent": "JobRadar/1.0", "Accept": "application/vnd.github.v3+json"}

    profile_info = {"username": username, "bio": "", "public_repos": 0, "top_repositories": []}

    try:
        with httpx.Client(timeout=10.0, headers=headers) as client:
            user_res = client.get(f"https://api.github.com/users/{username}")
            if user_res.status_code == 200:
                user_data = user_res.json()
                profile_info["bio"] = user_data.get("bio") or ""
                profile_info["public_repos"] = user_data.get("public_repos", 0)
                profile_info["name"] = user_data.get("name") or username

            repos_res = client.get(f"https://api.github.com/users/{username}/repos?sort=updated&per_page=10")
            if repos_res.status_code == 200:
                repos = repos_res.json()
                for repo in repos:
                    if repo.get("fork"):
                        continue
                    profile_info["top_repositories"].append(
                        {
                            "name": repo.get("name"),
                            "description": repo.get("description") or "",
                            "language": repo.get("language") or "",
                            "stars": repo.get("stargazers_count", 0),
                            "url": repo.get("html_url"),
                            "topics": repo.get("topics", []),
                        }
                    )
    except Exception as e:
        profile_info["error"] = str(e)

    return profile_info


def fetch_personal_site(url: str) -> str:
    """Fetch and strip HTML tags to extract text from personal site/portfolio."""
    if not url.startswith("http"):
        url = "https://" + url
    try:
        with httpx.Client(timeout=10.0, follow_redirects=True) as client:
            res = client.get(url, headers={"User-Agent": "JobRadar/1.0"})
            if res.status_code == 200:
                html = res.text
                text = re.sub(r"<script.*?</script>", " ", html, flags=re.DOTALL | re.IGNORECASE)
                text = re.sub(r"<style.*?</style>", " ", text, flags=re.DOTALL | re.IGNORECASE)
                text = re.sub(r"<[^>]+>", " ", text)
                text = re.sub(r"\s+", " ", text)
                return text[:2000].strip()
    except Exception:
        pass
    return ""


def load_profile(profile_path: str = "profile.json") -> dict[str, Any]:
    """Load profile from disk. Creates default starter if not found."""
    path = Path(profile_path)
    if not path.exists():
        default_profile = {
            "name": "Diwakar Mishra",
            "headline": "CSE (AIML) Student & Builder",
            "roles_sought": ["AI Engineer", "Backend Engineer", "Full-Stack Engineer"],
            "seniority": ["intern", "fresher", "junior"],
            "skills": ["Python", "FastAPI", "PyTorch", "LLMs", "PostgreSQL", "Docker"],
            "stack": ["Python", "PyTorch", "FastAPI", "Streamlit"],
            "best_projects": [],
            "proof_points": [],
        }
        save_profile(default_profile, profile_path)
        return default_profile

    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_profile(data: dict[str, Any], profile_path: str = "profile.json") -> None:
    with open(profile_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


COMMON_TECH_KEYWORDS = [
    "Python",
    "PyTorch",
    "TensorFlow",
    "FastAPI",
    "Django",
    "React",
    "Next.js",
    "TypeScript",
    "Docker",
    "Kubernetes",
    "PostgreSQL",
    "MongoDB",
    "Redis",
    "AWS",
    "GCP",
    "LLMs",
    "LangChain",
    "RAG",
]


def extract_profile_with_llm(
    resume_text: str,
    github_summary: dict[str, Any] | None = None,
    site_text: str | None = None,
) -> dict[str, Any] | None:
    """Uses Claude/Ollama (via radar/llm.py) to pull a richer profile out of the
    resume than keyword-spotting can: a headline, best_projects (with a
    one-liner and stack per project), and proof_points. Returns None on any
    failure (no key, no Ollama, bad JSON) so callers can fall back to the
    keyword-only extraction — this must never be the only path.
    """
    from radar.llm import LLMClient

    llm = LLMClient()
    prompt = f"""Extract a structured candidate profile from the inputs below.
Only use information present in the inputs. If something is unknown, omit it
or use an empty list — never invent facts.

Resume text:
{resume_text[:4000]}

GitHub summary (may be empty):
{json.dumps(github_summary or {})[:2000]}

Personal site text (may be empty):
{(site_text or "")[:1500]}

Return ONLY valid JSON matching this schema, nothing else:
{{
  "headline": "one line describing the candidate's focus/level",
  "skills": ["skill1", "skill2"],
  "best_projects": [{{"name": "...", "one_liner": "...", "stack": ["..."], "url": null}}],
  "proof_points": ["strongest 3-5 concrete facts: metrics, scale, outcomes"]
}}"""
    res_text, _provider = llm.complete(prompt, prefer_quality=True)
    if not res_text:
        return None
    try:
        clean_json = re.sub(r"^```json\s*", "", res_text.strip(), flags=re.IGNORECASE)
        clean_json = re.sub(r"```$", "", clean_json.strip())
        data = json.loads(clean_json)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def build_profile(
    resume_path: str | None = None,
    github: str | None = None,
    site: str | None = None,
    output: str = "profile.json",
    roles_sought: list[str] | None = None,
    seniority: list[str] | None = None,
) -> dict[str, Any]:
    """Extracts resume/GitHub/site signal into profile.json and saves it.

    Shared by the CLI (`python -m radar profile`) and the dashboard's Setup tab.
    roles_sought/seniority, when given, are explicit user input (e.g. from the
    Setup tab's targeting fields) and fully replace the stored values, rather
    than merging like skills do — otherwise editing your target roles in the
    dashboard has no effect on the profile that scoring and the sidebar use.
    """
    profile = load_profile(output)

    if roles_sought is not None:
        profile["roles_sought"] = roles_sought
    if seniority is not None:
        profile["seniority"] = seniority

    if github:
        gh_data = fetch_github_profile(github)
        profile["github_summary"] = gh_data
        for r in gh_data.get("top_repositories", []):
            if r.get("language") and r["language"] not in profile.get("skills", []):
                profile["skills"].append(r["language"])

    if site:
        profile["portfolio_text"] = fetch_personal_site(site)

    if resume_path:
        resume_text = parse_resume_text(resume_path)
        profile["resume_raw_summary"] = resume_text[:1000]
        found_skills = set(profile.get("skills", []))
        for tech in COMMON_TECH_KEYWORDS:
            if re.search(r"\b" + re.escape(tech) + r"\b", resume_text, re.IGNORECASE):
                found_skills.add(tech)
        profile["skills"] = sorted(found_skills)

        llm_extracted = extract_profile_with_llm(resume_text, profile.get("github_summary"), profile.get("portfolio_text"))
        if llm_extracted:
            if llm_extracted.get("headline"):
                profile["headline"] = llm_extracted["headline"]
            llm_skills = [s for s in llm_extracted.get("skills", []) if isinstance(s, str)]
            if llm_skills:
                profile["skills"] = sorted(set(profile["skills"]) | set(llm_skills))
            llm_projects = llm_extracted.get("best_projects")
            if isinstance(llm_projects, list) and llm_projects:
                profile["best_projects"] = llm_projects
            llm_proof = llm_extracted.get("proof_points")
            if isinstance(llm_proof, list) and llm_proof:
                profile["proof_points"] = [p for p in llm_proof if isinstance(p, str)]

    save_profile(profile, output)
    return profile


def main():
    parser = argparse.ArgumentParser(description="Extract and update Job Radar profile.")
    parser.add_argument("--resume", help="Path to resume PDF or TXT file")
    parser.add_argument("--github", help="GitHub username or profile URL")
    parser.add_argument("--site", help="Personal website or portfolio URL")
    parser.add_argument("--output", default="profile.json", help="Path to output profile.json")
    args = parser.parse_args()

    if args.resume:
        print(f"Reading resume from {args.resume}...")
    if args.github:
        print(f"Fetching GitHub data for {args.github}...")
    if args.site:
        print(f"Fetching portfolio text from {args.site}...")

    build_profile(resume_path=args.resume, github=args.github, site=args.site, output=args.output)
    print(f"Updated profile saved to {args.output}")


if __name__ == "__main__":
    main()
