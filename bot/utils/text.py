import re

_LINK_RE = re.compile(
    r"(https?://[^\s]+|www\.[^\s]+|t(?:elegram)?\.me/[^\s]+)", re.IGNORECASE
)


def extract_links(text: str) -> list[str]:
    return _LINK_RE.findall(text or "")


def contains_link(text: str) -> bool:
    return bool(_LINK_RE.search(text or ""))


def is_whitelisted(link: str, whitelist_domains: list[str]) -> bool:
    link_lower = link.lower()
    return any(domain in link_lower for domain in whitelist_domains)


def contains_banned_word(text: str, banned_words: list[str]) -> bool:
    if not text or not banned_words:
        return False
    lowered = text.lower()
    return any(word in lowered for word in banned_words)
