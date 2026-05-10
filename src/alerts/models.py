"""Domain models for the alerts module."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class AlertStatus(str, Enum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class TelegramSendResult:
    success: bool
    message_id: Optional[int] = None
    error: Optional[str] = None
