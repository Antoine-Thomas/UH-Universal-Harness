"""Ce qui garde le proxy de vignettes utilisable : un cache borné et un débit borné.

Deux mécanismes, deux raisons différentes :

* le **cache disque** évite de redemander la même vignette au site distant à
  chaque rendu. 64 Mo, purge **FIFO** (les fichiers les plus anciens partent
  d'abord, par date de création — pas par date de lecture : un cache FIFO ne
  récompense pas ce qui est relu, il garantit qu'un fichier finit par sortir).
  Tout vit sous ``$UH_HOME/media-cache``, rien ailleurs.
* la **limite de débit** par session : 30 vignettes par minute, 300 par heure.
  C'est un proxy sortant : sans plafond, une page qui affiche cinquante résultats
  ferait cinquante requêtes à des tiers, et l'opérateur n'aurait rien demandé.

Les deux objets sont utilisés par la route ``/api/media/thumb`` et testés seuls
(horloge injectable pour le débit, dossier temporaire pour le cache).
"""

from __future__ import annotations

import hashlib
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Deque, Dict, Optional

from .images import MAX_BYTES

#: 64 Mo : de quoi tenir quelques centaines de vignettes de 320 px, sans jamais
#: devenir un dépôt de fichiers que personne ne relit.
CACHE_BYTES = 64 * 1024 * 1024
PER_MINUTE = 30
PER_HOUR = 300

#: Le type reconnu décide de l'extension du fichier en cache — jamais l'URL.
EXTENSIONS = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
}
MIME_BY_EXTENSION = {value: key for key, value in EXTENSIONS.items()}


@dataclass(frozen=True)
class Cached:
    """Une vignette relue du disque, avec le type qu'elle avait à l'écriture."""

    mime: str
    data: bytes


class RateLimiter:
    """Fenêtres glissantes par session : 30/min et 300/h.

    Deux fenêtres plutôt qu'une moyenne : la rafale de cinquante vignettes d'une
    galerie est refusée tout de suite, alors qu'une consultation lente reste
    possible toute la journée.
    """

    def __init__(
        self,
        *,
        per_minute: int = PER_MINUTE,
        per_hour: int = PER_HOUR,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.per_minute = per_minute
        self.per_hour = per_hour
        self._clock = clock
        self._hits: Dict[str, Deque[float]] = {}

    def allow(self, key: str) -> tuple[bool, str]:
        """``(True, "")`` ou ``(False, phrase)``. Une phrase montre le plafond."""
        label = (key or "").strip() or "anonyme"
        now = self._clock()
        hits = self._hits.setdefault(label, deque())
        while hits and now - hits[0] > 3600:
            hits.popleft()
        last_minute = sum(1 for stamp in hits if now - stamp <= 60)
        if last_minute >= self.per_minute:
            return False, f"trop de vignettes : {self.per_minute} par minute"
        if len(hits) >= self.per_hour:
            return False, f"trop de vignettes : {self.per_hour} par heure"
        hits.append(now)
        return True, ""

    def forget(self, key: str) -> None:
        """Oublie une session : rien ne doit survivre à la conversation."""
        self._hits.pop((key or "").strip() or "anonyme", None)


class ThumbCache:
    """Cache disque FIFO, borné en octets, sous ``$UH_HOME/media-cache``."""

    def __init__(self, root: Path, *, limit: int = CACHE_BYTES) -> None:
        self.root = Path(root)
        self.limit = limit

    # -- chemins -------------------------------------------------------- #
    def _key(self, url: str) -> str:
        return hashlib.sha256(url.encode("utf-8", "replace")).hexdigest()

    def path_for(self, url: str, mime: str) -> Path:
        return self.root / f"{self._key(url)}{EXTENSIONS.get(mime, '.bin')}"

    # -- lecture / écriture --------------------------------------------- #
    def get(self, url: str) -> Optional[Cached]:
        """La vignette si elle est là et lisible ; ``None`` sinon (jamais d'erreur)."""
        stem = self._key(url)
        try:
            candidates = list(self.root.glob(f"{stem}.*"))
        except OSError:
            return None
        for path in candidates:
            mime = MIME_BY_EXTENSION.get(path.suffix)
            if mime is None:
                continue
            try:
                data = path.read_bytes()
            except OSError:
                return None
            if len(data) > MAX_BYTES:
                return None
            return Cached(mime=mime, data=data)
        return None

    def put(self, url: str, *, mime: str, data: bytes) -> None:
        """Écrit la vignette puis purge. Un disque plein ne casse pas la réponse."""
        if mime not in EXTENSIONS or not data or len(data) > MAX_BYTES:
            return
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            target = self.path_for(url, mime)
            temporary = target.with_suffix(target.suffix + ".part")
            temporary.write_bytes(data)
            temporary.replace(target)
        except OSError:
            return
        self.purge()

    # -- entretien ------------------------------------------------------- #
    def total(self) -> int:
        total = 0
        try:
            entries = list(self.root.iterdir())
        except OSError:
            return 0
        for path in entries:
            try:
                if path.is_file():
                    total += path.stat().st_size
            except OSError:
                continue
        return total

    def purge(self) -> int:
        """Retire les plus anciens jusqu'à repasser sous la limite ; rend le nombre."""
        try:
            files = [path for path in self.root.iterdir() if path.is_file()]
        except OSError:
            return 0
        sized = []
        for path in files:
            try:
                stat = path.stat()
            except OSError:
                continue
            sized.append((stat.st_mtime, path, stat.st_size))
        total = sum(size for _, _, size in sized)
        if total <= self.limit:
            return 0
        removed = 0
        # FIFO : l'ordre de création (mtime à l'écriture), pas l'ordre de lecture.
        for _, path, size in sorted(sized):
            if total <= self.limit:
                break
            try:
                path.unlink()
            except OSError:
                continue
            total -= size
            removed += 1
        return removed

    def clear(self) -> int:
        """Vide le cache ; rend le nombre de fichiers retirés."""
        try:
            files = [path for path in self.root.iterdir() if path.is_file()]
        except OSError:
            return 0
        removed = 0
        for path in files:
            try:
                path.unlink()
                removed += 1
            except OSError:
                continue
        return removed


__all__ = [
    "CACHE_BYTES",
    "EXTENSIONS",
    "PER_HOUR",
    "PER_MINUTE",
    "Cached",
    "RateLimiter",
    "ThumbCache",
]
