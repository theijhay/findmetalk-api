"""Email delivery boundary; the console provider keeps addresses and codes out of logs."""

from typing import Protocol

from api.utils.logger import logger


class EmailSender(Protocol):
    async def send_code(self, *, email: str, code: str) -> None: ...


class ConsoleEmailSender:
    async def send_code(self, *, email: str, code: str) -> None:
        logger.info("development email delivery", extra={"outcome": "accepted"})


def get_email_sender() -> EmailSender:
    return ConsoleEmailSender()
