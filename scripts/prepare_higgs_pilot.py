"""Prepare a matched Book 1 voice-clone pace test for Higgs TTS 3.

This only stages local inputs. It does not download weights or send audio anywhere.
"""

import json
import re
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "training-work/qwen-17b-pace-probe/result.json"
BANK = ROOT / "training-work/character-rb1/provisional-narrator-reference-bank.json"
OUTPUT = ROOT / "training-work/higgs-tts-3-pilot"
MODEL = "bosonai/higgs-tts-3-4b"
TAG = re.compile(r"<\|(?:emotion|style|prosody|sfx):[a-z_]+\|>")


def main() -> None:
    source = json.loads(SOURCE.read_text(encoding="utf-8"))
    text = source["text"].strip()
    references = json.loads(BANK.read_text(encoding="utf-8"))["candidate_references"]
    reference = references[0]
    sentences = re.split(r"(?<=[.!?\u2026])\s+(?=[\u0410-\u042f\u0401])", text)
    if len(sentences) != 4 or " ".join(sentences) != text:
        raise ValueError("Book 1 test passage changed; review the prompt before staging it")

    plain = text
    slow = " ".join("<|prosody:speed_slow|>" + sentence for sentence in sentences)
    paced = " ".join(
        "<|prosody:speed_slow|>" + sentence + (" <|prosody:pause|>" if index in (0, 2) else "")
        for index, sentence in enumerate(sentences)
    )
    prompts = {"plain": plain, "slow": slow, "slow_with_pauses": paced}
    for name, prompt in prompts.items():
        if re.sub(r"\s+", " ", TAG.sub("", prompt)).strip() != text:
            raise ValueError(f"{name} changes the book's words")

    OUTPUT.mkdir(parents=True, exist_ok=True)
    reference_path = OUTPUT / "vlad_reference.wav"
    shutil.copyfile(reference["audio"], reference_path)
    manifest = {
        "model": MODEL,
        "source_segment_id": source["segment_id"],
        "source_text": text,
        "reference_audio": reference_path.name,
        "reference_text": reference["text"].strip(),
        "reference_id": reference["id"],
        "reference_seconds": reference["duration"],
        "playback_rate": 1.0,
        "variants": [{"name": name, "input": prompt} for name, prompt in prompts.items()],
        "evaluation": [
            "Vlad likeness", "Russian pronunciation and stresses", "Natural speaking pace",
            "Comma and sentence pauses", "Exact words; no omissions or repeats",
        ],
        "status": "prepared_only_no_higgs_inference_run",
    }
    (OUTPUT / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(OUTPUT / "manifest.json")


if __name__ == "__main__":
    main()
