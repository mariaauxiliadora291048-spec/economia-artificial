from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import quote

import httpx


class ResearchError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ResearchItem:
    title: str
    snippet: str
    source_url: str


@dataclass(frozen=True, slots=True)
class ResearchReport:
    query: str
    source: str
    items: list[ResearchItem]


class ResearchClient(Protocol):
    def search(self, query: str) -> ResearchReport: ...


class WikipediaResearchClient:
    """Read-only, allowlisted connector to a public real-world knowledge source."""

    _ENDPOINT = "https://pt.wikipedia.org/w/api.php"

    def __init__(self, timeout_seconds: float = 10.0, max_results: int = 5) -> None:
        self._timeout_seconds = timeout_seconds
        self._max_results = max_results

    def search(self, query: str) -> ResearchReport:
        normalized_query = query.strip()
        if not 2 <= len(normalized_query) <= 200:
            raise ResearchError("Research query must contain 2 to 200 characters")
        try:
            response = httpx.get(
                self._ENDPOINT,
                params={
                    "action": "query",
                    "format": "json",
                    "list": "search",
                    "srsearch": normalized_query,
                    "srlimit": self._max_results,
                    "utf8": 1,
                },
                headers={"User-Agent": "EconomiaArtificial/0.2 (read-only research)"},
                timeout=self._timeout_seconds,
                follow_redirects=False,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ResearchError("Web research request failed") from exc

        results = response.json().get("query", {}).get("search", [])
        items = [
            ResearchItem(
                title=result["title"],
                snippet=_strip_markup(result.get("snippet", "")),
                source_url=_article_url(result["title"]),
            )
            for result in results
        ]
        return ResearchReport(normalized_query, self._ENDPOINT, items)


def _strip_markup(value: str) -> str:
    return re.sub(r"<[^>]+>", "", value).strip()


def _article_url(title: str) -> str:
    encoded_title = quote(title.replace(" ", "_"))
    return f"https://pt.wikipedia.org/wiki/{encoded_title}"
