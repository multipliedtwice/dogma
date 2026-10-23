"""Check rendered Higgs opening passages for dropped or repeated words with local ASR."""

import json
import re
import sqlite3
from difflib import SequenceMatcher
from pathlib import Path

import torch
from qwen_asr import Qwen3ASRModel


ROOT = Path(__file__).resolve().parents[1]
PILOT = ROOT / "training-work/higgs-tts-3-pilot"
DATA = ROOT / "training-work/book1-listening-app/library.sqlite3"
SOURCE = "9eaffc3307f50ca0077e3332"


def words(value: str) -> list[str]:
    return re.findall(r"[а-яёa-z0-9]+", value.casefold())


def main() -> None:
    wavs = sorted((PILOT / "opening").glob("*.wav"))
    if not wavs:
        raise RuntimeError("No Higgs opening audio exists")
    model = Qwen3ASRModel.from_pretrained(
        str(ROOT / "training-work/models/Qwen3-ASR-0.6B"), device_map="cuda:0",
        dtype=torch.bfloat16, attn_implementation="sdpa", local_files_only=True)
    report = []
    with sqlite3.connect(DATA) as db:
        for wav in wavs:
            source = db.execute("SELECT spoken FROM segments WHERE generation_id=? AND id=?",
                                (SOURCE, wav.stem)).fetchone()
            if not source:
                raise RuntimeError(f"Unknown segment: {wav.stem}")
            heard = model.transcribe(str(wav), language="Russian")[0].text
            expected_tokens, heard_tokens = words(source[0]), words(heard)
            edits = [dict(kind=op, expected=expected_tokens[i:j], heard=heard_tokens[k:l])
                     for op, i, j, k, l in SequenceMatcher(None, expected_tokens, heard_tokens).get_opcodes()
                     if op != "equal"]
            row = {"id": wav.stem, "expected": source[0], "asr": heard,
                   "expected_words": len(expected_tokens), "asr_words": len(heard_tokens),
                   "edits": edits}
            report.append(row)
            print(json.dumps({"id": wav.stem, "expected_words": row["expected_words"],
                              "asr_words": row["asr_words"], "edits": edits}, ensure_ascii=True), flush=True)
    (PILOT / "asr-audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
