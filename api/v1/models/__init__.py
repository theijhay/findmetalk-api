"""Every model is imported here so Alembic sees it on ``Base.metadata``.

A new model file is added to this list in the same change that creates it.
"""

from api.v1.models.admin.audit import AuditLog
from api.v1.models.admin.config import ConfigEntry
from api.v1.models.auth.auth import AuthSession, EmailCode, User
from api.v1.models.base_model import BaseTableModel

__all__ = ["AuditLog", "AuthSession", "BaseTableModel", "ConfigEntry", "EmailCode", "User"]
