"""Install the shared character review view without touching corpus data."""
from __future__ import annotations

import argparse
import hashlib
import re
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ASSET = ROOT / "scripts" / "bucket_character_mode.js"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def install(board: Path) -> None:
    required = [board / "candidates.jsonl", board / "review-draft.json", board / "bucket-review.js"]
    missing = [path for path in required if not path.is_file()]
    if missing:
        raise FileNotFoundError("Bucket board is incomplete: " + ", ".join(map(str, missing)))
    protected = {path: digest(path) for path in required[:2]}
    shutil.copyfile(ASSET, board / "bucket-character-mode.js")
    for name in ("index.html", "buckets.html"):
        page = board / name
        if not page.is_file():
            continue
        html = page.read_text(encoding="utf-8")
        if "bucket-character-mode.js" not in html:
            pattern = r'(<script src="bucket-review\.js(?:\?[^" ]*)?"></script>)'
            html, count = re.subn(pattern, r'\1<script src="bucket-character-mode.js?v=1"></script>', html, count=1)
            if count != 1:
                raise ValueError(f"Could not locate bucket-review.js in {page}")
            page.write_text(html, encoding="utf-8")
    for path, before in protected.items():
        if digest(path) != before:
            raise RuntimeError(f"Installer changed protected board data: {path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--board", action="append", required=True)
    args = parser.parse_args()
    for value in args.board:
        target = (ROOT / value).resolve()
        install(target)
        print(f"Installed character review mode in {target}")
