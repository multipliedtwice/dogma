# Book 1 clip precision gate

The Book 1 review queue has 3,530 immutable sentence candidates. Saved voice and mood answers in `training-work/character-rb1/fine-tune-all/review-draft.json` stay keyed to those IDs.

The old quality screen compared EPUB text with ASR text from the same 30-second transcription used to choose word timestamps. It did not transcribe the finished WAV or establish its exact cut edges. Its `screen_pass` value must not authorize training.

The replacement check is:

1. Decode each frozen source MP3 and move each candidate edge to the midpoint of a nearby quiet gap (FFmpeg silence threshold −35 dB for at least 120 ms; shift at most 650 ms). Save the new WAV under `precision-v2/clips/`. Hold candidates without a nearby gap.
2. Transcribe every new WAV with the local Qwen3-ASR-0.6B model, without supplying the EPUB text as a prompt. Keep the raw heard text and token edits.
3. Accept only an exact lexical match to the EPUB, with unchanged source and WAV hashes, 24 kHz mono PCM, 1–30 second duration, at least 40 ms of quiet audio at both edges, and no unsafe inward edge shift.
4. Hold mixed-voice candidates, any overlapping accepted audio, and any clip whose audio order conflicts with the EPUB order. A saved listener boundary report also holds the affected clip.
5. Publish `precision-v2/status.json` only after every re-cut WAV has an ASR result. The export script checks the status, audio hashes, and audit hashes again before writing dataset rows. The older candidate exports are retired.

Run from the repository root:

```powershell
.\training-work\.venv-asr\Scripts\python.exe scripts\repair_book1_clip_edges.py
.\training-work\.venv-asr\Scripts\python.exe scripts\audit_book1_clip_transcripts.py --repairs training-work\character-rb1\fine-tune-all\precision-v2\repairs.json
.\training-work\.venv-asr\Scripts\python.exe scripts\build_book1_precision_status.py
.\training-work\.venv-asr\Scripts\python.exe -m unittest verification.test_book1_training_export -q
```

The ASR step resumes from `precision-v2/clip-asr-audit.jsonl`. The final status records model and input hashes. Clips with different spoken wording remain on hold; their text is never silently changed to an ASR guess. This gate checks words and cut edges, while voice family and mood still require the separate listener review.
