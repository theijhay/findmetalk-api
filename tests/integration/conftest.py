"""Apply real revisions before integration fixtures use durable tables."""

import asyncio
import sys

import pytest


@pytest.fixture(scope="session")
async def migrated_database() -> None:
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "alembic",
        "upgrade",
        "head",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    assert process.returncode == 0, (stdout + stderr).decode()
