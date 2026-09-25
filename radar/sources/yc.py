"""Y Combinator startup discovery placeholder.

CLAUDE.md hard rule: "Wellfound and YC appear only through Tavily search
results." YC/Work at a Startup requires a logged-in session for its job API
and must never be crawled directly. YC coverage comes entirely from
TavilySearchSource's site:ycombinator.com/companies query instead (see
radar/sources/tavily.py). This source is kept as a no-op so `config.yaml`'s
`sources.yc` toggle and the CLI's `--source yc` selector keep working, and so
a future *public*, non-logged-in YC API could be wired in here without
touching run.py or the source registry.
"""

from radar.sources.base import JobSource, RawOpening


class YCStartupSource(JobSource):
    """Intentionally inert — see module docstring. YC discovery happens via Tavily."""

    @property
    def name(self) -> str:
        return "yc"

    def discover(self, cursor: str | None = None) -> tuple[list[RawOpening], str | None]:
        return [], cursor
