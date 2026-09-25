"""Base classes and types for Job Radar discovery sources."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class RawOpening:
    company_name: str
    company_domain: str
    title: str
    apply_url: str
    source: str
    seniority: str | None = None
    location: str | None = None
    remote: bool = False
    posted_at: str | None = None
    description: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class JobSource(ABC):
    """Abstract base class for all job discovery sources."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Source identifier name (e.g. 'greenhouse', 'ashby', 'lever', 'hn', 'tavily', 'yc')."""

    @abstractmethod
    def discover(self, cursor: str | None = None) -> tuple[list[RawOpening], str | None]:
        """
        Fetch new openings starting from cursor.
        Returns:
            Tuple of (list_of_openings, new_cursor)
        """
