"""Semantic-version compatibility helper."""


def is_compatible(current: str, minimum: str) -> bool:
    """Return whether current is at least minimum."""
    return current >= minimum
