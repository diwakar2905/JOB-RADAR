"""Link validator checking apply URLs with HTTP HEAD/GET requests."""

import httpx
from typing import Optional


def check_apply_link(url: Optional[str], timeout: float = 6.0) -> bool:
    """
    Verifies that the apply URL is reachable and does not return 404 or 410.
    Returns True if healthy, False if broken or unreachable.
    """
    if not url or not url.startswith("http"):
        return False

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "*/*"
    }

    try:
        with httpx.Client(timeout=timeout, follow_redirects=True, headers=headers) as client:
            try:
                res = client.head(url)
                if res.status_code in (200, 201, 204, 301, 302, 307, 308):
                    return True
                if res.status_code == 405: # Method Not Allowed for HEAD, try streaming GET
                    with client.stream("GET", url) as stream_res:
                        return stream_res.status_code < 400
                return res.status_code < 400
            except httpx.HTTPError:
                # Some servers reject HEAD completely
                with client.stream("GET", url) as stream_res:
                    return stream_res.status_code < 400
    except Exception:
        return False
