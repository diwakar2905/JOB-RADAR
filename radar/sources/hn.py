"""Hacker News 'Who is hiring' source using the public Algolia Search API."""

import html
import re

import httpx

from radar.sources.base import JobSource, RawOpening


class HackerNewsHiringSource(JobSource):
    """Fetches and parses top-level comments from the latest 'Ask HN: Who is hiring?' threads."""

    @property
    def name(self) -> str:
        return "hn"

    def _find_latest_hiring_thread_id(self, client: httpx.Client) -> int | None:
        """Find the story_id of the most recent 'Ask HN: Who is hiring?' thread."""
        url = "https://hn.algolia.com/api/v1/search?query=Ask+HN:+Who+is+hiring&tags=story,author_whoishiring&hitsPerPage=3"
        res = client.get(url)
        if res.status_code == 200:
            hits = res.json().get("hits", [])
            for hit in hits:
                title = hit.get("title", "")
                if "who is hiring" in title.lower() and "seeking freelancer" not in title.lower():
                    return int(hit.get("objectID"))
        return None

    def discover(self, cursor: str | None = None) -> tuple[list[RawOpening], str | None]:
        openings: list[RawOpening] = []
        new_cursor = cursor
        headers = {"User-Agent": "JobRadar/1.0"}

        try:
            with httpx.Client(timeout=15.0, headers=headers) as client:
                story_id = self._find_latest_hiring_thread_id(client)
                if not story_id:
                    return [], cursor

                # Fetch top comments from this thread
                # If cursor is story_id:last_comment_id, pick up from there
                last_comment_id = int(cursor.split(":")[-1]) if (cursor and ":" in cursor) else 0

                api_url = f"https://hn.algolia.com/api/v1/search_by_date?tags=comment,story_{story_id}&hitsPerPage=50"
                res = client.get(api_url)
                if res.status_code != 200:
                    return [], cursor

                hits = res.json().get("hits", [])
                max_seen_id = last_comment_id

                for hit in hits:
                    comment_id = int(hit.get("objectID", 0))
                    if comment_id <= last_comment_id:
                        continue
                    max_seen_id = max(max_seen_id, comment_id)

                    comment_html = hit.get("comment_text") or ""
                    # Unescape HTML entities and replace <p> with newline
                    clean_text = html.unescape(re.sub(r"<p>", "\n", comment_html))
                    clean_text = re.sub(r"<[^>]+>", " ", clean_text).strip()
                    lines = [line.strip() for line in clean_text.splitlines() if line.strip()]
                    if not lines:
                        continue

                    header_line = lines[0]
                    # Format typically: "Company | Title / Roles | Location | REMOTE / ONSITE | URL"
                    parts = [p.strip() for p in re.split(r"\s*\|\s*|\s*–\s*|\s*-\s*", header_line) if p.strip()]
                    if len(parts) >= 2:
                        company_name = parts[0]
                        # Discard overly long strings as company name
                        if len(company_name) > 40:
                            continue

                        role_title = parts[1] if len(parts) > 1 else "Software Engineer"
                        location = parts[2] if len(parts) > 2 else "Remote"

                        # Find URLs in comment
                        urls = re.findall(r"https?://[^\s<>\"'()]+", clean_text)
                        apply_url = urls[0] if urls else f"https://news.ycombinator.com/item?id={comment_id}"

                        # Infer company domain from url
                        domain_match = re.search(r"https?://(?:www\.)?([^/]+)", apply_url)
                        company_domain = (
                            domain_match.group(1).lower() if domain_match else f"{company_name.lower().replace(' ', '')}.com"
                        )

                        is_remote = "remote" in header_line.lower() or "remote" in clean_text[:200].lower()

                        openings.append(
                            RawOpening(
                                company_name=company_name,
                                company_domain=company_domain,
                                title=role_title,
                                apply_url=apply_url,
                                source="hn",
                                location=location,
                                remote=is_remote,
                                posted_at=hit.get("created_at"),
                                description=clean_text[:1000],
                                extra={"hn_item_id": comment_id, "story_id": story_id},
                            )
                        )

                new_cursor = f"{story_id}:{max_seen_id}"
        except Exception:
            return openings, cursor

        return openings, new_cursor
