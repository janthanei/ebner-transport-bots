from __future__ import annotations

from urllib.parse import urlparse


def filter_target_links(links: list[str], required_substring: str) -> list[str]:
    required = required_substring.strip().lower()
    if not required:
        return []
    return [link for link in links if required in link.lower()]


def is_domain_allowed(url: str, allowlist: list[str]) -> bool:
    if not allowlist:
        return True
    host = (urlparse(url).hostname or "").lower()
    for domain in allowlist:
        domain = domain.lower()
        if host == domain or host.endswith(f".{domain}"):
            return True
    return False

