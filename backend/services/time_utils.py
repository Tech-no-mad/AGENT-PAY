"""UTC clock and timestamp parsing shared by requests and payment decisions."""

from datetime import datetime, timezone


def utc_now() -> datetime:
    """Read the clock on every call; never cache an application's current time."""
    return datetime.now(timezone.utc)


def utc_timestamp() -> str:
    return utc_now().isoformat()


def parse_utc_timestamp(value: str) -> datetime:
    """Require an explicit offset so server-local time cannot change a deadline."""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Timestamp must include a timezone")
    return parsed.astimezone(timezone.utc)
