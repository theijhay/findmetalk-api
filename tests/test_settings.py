import pytest

from api.utils.settings import Settings

_REQUIRED = {"DB_URL": "postgresql://u:p@h/d", "REDIS_URL": "redis://h:6379/0"}


def test_the_product_name_comes_from_config() -> None:
    assert Settings(**_REQUIRED).APP_NAME == "FindMeTalk"  # type: ignore[arg-type]
    assert Settings(**_REQUIRED, APP_NAME="Renamed").APP_NAME == "Renamed"  # type: ignore[arg-type]


def test_cors_origins_are_split_and_trimmed() -> None:
    s = Settings(**_REQUIRED, CORS_ORIGINS=" https://a.example , ,https://b.example ")  # type: ignore[arg-type]
    assert s.cors_origins == ["https://a.example", "https://b.example"]


def test_an_unknown_environment_name_is_refused() -> None:
    with pytest.raises(ValueError):
        Settings(**_REQUIRED, APP_ENV="prod")  # type: ignore[arg-type]


def test_database_and_redis_urls_are_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DB_URL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    with pytest.raises(ValueError):
        Settings(_env_file=None)
