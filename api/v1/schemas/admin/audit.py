"""Supported audit actions (TR-7, SAFE-9)."""

from enum import StrEnum


class AuditAction(StrEnum):
    USER_BLOCKED = "user.blocked"
    REPORT_CREATED = "report.created"
    USER_SUSPENDED = "user.suspended"
    USER_BANNED = "user.banned"
    USER_WARNED = "user.warned"
    REPORT_DISMISSED = "report.dismissed"
    APPEAL_DECIDED = "appeal.decided"
    RECORDING_ACCESSED = "recording.accessed"
    RECORDING_DELETED = "recording.deleted"
    CONFIG_UPDATED = "config.updated"
    CIRCLE_MEMBER_REMOVED = "circle.member_removed"
    MODERATOR_GRANTED = "moderator.granted"
    MODERATOR_REVOKED = "moderator.revoked"
