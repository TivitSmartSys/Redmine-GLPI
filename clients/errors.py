"""Shared API exception types.

Every message carried by these exceptions passes through report.messages.redact()
before it is raised, so a token can never surface in a traceback shown to the
user (spec section 13, rule 8).
"""

from __future__ import annotations


class ApiError(Exception):
    """Base class for Redmine and GLPI transport/protocol failures."""


class RedmineError(ApiError):
    """A Redmine request failed."""


class GlpiError(ApiError):
    """A GLPI request failed."""


class GlpiRightMissingError(GlpiError):
    """GLPI answered ERROR_RIGHT_MISSING - the API profile lacks permissions."""


class GlpiItemtypeNotFoundError(GlpiError):
    """GLPI answered ERROR_ITEMTYPE_NOT_FOUND for the requested itemtype."""
