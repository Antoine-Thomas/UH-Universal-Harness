"""Le cache FIFO et la limite de débit, seuls (pas de réseau, pas de serveur).

Le cache écrit dans un dossier temporaire ; l'horloge de la limite est injectée,
donc aucun test n'attend une minute.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from uh import media_store

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
URL = "https://api.openverse.org/v1/images/x/thumb/"


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


# --------------------------------------------------------------------------- #
# la limite de débit
# --------------------------------------------------------------------------- #
def test_thirty_vignettes_a_minute_then_a_refusal() -> None:
    clock = Clock()
    limiter = media_store.RateLimiter(clock=clock)

    for index in range(media_store.PER_MINUTE):
        allowed, reason = limiter.allow("s1")
        assert allowed, (index, reason)

    allowed, reason = limiter.allow("s1")
    assert not allowed
    assert "30 par minute" in reason


def test_the_minute_window_slides() -> None:
    clock = Clock()
    limiter = media_store.RateLimiter(clock=clock)
    for _ in range(media_store.PER_MINUTE):
        limiter.allow("s1")
    assert not limiter.allow("s1")[0]

    clock.advance(61)
    assert limiter.allow("s1")[0]


def test_the_hour_window_stops_a_slow_drip() -> None:
    clock = Clock()
    limiter = media_store.RateLimiter(clock=clock)
    for _ in range(media_store.PER_HOUR):
        assert limiter.allow("s1")[0]
        clock.advance(11)  # jamais 30 dans la même minute, mais 300 en une heure

    allowed, reason = limiter.allow("s1")
    assert not allowed
    assert "300 par heure" in reason

    clock.advance(3601)
    assert limiter.allow("s1")[0], "la fenêtre horaire se vide"


def test_two_sessions_have_two_budgets() -> None:
    clock = Clock()
    limiter = media_store.RateLimiter(clock=clock)
    for _ in range(media_store.PER_MINUTE):
        limiter.allow("s1")

    assert not limiter.allow("s1")[0]
    assert limiter.allow("s2")[0], "une autre session ne paie pas pour la première"


def test_an_empty_key_falls_back_to_one_bucket() -> None:
    clock = Clock()
    limiter = media_store.RateLimiter(clock=clock)
    for _ in range(media_store.PER_MINUTE):
        limiter.allow("")

    assert not limiter.allow("   ")[0]


def test_forget_clears_a_session() -> None:
    limiter = media_store.RateLimiter(clock=Clock())
    for _ in range(media_store.PER_MINUTE):
        limiter.allow("s1")
    limiter.forget("s1")
    assert limiter.allow("s1")[0]


# --------------------------------------------------------------------------- #
# le cache disque
# --------------------------------------------------------------------------- #
def test_a_stored_thumbnail_comes_back_unchanged(tmp_path: Path) -> None:
    cache = media_store.ThumbCache(tmp_path / "cache")

    cache.put(URL, mime="image/png", data=PNG)
    hit = cache.get(URL)

    assert hit is not None
    assert hit.mime == "image/png"
    assert hit.data == PNG


def test_the_extension_follows_the_detected_type(tmp_path: Path) -> None:
    cache = media_store.ThumbCache(tmp_path / "cache")
    cache.put(URL, mime="image/webp", data=b"RIFF....WEBP")

    assert (tmp_path / "cache").glob("*.webp")


def test_an_unknown_type_is_not_cached(tmp_path: Path) -> None:
    cache = media_store.ThumbCache(tmp_path / "cache")
    cache.put(URL, mime="image/svg+xml", data=b"<svg/>")

    assert cache.get(URL) is None
    assert cache.total() == 0


def test_an_oversized_body_is_not_cached(tmp_path: Path) -> None:
    cache = media_store.ThumbCache(tmp_path / "cache")
    cache.put(URL, mime="image/png", data=PNG + b"\x00" * (media_store.MAX_BYTES + 1))

    assert cache.get(URL) is None


def test_an_evicted_thumbnail_is_gone(tmp_path: Path) -> None:
    cache = media_store.ThumbCache(tmp_path / "cache", limit=1000)
    cache.put(URL, mime="image/png", data=PNG)
    assert cache.get(URL) is not None

    cache.put(URL + "2", mime="image/png", data=b"\x89PNG\r\n\x1a\n" + b"\x00" * 1500)

    assert cache.total() <= 1000, "le plus ancien est parti pour rester sous la limite"


def test_the_purge_removes_the_oldest_first(tmp_path: Path) -> None:
    import os
    import time

    cache = media_store.ThumbCache(tmp_path / "cache", limit=2500)
    old = tmp_path / "cache"
    old.mkdir(parents=True, exist_ok=True)
    big = PNG + b"\x00" * 1200  # ~1,2 Ko : trois fichiers dépassent 2,5 Ko

    cache.put(URL + "a", mime="image/png", data=big)
    ancient = list(old.glob("*.png"))[0]
    was = time.time() - 3600
    os.utime(ancient, (was, was))

    cache.put(URL + "b", mime="image/png", data=big)
    cache.put(URL + "c", mime="image/png", data=big)
    cache.purge()

    assert not ancient.exists(), "le fichier le plus ancien part le premier"
    assert cache.total() <= 2500
    assert len(list(old.glob("*.png"))) >= 1


def test_a_missing_folder_is_not_an_error(tmp_path: Path) -> None:
    cache = media_store.ThumbCache(tmp_path / "jamais-cree")

    assert cache.get(URL) is None
    assert cache.total() == 0
    assert cache.purge() == 0


def test_a_corrupt_entry_is_ignored(tmp_path: Path) -> None:
    cache = media_store.ThumbCache(tmp_path / "cache")
    cache.put(URL, mime="image/png", data=PNG)
    stored = next((tmp_path / "cache").glob("*.png"))
    stored.write_bytes(b"pas une image")

    hit = cache.get(URL)

    assert hit is not None, "le fichier est là"
    assert hit.data == b"pas une image", "et son contenu est ce qu'il est"


def test_clear_empties_the_cache(tmp_path: Path) -> None:
    cache = media_store.ThumbCache(tmp_path / "cache")
    cache.put(URL, mime="image/png", data=PNG)

    assert cache.clear() == 1
    assert cache.get(URL) is None


def test_the_default_limit_is_64_megabytes() -> None:
    assert media_store.CACHE_BYTES == 64 * 1024 * 1024
    assert media_store.PER_MINUTE == 30
    assert media_store.PER_HOUR == 300


@pytest.mark.parametrize("mime", ["image/png", "image/jpeg", "image/gif", "image/webp"])
def test_every_drawable_type_has_an_extension(mime: str) -> None:
    assert mime in media_store.EXTENSIONS
