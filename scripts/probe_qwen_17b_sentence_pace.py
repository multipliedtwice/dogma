"""Test native sentence-level Qwen pacing without time stretching or added silence."""

import json
import re
import sys
from pathlib import Path

import numpy as np
import soundfile as sf
import torch
from qwen_tts import Qwen3TTSModel

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.probe_qwen_17b_pace import metrics

OUTPUT = ROOT / "training-work/qwen-17b-pace-probe"
MODEL = ROOT / "training-work/models/Qwen3-TTS-12Hz-1.7B-Base"
BANK = ROOT / "training-work/character-rb1/provisional-narrator-reference-bank.json"


def main():
    data = json.loads((OUTPUT / "result.json").read_text(encoding="utf-8"))
    original = data["text"]
    pieces = re.split(r"(?<=[.!?…])\s+(?=[А-ЯЁ])", original.strip())
    if len(pieces) != 4 or " ".join(pieces) != original.strip():
        raise RuntimeError("Unexpected sentence boundaries")
    ref = json.loads(BANK.read_text(encoding="utf-8"))["candidate_references"][9]
    dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
    model = Qwen3TTSModel.from_pretrained(str(MODEL), device_map="cuda:0", dtype=dtype,
                                          attn_implementation="sdpa", local_files_only=True)
    prompt = model.create_voice_clone_prompt(ref_audio=ref["audio"], ref_text=ref["text"],
                                              x_vector_only_mode=False)
    audio = []
    for index, piece in enumerate(pieces, 1):
        torch.manual_seed(42)
        wavs, rate = model.generate_voice_clone(text=piece, language="Auto",
                                                 voice_clone_prompt=prompt, max_new_tokens=2048)
        chunk = np.asarray(wavs[0], dtype=np.float32).reshape(-1)
        audio.append(chunk)
        print(json.dumps({"sentence": index, "duration": round(len(chunk)/rate, 2)}), flush=True)
    combined = np.concatenate(audio)
    name = "unhurried-sentences.wav"
    sf.write(OUTPUT / name, combined, rate, format="WAV", subtype="PCM_16")
    result = {"label": "unhurried-sentences", "file": name, "reference_id": ref["id"],
              "parts": pieces, "added_silence_seconds": 0,
              **metrics(combined, rate, data["words"])}
    data["results"].append(result)
    (OUTPUT / "result.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    page = (OUTPUT / "review.html").read_text(encoding="utf-8")
    card = f"<section><h2>unhurried, sentence by sentence</h2><audio controls preload='none' src='{name}'></audio><p>{result['duration_seconds']} seconds · {result['words_per_minute']} words/minute · {result['pause_seconds_120ms']} seconds of measured pauses. No silence or time stretching added.</p></section>"
    (OUTPUT / "review.html").write_text(page.replace("</html>", card + "</html>"), encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key != "parts"}), flush=True)


if __name__ == "__main__":
    main()
