"""Build an explicitly unreviewed local clone pack for book-1 listening drafts."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "training-work" / "character-rb1"
SPEECH = ROOT / "training-work/models/Qwen3-TTS-12Hz-0.6B-Base"
BONSAI = Path("C:/Users/multi/.ollama/models/blobs/sha256-ead25897bc034fa52569d0c6d054ce38216f95db09900c8add8f6bbfb370cff1")
SERIES_ALIASES = ROOT / "training" / "series_character_aliases" / "reality-benders.json"


def copy_file(source: Path, target: Path):
    if not source.is_file():
        raise FileNotFoundError(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.is_file() or target.stat().st_size != source.stat().st_size:
        shutil.copy2(source, target)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "training-work/book1-provisional-pack")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if BONSAI.open("rb").read(4) != b"GGUF":
        raise ValueError("Local Bonsai text model is not GGUF")
    copy_file(BONSAI, output / "models/text/model.gguf")
    for source in SPEECH.rglob("*"):
        if source.is_file() and ".cache" not in source.parts:
            copy_file(source, output / "models/speech" / source.relative_to(SPEECH))
    narrator = json.loads((BASE / "provisional-narrator-reference-bank.json").read_text(encoding="utf-8"))["candidate_references"][0]
    game = json.loads((BASE / "provisional-game-ui-reference-bank.json").read_text(encoding="utf-8"))["candidate_references"][0]
    characters = json.loads((BASE / "provisional-voice-reference-bank.json").read_text(encoding="utf-8"))["references"]
    names = {entry["id"]: [entry["name"]] for entry in
             json.loads((BASE / "character-reference-coverage.json").read_text(encoding="utf-8"))["roles"]}
    # This registry was checked against the EPUB.  Keep its exact aliases in the
    # pack so a later book can bind a callsign such as Комар without fuzzy name
    # matching or a family ID in casting rules.
    if SERIES_ALIASES.is_file():
        for character in json.loads(SERIES_ALIASES.read_text(encoding="utf-8"))["characters"]:
            names[character["voice_id"]] = list(dict.fromkeys([character["name"], *character.get("aliases", [])]))
    choices = [("vlad-rimsky", "Narrator · provisional", "narrator", narrator),
               ("mechanical_game_voice", "Game UI · provisional", "mechanical", game)]
    choices += [(row["character_id"], row["character_id"] + " · provisional", "character", row["candidate_references"][0])
                for row in characters if row["candidate_references"]]
    voices = []
    trace = []
    for voice_id, name, role, clip in choices:
        source = Path(clip["audio"])
        if hashlib.sha256(source.read_bytes()).hexdigest() != clip["audio_sha256"]:
            raise ValueError(f"Reference changed: {voice_id}")
        relative = f"voices/{voice_id}/neutral.wav"
        copy_file(source, output / relative)
        voices.append({"id": voice_id, "name": name, "role": role, "tags": [],
                       **({"character_names": names[voice_id]} if voice_id in names else {}),
                       "references": {"neutral": {"audio": relative, "text": clip["text"]}}})
        trace.append({"voice_id": voice_id, "candidate_id": clip["id"], "source_id": clip["source_id"],
                      "audio_sha256": clip["audio_sha256"], "human_audio_reviewed": False})
    (output / "draft-provenance.json").write_text(json.dumps({"schema": 1, "provisional": True, "references": trace,
        "limitations": "Unreviewed audiobook clips. No channel effects. Unknown speakers and Tamara fall back to narration."}, ensure_ascii=False, indent=2), encoding="utf-8")
    pack = {"schema": 1, "name": "Reality Benders 1 listening draft · provisional",
            "text_model": {"path": "models/text/model.gguf", "context": 8192},
            "speech": {"path": "models/speech", "variant": "clone", "instruction_control": False,
                       "language_mode": "auto", "max_new_tokens": 2048},
            "voices": voices, "rights": {"voice_assets": "User attested authorization for the book and Vlad Rimsky recordings. Experimental, unreviewed references."},
            "files": {}}
    (output / "pack.json").write_text(json.dumps(pack, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output), "voices": len(voices), "provisional": True}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
