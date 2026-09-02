import hashlib
import re
from pathlib import Path


def video_cache_key(filename):
    path = Path(filename).expanduser().resolve()
    stat = path.stat()
    identity = f"{path}\0{stat.st_size}\0{stat.st_mtime_ns}"
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", path.stem).strip("._-") or "video"
    extension = re.sub(r"[^A-Za-z0-9]+", "", path.suffix.lower()) or "file"
    return f"{stem}-{extension}-{digest}"
