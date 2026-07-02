"""Drift detector data model."""

from dataclasses import asdict, dataclass
from typing import Optional


@dataclass(frozen=True)
class DriftEntry:  # pylint: disable=too-many-instance-attributes  # data record
    """One drift between an authoritative source and a consumer."""

    plugin: str
    image: str  # release key, after alias resolution
    alias: str  # <name>_tag prefix as found in the source
    expected: str  # value from the authoritative source
    found: str  # value from the consumer
    expected_src: str  # human-readable path of the authoritative source
    found_src: str  # human-readable path of the consumer
    allowlisted: bool = False
    reason: Optional[str] = None
    # Optional per-entry overrides of the plugin's SUMMARY/REMEDIATION, for
    # plugins whose findings split into distinct actions (e.g. add vs remove).
    # When None the report falls back to the plugin-level constants.
    summary: Optional[str] = None
    remediation: Optional[str] = None

    def as_allowlisted(self, reason: str) -> "DriftEntry":
        """Return a copy marked allowlisted, carrying the match reason."""
        return DriftEntry(
            plugin=self.plugin,
            image=self.image,
            alias=self.alias,
            expected=self.expected,
            found=self.found,
            expected_src=self.expected_src,
            found_src=self.found_src,
            allowlisted=True,
            reason=reason,
            summary=self.summary,
            remediation=self.remediation,
        )

    def to_dict(self) -> dict:
        """Return the entry's fields as a plain, JSON-serialisable dict."""
        return asdict(self)
