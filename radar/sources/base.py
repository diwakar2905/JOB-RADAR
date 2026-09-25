"""Base classes and types for Job Radar discovery sources."""

from dataclasses import dataclass, field
from abc import ABC, abstractmethod
from typing import List, Optional, Tuple, Dict, Any


@dataclass
class RawOpening:
    company_name: str
    company_domain: str
    title: str
    apply_url: str
    source: str
    seniority: Optional[str] = None
    location: Optional[str] = None
    remote: bool = False
    posted_at: Optional[str] = None
    description: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)


class JobSource(ABC):
    """Abstract base class for all job discovery sources."""
    
    @property
    @abstractmethod
    def name(self) -> str:
        """Source identifier name (e.g. 'greenhouse', 'ashby', 'lever', 'hn', 'tavily', 'yc')."""
        pass

    @abstractmethod
    def discover(self, cursor: Optional[str] = None) -> Tuple[List[RawOpening], Optional[str]]:
        """
        Fetch new openings starting from cursor.
        Returns:
            Tuple of (list_of_openings, new_cursor)
        """
        pass
