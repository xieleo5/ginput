"""Errors shared by tooling and platform adapters."""

class InputError(Exception):
    """A user-actionable validation or deployment failure."""


class Pending(InputError):
    """Wait before replacing input method files."""
