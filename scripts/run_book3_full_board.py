"""Prepare the complete Book 3 bucket board with frozen Books 1+2 models.

The run is resumable.  Book 3 labels never enter the predictors before this
untouched evaluation, and every prediction remains a human-review proposal.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import time


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "training-work/character-rb3/labeling-full"
SOURCE = ROOT / "training-work/character-rb3/complete/source"
BOARD = ROOT / "training-work/character-rb3-full/fine-tune-all"
EPUB = ROOT / "content/reality-benders-3.epub"
MODEL = ROOT / "training-work/series-label-predictor/books12-v1"
TTS = ROOT / "training-work/.venv-tts/Scripts/python.exe"
ASR = ROOT / "training-work/.venv-asr/Scripts/python.exe"
EMOTION = ROOT / "training-work/.venv-emotion-benchmark/Scripts/python.exe"
EPUB_SHA256 = "bb546ee442d31566c2eb8ece698c3c47be5558e5fd9a031a0853fe9f0ce28025"
TRACKS = [f"{number:02d}" for number in range(33)]


def sha(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n",
                         encoding="utf-8")
    temporary.replace(path)


def status(stage: str, state: str, **extra) -> None:
    payload = {"schema": 1, "stage": stage, "state": state,
               "updated_at": datetime.now(timezone.utc).isoformat(),
               "epub_sha256": EPUB_SHA256, **extra}
    write(OUT / "board-run-progress.json", payload)
    with (OUT / "board-run.log").open("a", encoding="utf-8") as stream:
        stream.write(f"{payload['updated_at']} {state}: {stage}\n")
    print(f"{state}: {stage}", flush=True)


def verify_inputs() -> dict:
    if sha(EPUB) != EPUB_SHA256:
        raise ValueError("Book 3 EPUB changed")
    missing = [track for track in TRACKS
               if not (ROOT / "content/reality-benders-3" / f"{track}.mp3").is_file()]
    if missing:
        raise FileNotFoundError("Missing Book 3 tracks: " + ", ".join(missing))
    latest = json.loads((MODEL / "latest.json").read_text(encoding="utf-8"))
    for name, expected in latest["artifacts"].items():
        if sha(MODEL / name) != expected:
            raise ValueError(f"Frozen predictor changed: {name}")
    if latest.get("next_pristine_gate") != "reality-benders-3":
        raise ValueError("The frozen predictor is not bound to the Book 3 gate")
    return latest


def protected_review_hashes() -> dict:
    result = {}
    for book, board_folder in (("reality-benders-1", "character-rb1"),
                               ("reality-benders-2-full", "character-rb2-full")):
        protected = ROOT / "manual-labeling" / book / "review.json"
        working = ROOT / "training-work" / board_folder / "fine-tune-all/review-draft.json"
        if not protected.is_file() or not working.is_file() or protected.read_bytes() != working.read_bytes():
            raise ValueError(f"Protected and working review differ before Book 3: {book}")
        result[book] = sha(protected)
    return result


def prepare_source() -> None:
    target = OUT / "source"
    target.mkdir(parents=True, exist_ok=True)
    for name in ("book.json", "preparation.json", "speech-inputs.jsonl"):
        source = SOURCE / name
        destination = target / name
        if destination.is_file() and sha(destination) != sha(source):
            raise ValueError(f"Prepared Book 3 source changed: {name}")
        if not destination.exists():
            shutil.copyfile(source, destination)


def command(stage: str, *arguments, python: Path = TTS) -> None:
    status(stage, "started")
    with (OUT / "board-run.log").open("a", encoding="utf-8") as stream:
        subprocess.run([str(python), "-X", "utf8", "-u", *map(str, arguments)],
                       cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT, check=True)
    status(stage, "finished")


def wait_for_context_lanes() -> None:
    expected = [OUT / f"context-lane-{name}.json" for name in ("a", "b", "c")]
    if not all(path.is_file() for path in expected):
        status("context", "waiting", lanes=[path.name for path in expected])
    while not all(path.is_file() for path in expected):
        time.sleep(30)
    command("merge-context", ROOT / "scripts/merge_book_context_lanes.py",
            "--folder", OUT, "--lanes", "a", "b", "c")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-stage", choices=("audio", "features", "board"), default="audio")
    args = parser.parse_args()
    latest = verify_inputs()
    protected_before = protected_review_hashes()
    OUT.mkdir(parents=True, exist_ok=True)
    prepare_source()
    status("inputs", "verified", predictor_artifacts=latest["artifacts"])

    if args.from_stage == "audio":
        base = [ROOT / "scripts/prepare_book_label_pilot.py"]
        common = ["--book", "reality-benders-3", "--book-id", "reality-benders-3",
                  "--clip-prefix", "rb3", "--chapter-offset", "3",
                  "--out", OUT, "--tracks", *TRACKS]
        for stage in ("asr", "align", "clips", "audit"):
            command("audio-" + stage, *base, stage, *common, python=ASR)

    if args.from_stage in ("audio", "features"):
        command("features-wave", ROOT / "scripts/extract_label_pilot_features.py",
                "wave", "--out", OUT)
        command("features-emotion-frame", ROOT / "scripts/extract_label_pilot_features.py",
                "emotion-frame", "--out", OUT, python=EMOTION)
        command("features-combine", ROOT / "scripts/extract_label_pilot_features.py",
                "combine", "--out", OUT, "--emotion-frame-mean")
        suggestions = OUT / "suggestions-books12-v1.jsonl"
        if suggestions.exists():
            suggestions.unlink()
        command("predict", ROOT / "scripts/predict_book_labels.py",
                "--model-directory", MODEL, "--features", OUT / "features.npz",
                "--manifest", OUT / "checked-clips.jsonl", "--output", suggestions)
        command("hybrid-mood", ROOT / "scripts/train_hybrid_mood_suggestions.py",
                "--out", OUT, "--frozen-report",
                ROOT / "training-work/character-rb2/pilot/hybrid/report.json",
                "--candidate-top1", "--book-id", "reality-benders-3",
                "--suggestions", suggestions.name)
        wait_for_context_lanes()

    if args.from_stage == "board" and not (OUT / "context.json").is_file():
        wait_for_context_lanes()

    command("build-board", ROOT / "scripts/build_label_pilot_buckets.py",
            "--source-folder", OUT, "--board-folder", BOARD,
            "--manual-book-id", "reality-benders-3", "--book-label", "Book 3",
            "--full-book", "--candidate-model-proposals", "--update-unreviewed",
            "--source-epub", EPUB, "--port", "1440",
            "--suggestions", "suggestions-books12-v1.jsonl")
    command("series-prefill", ROOT / "scripts/build_book3_voice_prefill.py", "--apply")
    command("verify-board", ROOT / "scripts/verify_book3_full_board.py")
    protected_after = protected_review_hashes()
    if protected_after != protected_before:
        raise ValueError("A protected Book 1 or Book 2 review changed during Book 3 preparation")
    status("complete", "finished", board=str(BOARD.relative_to(ROOT)),
           url="http://127.0.0.1:1440/character-rb3-full/fine-tune-all/",
           protected_review_hashes=protected_after)


if __name__ == "__main__":
    main()
