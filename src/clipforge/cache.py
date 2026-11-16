"""Cache management and idempotency helpers for pipeline stages."""

import hashlib
import json
from pathlib import Path
from typing import Any, Optional


def get_video_id(url_or_path: str) -> str:
    """Generate a clean, reproducible ID for a URL or local file path."""
    clean = url_or_path.strip()
    if "youtube.com/watch?v=" in clean:
        vid = clean.split("v=")[1].split("&")[0]
        return f"yt_{vid}"
    if "youtu.be/" in clean:
        vid = clean.split("youtu.be/")[1].split("?")[0]
        return f"yt_{vid}"
    # Local file or generic URL
    name = Path(clean).stem
    h = hashlib.md5(clean.encode()).hexdigest()[:8]
    safe_name = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in name)[:24]
    return f"{safe_name}_{h}"


class CacheManager:
    def __init__(self, video_id: str, base_dir: Path = Path("cache")):
        self.video_id = video_id
        self.dir = base_dir / video_id
        self.dir.mkdir(parents=True, exist_ok=True)

    def path(self, filename: str) -> Path:
        return self.dir / filename

    def exists(self, filename: str) -> bool:
        return (self.dir / filename).exists()

    def load_json(self, filename: str) -> Optional[Any]:
        p = self.dir / filename
        if not p.exists():
            return None
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)

    def save_json(self, filename: str, data: Any) -> Path:
        p = self.dir / filename
        with open(p, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return p
