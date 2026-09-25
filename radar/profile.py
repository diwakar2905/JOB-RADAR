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


def main():
    parser = argparse.ArgumentParser(description="Extract and update Job Radar profile.")
    parser.add_argument("--resume", help="Path to resume PDF or TXT file")
    parser.add_argument("--github", help="GitHub username or profile URL")
    parser.add_argument("--site", help="Personal website or portfolio URL")
    parser.add_argument("--output", default="profile.json", help="Path to output profile.json")
    args = parser.parse_args()

    profile = load_profile(args.output)

    if args.resume:
        print(f"Reading resume from {args.resume}...")
        resume_text = parse_resume_text(args.resume)
        profile["resume_raw_summary"] = resume_text[:1000]
        # Basic keyword heuristics
        found_skills = set(profile.get("skills", []))
        common_tech = [
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
        for tech in common_tech:
            if re.search(r"\b" + re.escape(tech) + r"\b", resume_text, re.IGNORECASE):
                found_skills.add(tech)
        profile["skills"] = sorted(list(found_skills))

    if args.github:
        print(f"Fetching GitHub data for {args.github}...")
        gh_data = fetch_github_profile(args.github)
        profile["github_summary"] = gh_data
        for r in gh_data.get("top_repositories", []):
            if r.get("language") and r["language"] not in profile.get("skills", []):
                profile["skills"].append(r["language"])

    if args.site:
        print(f"Fetching portfolio text from {args.site}...")
        site_text = fetch_personal_site(args.site)
        profile["portfolio_text"] = site_text

    save_profile(profile, args.output)
    print(f"Updated profile saved to {args.output}")


if __name__ == "__main__":
    main()
