"""Install the maintained Book 1 bucket-labeling UI into its generated board."""
from __future__ import annotations

import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "scripts"
TARGET = ROOT / "training-work/character-rb1/fine-tune-all"


def main() -> None:
    required = [
        TARGET / "candidates.jsonl",
        TARGET / "summary.json",
        TARGET / "review-draft.json",
    ]
    missing = [path for path in required if not path.is_file()]
    if missing:
        raise FileNotFoundError("Book 1 board data is missing: " + ", ".join(map(str, missing)))

    TARGET.mkdir(parents=True, exist_ok=True)
    html = (SOURCE / "book1_bucket_review.html").read_bytes()
    for name in ("index.html", "buckets.html"):
        (TARGET / name).write_bytes(html)
    for source, target in (
        ("book1_bucket_review.js", "bucket-review.js"),
        ("book1_bucket_acoustics.js", "bucket-acoustics.js"),
        ("book1_review_shared.js", "review-shared.js"),
        ("book_navigation.js", "book-navigation.js"),
        ("bucket_voice_family_editor.js", "voice-family-editor.js"),
        ("bucket_character_mode.js", "bucket-character-mode.js"),
    ):
        shutil.copyfile(SOURCE / source, TARGET / target)
    from install_bucket_hls_ui import install as install_hls
    install_hls(TARGET)
    print(f"Installed Book 1 bucket UI at {TARGET / 'index.html'}")


if __name__ == "__main__":
    main()
