"""Media preparation: what the model receives, and what stays on disk.

The rules (P1-14), and the reason each one exists:

* **the file the operator chose is never destroyed.** A 4K screenshot is resized
  into a *sibling* (`<name>-sent.jpg`) under a byte budget; the original stays where
  it was until « Garder » or a purge decides otherwise.
* **a 4K image does not leave at 8 MB.** Under :data:`IMAGE_BUDGET` bytes and
  :data:`MAX_SIDE` pixels on the long side, which is what a vision model reads
  anyway - sending more costs tokens, time and money for nothing measured.
* **a video is not sent as a video.** Keyframes are extracted, and the audio track
  too when there is one: that is what a text-or-vision model can actually use.
* **temporaries are purged.** Everything written here lands under
  ``$UH_HOME/uploads/<session>/derived/`` and :func:`purge` removes it - the
  interface calls it after a turn, and a restart sweeps what was left.

ffmpeg is resolved through :func:`uh.audio.ffmpeg_path`, which prefers the embedded
binary the installer will ship. Absent ffmpeg is a refusal with a sentence, never a
silent "no frames".
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..audio import ffmpeg_path

#: Byte budget for one image sent to a model.
IMAGE_BUDGET = 1_000_000
#: Long side in pixels. A vision encoder downsamples to ~1k tokens anyway.
MAX_SIDE = 1536
#: Keyframes taken from a video, evenly spaced.
VIDEO_FRAMES = 4
#: Suffix of a prepared copy, so a purge never confuses it with the original.
SENT_SUFFIX = "-sent.jpg"

IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".heic", ".tiff"})
VIDEO_SUFFIXES = frozenset({".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"})
AUDIO_SUFFIXES = frozenset({".mp3", ".wav", ".flac", ".ogg", ".m4a", ".opus", ".webm"})
DOC_SUFFIXES = frozenset({".pdf", ".csv", ".html", ".htm", ".zip", ".txt", ".md", ".json"})


class MediaError(RuntimeError):
    """A refusal the page can show: nothing was destroyed."""


def kind_of(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in IMAGE_SUFFIXES:
        return "image"
    if suffix in VIDEO_SUFFIXES:
        return "video"
    if suffix in AUDIO_SUFFIXES:
        return "audio"
    if suffix in DOC_SUFFIXES:
        return "document"
    return "file"


def derived_dir(path: Path) -> Path:
    """Where prepared copies go: beside the original, in its session folder."""
    folder = path.parent / "derived"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


# --------------------------------------------------------------------------- #
# images
# --------------------------------------------------------------------------- #
def shrink_image(path: Path, *, budget: int = IMAGE_BUDGET, max_side: int = MAX_SIDE) -> Path:
    """A JPEG under ``budget`` bytes and ``max_side`` pixels. The original is untouched.

    Quality is walked down before the size is, and the size only down to
    :data:`MAX_SIDE`: below that, a screenshot's text stops being readable, and an
    unreadable image that fits the budget is not a success.
    """
    from PIL import Image  # optional dependency, imported where it is needed

    if not path.is_file():
        raise MediaError("image introuvable")
    try:
        with Image.open(path) as opened:
            image = opened.convert("RGB")
    except Exception as exc:  # noqa: BLE001 - PIL raises many things for one cause
        raise MediaError(f"image illisible : {type(exc).__name__}") from exc

    longest = max(image.size)
    if longest > max_side:
        ratio = max_side / float(longest)
        image = image.resize((max(1, int(image.width * ratio)), max(1, int(image.height * ratio))), Image.Resampling.LANCZOS)

    target = derived_dir(path) / (path.stem + SENT_SUFFIX)
    for quality in (85, 75, 65, 55, 45, 35):
        image.save(target, format="JPEG", quality=quality, optimize=True)
        if target.stat().st_size <= budget:
            return target
    # Last resort: halve until it fits, but never below 640 px on the long side.
    while target.stat().st_size > budget and max(image.size) > 640:
        image = image.resize((max(1, image.width // 2), max(1, image.height // 2)), Image.Resampling.LANCZOS)
        image.save(target, format="JPEG", quality=70, optimize=True)
    if target.stat().st_size > budget:
        raise MediaError("image impossible à réduire sous le budget sans la rendre illisible")
    return target


def image_info(path: Path) -> Dict[str, Any]:
    from PIL import Image

    with Image.open(path) as opened:
        return {
            "path": str(path),
            "bytes": path.stat().st_size,
            "width": opened.width,
            "height": opened.height,
            "format": opened.format,
        }


# --------------------------------------------------------------------------- #
# video
# --------------------------------------------------------------------------- #
def _ffprobe() -> Optional[str]:
    binary = ffmpeg_path()
    if not binary:
        return None
    sibling = Path(binary).with_name("ffprobe.exe" if os.name == "nt" else "ffprobe")
    if sibling.is_file():
        return str(sibling)
    return shutil.which("ffprobe")


def video_info(path: Path) -> Dict[str, Any]:
    """Duration, codec and audio presence - the three things the UI has to say."""
    probe = _ffprobe()
    if not probe:
        raise MediaError("ffprobe absent : impossible de lire la vidéo (ffmpeg embarqué requis)")
    out = subprocess.run(
        [probe, "-v", "error", "-show_entries",
         "format=duration:stream=codec_name,codec_type", "-of", "json", str(path)],
        capture_output=True, text=True, timeout=60, check=False,
    )
    if out.returncode != 0:
        raise MediaError(f"ffprobe a refusé le fichier : {out.stderr[-160:]}")
    import json as _json

    try:
        data = _json.loads(out.stdout or "{}")
    except ValueError as exc:
        raise MediaError("sortie ffprobe illisible") from exc
    streams = data.get("streams") or []
    video: Dict[str, Any] = next((s for s in streams if s.get("codec_type") == "video"), {})
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    return {
        "path": str(path),
        "seconds": round(float((data.get("format") or {}).get("duration") or 0), 2),
        "codec": str(video.get("codec_name") or ""),
        "audio": has_audio,
        "hevc": str(video.get("codec_name") or "").lower() in {"hevc", "h265"},
    }


def video_frames(path: Path, *, count: int = VIDEO_FRAMES) -> List[Path]:
    """Evenly spaced keyframes as JPEGs under the derived folder."""
    binary = ffmpeg_path()
    if not binary:
        raise MediaError("ffmpeg absent : aucune image clé ne peut être extraite")
    info = video_info(path)
    duration = max(0.2, float(info["seconds"] or 0.2))
    folder = derived_dir(path)
    frames: List[Path] = []
    for index in range(count):
        moment = duration * (index + 0.5) / count
        target = folder / f"{path.stem}-frame{index + 1}.jpg"
        proc = subprocess.run(
            [binary, "-y", "-ss", f"{moment:.2f}", "-i", str(path), "-frames:v", "1",
             "-vf", f"scale='min({MAX_SIDE},iw)':-2", str(target)],
            capture_output=True, text=True, timeout=180, check=False,
        )
        if proc.returncode == 0 and target.is_file():
            frames.append(target)
    if not frames:
        raise MediaError("aucune image clé extraite de cette vidéo")
    return frames


def video_audio(path: Path) -> Optional[Path]:
    """The soundtrack as 16 kHz mono WAV - what dictation can read. ``None`` if silent."""
    binary = ffmpeg_path()
    if not binary:
        return None
    info = video_info(path)
    if not info["audio"]:
        return None
    target = derived_dir(path) / f"{path.stem}-audio.wav"
    proc = subprocess.run(
        [binary, "-y", "-i", str(path), "-vn", "-ac", "1", "-ar", "16000", str(target)],
        capture_output=True, text=True, timeout=300, check=False,
    )
    return target if proc.returncode == 0 and target.is_file() else None


# --------------------------------------------------------------------------- #
# what a turn sends, and the purge
# --------------------------------------------------------------------------- #
def _mark_owner(copy: Path, original: Path) -> None:
    """Write ``<copy>.json`` recording the file this copy was made from.

    The purge needs to know whether the operator asked to keep the ORIGINAL before
    deleting the prepared copy - and a name heuristic (`-sent`, `-frame2`) would be a
    guess. A one-line sidecar is exact, cheap and disappears with its copy.
    """
    try:
        copy.with_name(copy.name + ".json").write_text(
            json.dumps({"from": str(original)}, ensure_ascii=False), encoding="utf-8"
        )
    except OSError:
        pass


def prepare(path: Path) -> Dict[str, Any]:
    """What to hand a model for this file, and what to leave on disk.

    Returns a record the composer can use without knowing the format: the prepared
    paths (one image, several frames, or the soundtrack), plus the measured facts the
    warning needs (kind, bytes, seconds, codec).
    """
    kind = kind_of(path)
    if kind == "image":
        sent = shrink_image(path)
        _mark_owner(sent, path)
        info = image_info(sent)
        return {
            "kind": "image",
            "original": str(path),
            "sent": [str(sent)],
            "bytes": info["bytes"],
            "width": info["width"],
            "height": info["height"],
        }
    if kind == "video":
        info = video_info(path)
        frames = video_frames(path)
        soundtrack = video_audio(path)
        for produced in frames + ([soundtrack] if soundtrack else []):
            _mark_owner(produced, path)
        return {
            "kind": "video",
            "original": str(path),
            "sent": [str(frame) for frame in frames] + ([str(soundtrack)] if soundtrack else []),
            "seconds": info["seconds"],
            "codec": info["codec"],
            "audio": bool(soundtrack),
        }
    if kind == "audio":
        return {"kind": "audio", "original": str(path), "sent": [], "bytes": path.stat().st_size}
    return {"kind": "document", "original": str(path), "sent": [], "bytes": path.stat().st_size}


def fingerprint(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def purge(session_dir: Path, *, keep_marked: bool = True) -> int:
    """Remove every prepared copy under a session. Returns how many files went.

    Only ``derived/`` is touched: the files the operator chose stay until they say
    otherwise, which is the promise the « Garder » button makes. A prepared copy whose
    original carries a ``.keep`` sidecar survives too - keeping the screenshot and then
    losing the copy the model saw would be a strange kind of keeping.
    """
    removed = 0
    for folder in list(Path(session_dir).rglob("derived")):
        if not folder.is_dir():
            continue
        for item in sorted(folder.rglob("*")):
            if not item.is_file():
                continue
            if item.name.endswith(".json") and item.with_name(item.name[: -len(".json")]).exists():
                # the sidecar of a copy is not a copy
                continue
            if keep_marked and _owner_is_kept(item):
                continue
            try:
                item.unlink()
                removed += 1
            except OSError:
                continue
        shutil.rmtree(folder, ignore_errors=True) if not any(folder.iterdir()) else None
    return removed


def _owner_is_kept(copy: Path) -> bool:
    """True when the file this copy came from was marked « Garder »."""
    sidecar = copy.with_name(copy.name + ".json")
    if not sidecar.is_file():
        return False
    try:
        owner = Path(str(json.loads(sidecar.read_text(encoding="utf-8")).get("from") or ""))
    except (OSError, ValueError):
        return False
    return bool(str(owner)) and owner.with_name(owner.name + ".keep").is_file()


__all__ = [
    "AUDIO_SUFFIXES",
    "DOC_SUFFIXES",
    "IMAGE_BUDGET",
    "IMAGE_SUFFIXES",
    "MAX_SIDE",
    "MediaError",
    "VIDEO_FRAMES",
    "VIDEO_SUFFIXES",
    "derived_dir",
    "fingerprint",
    "image_info",
    "kind_of",
    "prepare",
    "purge",
    "shrink_image",
    "video_audio",
    "video_frames",
    "video_info",
]
