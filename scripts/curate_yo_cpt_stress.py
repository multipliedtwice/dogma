"""Stream a small, auditable YO-CPT-ru stress-control training subset.

The curator never materializes the full Hugging Face dataset.  It keeps exact
accepted WAV bytes, requires the stressed denormalized text to agree with the
word alignment, and creates wrong-stress text for counterfactual training
against the same audio.  The output is deliberately separate from the
protected manual-labeling corpus.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import sys
import time
import unicodedata
import urllib.request
import wave


DATASET_ID = "NCSpeech/YO-CPT-ru"
DATASET_URL = "https://huggingface.co/datasets/NCSpeech/YO-CPT-ru"
STATE_VERSION = 4
ACUTE = "\N{COMBINING ACUTE ACCENT}"
VOWELS = "аеёиоуыэюяАЕЁИОУЫЭЮЯ"
WORD_RE = re.compile(rf"[А-Яа-яЁё{ACUTE}]+(?:[-‐‑][А-Яа-яЁё{ACUTE}]+)*")
ALIGN_RE = re.compile(r"(\S+)\((\d+(?:\.\d+)?),(\d+(?:\.\d+)?)\)")
PAUSE_RE = re.compile(r"^\[pause\d+(?:\.\d+)?\]$")
SOURCE_LICENSES = {
    "dataset_annotations_and_compilation": {
        "license": "CC BY 4.0",
        "url": "https://creativecommons.org/licenses/by/4.0/",
    },
    "source_audio_yodas2": {
        "license": "CC BY 3.0",
        "url": "https://creativecommons.org/licenses/by/3.0/",
    },
    "dataset_license_file": f"{DATASET_URL}/blob/main/LICENSE.md",
    "dataset_card": DATASET_URL,
    "notice": (
        "Derived YouTube corpus. Preserve source attribution and review the pinned "
        "upstream license before redistributing audio or trained weights."
    ),
}


class Rejected(ValueError):
    """A source row is unsuitable for strict automatic curation."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def tree_bytes(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def normalize_stress(text: str, *, alignment: bool = False) -> str:
    if not isinstance(text, str) or not text.strip():
        raise Rejected("missing-text")
    value = text.replace("[+]", "+") if alignment else text
    value = re.sub(rf"\+([{VOWELS}])", rf"\1{ACUTE}", value)
    if "+" in value:
        raise Rejected("malformed-stress-mark")
    value = unicodedata.normalize("NFC", value)
    for index, char in enumerate(value):
        if char == ACUTE and (index == 0 or value[index - 1] not in VOWELS):
            raise Rejected("stress-not-after-vowel")
    return value


def words(text: str) -> list[str]:
    return WORD_RE.findall(text)


def bare_word(word: str) -> str:
    return word.replace(ACUTE, "").lower()


def vowel_positions(word: str) -> list[int]:
    return [index for index, char in enumerate(word) if char in VOWELS]


def stress_vowel_index(word: str) -> int | None:
    positions = vowel_positions(word)
    marked = [index for index, pos in enumerate(positions) if pos + 1 < len(word) and word[pos + 1] == ACUTE]
    if len(marked) > 1:
        raise Rejected("multiple-stresses-in-word")
    return marked[0] if marked else None


def parse_alignment(value: str, duration: float) -> tuple[str, list[str]]:
    normalized = normalize_stress(value, alignment=True)
    entries = ALIGN_RE.findall(normalized)
    if not entries:
        raise Rejected("malformed-alignment")
    aligned_words: list[str] = []
    last_start = -1.0
    last_end = -1.0
    for token, start_text, end_text in entries:
        start, end = float(start_text), float(end_text)
        if start < 0 or end <= start or start + 0.03 < last_start or end + 0.03 < last_end:
            raise Rejected("nonmonotonic-alignment")
        if end > duration + 0.25:
            raise Rejected("alignment-past-audio")
        last_start, last_end = start, end
        if PAUSE_RE.match(token):
            continue
        token_words = words(token)
        if len(token_words) != 1:
            raise Rejected("malformed-alignment-token")
        aligned_words.append(token_words[0])
    return normalized, aligned_words


def validate_and_normalize(row: dict, *, max_seconds: float, min_mos: float) -> dict:
    try:
        duration = float(row["duration"])
        mos = float(row["mos_score"])
    except (KeyError, TypeError, ValueError):
        raise Rejected("missing-numeric-metadata")
    if not math.isfinite(duration) or duration <= 0 or duration > max_seconds:
        raise Rejected("duration")
    if not math.isfinite(mos) or mos < min_mos:
        raise Rejected("mos")
    if row.get("lang") != "ru":
        raise Rejected("language")
    normalized_text = normalize_stress(row.get("text_denorm", ""))
    normalized_alignment, aligned_words = parse_alignment(row.get("text_alignment", ""), duration)
    text_words = words(normalized_text)
    if [bare_word(w) for w in text_words] != [bare_word(w) for w in aligned_words]:
        raise Rejected("text-alignment-word-mismatch")
    for left, right in zip(text_words, aligned_words):
        if stress_vowel_index(left) != stress_vowel_index(right):
            raise Rejected("text-alignment-stress-mismatch")
    candidates = []
    for index, word in enumerate(text_words):
        positions = vowel_positions(word)
        stressed = stress_vowel_index(word)
        # Conservative pilot: names/capitalized tokens and ё need contextual
        # adjudication and are left to the later hard-case stage.
        if len(positions) >= 2 and stressed is not None and not word[0].isupper() and "ё" not in word.lower():
            candidates.append({
                "word_index": index,
                "word": word,
                "base_word": bare_word(word),
                "correct_vowel_index": stressed,
                "vowel_count": len(positions),
            })
    if not candidates:
        raise Rejected("no-multivowel-stress-target")
    return {
        "duration": duration,
        "mos_score": mos,
        "text": normalized_text,
        "alignment": normalized_alignment,
        "words": text_words,
        "candidates": candidates,
    }


def replace_word(text: str, word_index: int, replacement: str) -> str:
    matches = list(WORD_RE.finditer(text))
    match = matches[word_index]
    return text[:match.start()] + replacement + text[match.end():]


def put_stress(word: str, vowel_index: int) -> str:
    unmarked = word.replace(ACUTE, "")
    positions = vowel_positions(unmarked)
    if vowel_index < 0 or vowel_index >= len(positions):
        raise ValueError("vowel index is out of range")
    position = positions[vowel_index]
    return unmarked[:position + 1] + ACUTE + unmarked[position + 1:]


def make_counterfactuals(text: str, candidates: list[dict], key: str, count: int = 1) -> list[dict]:
    ranked = sorted(
        candidates,
        key=lambda item: hashlib.sha256(f"target:{key}:{item['word_index']}".encode()).digest(),
    )
    result = []
    for target in ranked:
        alternatives = [i for i in range(target["vowel_count"]) if i != target["correct_vowel_index"]]
        alternatives.sort(key=lambda i: hashlib.sha256(f"alt:{key}:{target['word_index']}:{i}".encode()).digest())
        for alternative in alternatives:
            unmarked_text = text.replace(ACUTE, "")
            correct_word = put_stress(target["word"], target["correct_vowel_index"])
            wrong_word = put_stress(target["word"], alternative)
            result.append({
                **target,
                "wrong_vowel_index": alternative,
                "wrong_word": wrong_word,
                # Only the controlled target remains marked.  Other automatic
                # RUAccent marks are not allowed to leak into either side.
                "correct_text": replace_word(unmarked_text, target["word_index"], correct_word),
                "text": replace_word(unmarked_text, target["word_index"], wrong_word),
                "same_audio_negative": True,
            })
            if len(result) >= count:
                return result
    return result


def source_id(row: dict) -> str:
    local = str(row.get("local_spk_id") or "")
    if "__spk" in local:
        return local.split("__spk", 1)[0]
    utterance = str(row.get("utt_id") or "")
    match = re.match(r"(.+)_\d+_\d+$", utterance)
    if match:
        return match.group(1)
    raise Rejected("missing-source-id")


def hash_split(key: str, seed: int, validation_fraction: float) -> str:
    number = int.from_bytes(hashlib.sha256(f"{seed}:{key}".encode()).digest()[:8], "big")
    return "validation" if number < int(validation_fraction * (1 << 64)) else "train"


def assign_split(row: dict, seed: int, validation_fraction: float,
                 assignments: dict[str, str] | None = None) -> tuple[str, dict]:
    """Assign the accepted source/speaker component without validation bias.

    Independently hashing source and global speaker and retaining only matches
    makes the effective validation fraction ``p² / (p² + (1-p)²)``.  At p=.1
    that is about 1.2%, not 10%.  This online component map instead propagates
    an existing assignment across observed source/speaker edges.  An edge that
    would join already assigned opposite components is excluded, so no
    accepted source or speaker crosses the split.
    """
    assignments = assignments if assignments is not None else {}
    source = source_id(row)
    global_speaker = row.get("global_spk_id")
    keys = ["source:" + source]
    if global_speaker:
        keys.append("speaker:" + str(global_speaker))
    known = {assignments[key] for key in keys if key in assignments}
    if len(known) > 1:
        raise Rejected("source-speaker-split-conflict")
    if known:
        split = known.pop()
        decision = "propagated-observed-component"
    else:
        anchor = keys[1] if len(keys) > 1 else keys[0]
        split = hash_split("component:" + anchor, seed, validation_fraction)
        decision = "hashed-new-component"
    for key in keys:
        assignments.setdefault(key, split)
    basis = {
        "source_id": source,
        "global_spk_id": global_speaker,
        "local_spk_id": row.get("local_spk_id"),
        "assignment": split,
        "assignment_policy": "online-source-speaker-component-v1",
        "decision": decision,
        "component_keys": keys,
    }
    return split, basis


def audio_bytes(row: dict) -> bytes:
    audio = row.get("audio")
    if not isinstance(audio, dict):
        raise Rejected("missing-audio-bytes")
    data = audio.get("bytes")
    if data is None and audio.get("path"):
        path = Path(audio["path"])
        if path.is_file():
            data = path.read_bytes()
    if not isinstance(data, bytes) or not data:
        raise Rejected("missing-audio-bytes")
    return data


def validate_wav(data: bytes, expected_duration: float) -> dict:
    try:
        with wave.open(io.BytesIO(data), "rb") as wav:
            channels, rate, frames = wav.getnchannels(), wav.getframerate(), wav.getnframes()
            width = wav.getsampwidth()
    except (wave.Error, EOFError) as exc:
        raise Rejected("invalid-wav") from exc
    actual = frames / rate if rate else 0
    if channels != 1 or rate != 24000 or width not in (2, 3, 4):
        raise Rejected("unexpected-audio-format")
    if abs(actual - expected_duration) > max(0.25, expected_duration * 0.02):
        raise Rejected("audio-duration-mismatch")
    return {"sample_rate": rate, "channels": channels, "sample_width": width, "frames": frames, "seconds": actual}


def config_from_args(args, revision: str) -> dict:
    config = {
        "dataset_id": DATASET_ID,
        "dataset_revision": revision,
        "dataset_split": "train",
        "target_hours": args.target_hours,
        "max_seconds": args.max_seconds,
        "min_mos": args.min_mos,
        "validation_fraction": args.validation_fraction,
        "seed": args.seed,
        "max_clips_per_source": args.max_clips_per_source,
        "max_clips_per_global_speaker": args.max_clips_per_global_speaker,
        "counterfactuals_per_clip": args.counterfactuals_per_clip,
        "max_per_base_word": args.max_per_base_word,
        "max_per_stress_pattern": args.max_per_stress_pattern,
        "split_policy": "online-source-speaker-component-v1",
        "max_disk_bytes": int(args.max_disk_gb * 1024**3),
        "max_temp_bytes": int(args.max_temp_gb * 1024**3),
        "license": SOURCE_LICENSES,
    }
    if getattr(args, "scan_all", False):
        config["selection_mode"] = "scan-entire-pinned-source"
    return config


def initial_state(config: dict) -> dict:
    return {
        "version": STATE_VERSION,
        "config": config,
        "scanned_rows": 0,
        "accepted": [],
        "references": {},
        "pending": {},
        "rejections": {},
        "seconds": {"train": 0.0, "validation": 0.0},
        "source_counts": {},
        "global_speaker_counts": {},
        "target_base_counts": {},
        "stress_pattern_counts": {},
        "split_assignments": {},
        "complete": False,
        "updated_unix": time.time(),
    }


def bump(mapping: dict, key: str) -> None:
    mapping[key] = mapping.get(key, 0) + 1


def resume_stream(iterable, state: dict, *, enabled: bool):
    """Return an indexed iterator, restoring an exact HF stream checkpoint.

    Older state files have only ``scanned_rows`` and replay that prefix once.
    As soon as the next checkpoint is written, ``stream_state`` makes future
    retries resume at the next example without redownloading/reparsing every
    preceding row.
    """
    start_at = state["scanned_rows"]
    stream_state = state.get("stream_state") if enabled else None
    if stream_state is not None:
        iterable.load_state_dict(stream_state)
        return enumerate(iterable, start=start_at), True
    return enumerate(iterable), False


def capture_stream_state(iterable, state: dict, *, enabled: bool) -> None:
    if enabled:
        value = iterable.state_dict()
        # Fail before corrupting a durable checkpoint if the datasets release
        # ever returns a non-JSON state representation.
        json.dumps(value)
        state["stream_state"] = value


def ensure_stress_target(record: dict) -> None:
    """Fill the compact filter contract for early v3 resumable checkpoints."""
    metadata = record.get("metadata", {})
    if metadata.get("stress_target"):
        return
    alternatives = metadata.get("counterfactual_alternatives") or record.get("counterfactuals") or []
    if not alternatives:
        raise ValueError(f"Missing stress target evidence: {record.get('id')}")
    choice = alternatives[0]
    metadata["stress_target"] = {
        "word_index": choice["word_index"], "word": choice["word"], "base_word": choice["base_word"],
        "correct_vowel_index": choice["correct_vowel_index"], "wrong_vowel_index": choice["wrong_vowel_index"],
        "vowel_count": choice["vowel_count"],
        "stress_from_end": choice["vowel_count"] - 1 - choice["correct_vowel_index"],
        "balance_pattern": choice["balance_pattern"],
    }


def write_outputs(output: Path, state: dict) -> None:
    for split in ("train", "validation"):
        path = output / f"{split}.jsonl"
        temporary = path.with_suffix(".jsonl.tmp")
        selected = (row for row in state["accepted"] if row["split"] == split)
        temporary.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in selected), encoding="utf-8")
        os.replace(temporary, path)
    atomic_json(output / "references.json", {
        speaker: {
            "id": row["id"], "audio": row["audio"], "text": row["text"].replace(ACUTE, ""),
            "audio_sha256": row["audio_sha256"], "split": row["split"],
        }
        for speaker, row in sorted(state["references"].items())
    })
    file_hashes = {
        name: sha256_bytes((output / name).read_bytes())
        for name in ("train.jsonl", "validation.jsonl", "references.json")
    }
    for record in [*state["accepted"], *state["references"].values(), *state["pending"].values()]:
        if record.get("audio") and record.get("audio_sha256"):
            file_hashes[record["audio"]] = record["audio_sha256"]
    provenance = output / "provenance"
    if provenance.exists():
        for path in provenance.rglob("*"):
            if path.is_file():
                file_hashes[path.relative_to(output).as_posix()] = sha256_bytes(path.read_bytes())
    manifest = {
        "version": STATE_VERSION,
        "config": state["config"],
        "complete": state["complete"],
        "scanned_rows": state["scanned_rows"],
        "counts": {split: sum(r["split"] == split for r in state["accepted"]) for split in ("train", "validation")},
        "reserved_references": len(state["references"]),
        "unpaired_excluded": len(state["pending"]),
        "seconds": state["seconds"],
        "rejections": state["rejections"],
        "files": file_hashes,
    }
    if state.get("completion_reason"):
        manifest["completion_reason"] = state["completion_reason"]
    atomic_json(output / "manifest.json", manifest)


def bounded_fetch(url: str, max_bytes: int = 1024 * 1024) -> bytes:
    with urllib.request.urlopen(url, timeout=30) as response:
        data = response.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise RuntimeError(f"Provenance file exceeded {max_bytes} bytes")
    return data


def resolve_revision(requested: str | None) -> str:
    try:
        from huggingface_hub import HfApi
    except ImportError as exc:
        raise RuntimeError("Install huggingface_hub and datasets in the Higgs training environment.") from exc
    info = HfApi().dataset_info(DATASET_ID, revision=requested or "main")
    return info.sha


def stream_rows(revision: str, cache: Path, *, metadata_only: bool):
    try:
        from datasets import Audio, DownloadConfig, load_dataset
    except ImportError as exc:
        raise RuntimeError("Install the Hugging Face datasets package in the Higgs training environment.") from exc
    download = DownloadConfig(cache_dir=str(cache), resume_download=True)
    dataset = load_dataset(
        DATASET_ID,
        split="train",
        revision=revision,
        streaming=True,
        cache_dir=str(cache),
        download_config=download,
    )
    if metadata_only:
        # Disable feature decoding before removing the column.  Removing it
        # directly still invokes torchcodec in some datasets releases.
        dataset = dataset.cast_column("audio", Audio(sampling_rate=24000, decode=False))
        dataset = dataset.remove_columns(["audio"])
    else:
        dataset = dataset.cast_column("audio", Audio(sampling_rate=24000, decode=False))
    return dataset


def curate(args, rows_iter=None, revision: str | None = None) -> dict:
    output = args.output.resolve()
    protected = (Path(__file__).resolve().parents[1] / "manual-labeling").resolve()
    if output == protected or protected in output.parents:
        raise ValueError("Output may not be inside protected manual-labeling data.")
    if args.target_hours <= 0 or args.max_seconds <= 0 or not 0 < args.validation_fraction < 0.5:
        raise ValueError("Invalid target, duration, or validation fraction.")
    cache = output / ".hf-cache"
    temp = output / ".tmp"
    cache.mkdir(parents=True, exist_ok=True)
    temp.mkdir(parents=True, exist_ok=True)
    if rows_iter is None:
        # The CLI is a dedicated process.  Keep every Hub/datasets/temp cache
        # under the capped output tree instead of the user's global cache.
        os.environ["HF_HOME"] = str(cache / "home")
        os.environ["HF_HUB_CACHE"] = str(cache / "hub")
        os.environ["HF_DATASETS_CACHE"] = str(cache / "datasets")
        os.environ["TMPDIR"] = str(temp)
        os.environ["TEMP"] = str(temp)
    revision = revision or resolve_revision(args.revision)
    config = config_from_args(args, revision)
    state_path = output / "curation-state.json"
    if args.dry_run:
        state = initial_state(config)
    elif state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if state.get("version") != STATE_VERSION or state.get("config") != config:
            raise ValueError("Resume configuration differs from the pinned curation state; use another output directory.")
        for record in [*state["accepted"], *state.get("references", {}).values(), *state.get("pending", {}).values()]:
            ensure_stress_target(record)
            path = output / record["audio"]
            if not path.is_file() or path.stat().st_size != record["audio_bytes"]:
                raise ValueError(f"Accepted audio is missing or changed: {record['audio']}")
    else:
        output.mkdir(parents=True, exist_ok=True)
        state = initial_state(config)
        license_url = f"{DATASET_URL}/resolve/{revision}/LICENSE.md"
        license_data = bounded_fetch(license_url)
        provenance = output / "provenance"
        provenance.mkdir(exist_ok=True)
        (provenance / "LICENSE.YO-CPT-ru.md").write_bytes(license_data)
        atomic_json(provenance / "source.json", {
            "dataset_id": DATASET_ID,
            "dataset_url": DATASET_URL,
            "revision": revision,
            "license_sha256": sha256_bytes(license_data),
            "licenses": SOURCE_LICENSES,
        })
        atomic_json(state_path, state)

    managed_stream = rows_iter is None and not args.dry_run
    iterable = rows_iter if rows_iter is not None else stream_rows(revision, cache, metadata_only=args.dry_run)
    scan_all = bool(getattr(args, "scan_all", False))
    goals = ({"train": math.inf, "validation": math.inf} if scan_all else {
        "train": args.target_hours * 3600 * (1 - args.validation_fraction),
        "validation": args.target_hours * 3600 * args.validation_fraction,
    })
    accepted_this_run = 0
    indexed, exact_stream_resume = resume_stream(iterable, state, enabled=managed_stream)
    start_at = 0 if args.dry_run else state["scanned_rows"]
    for row_index, row in indexed:
        if not exact_stream_resume and row_index < start_at:
            continue
        if args.scan_limit is not None and state["scanned_rows"] >= args.scan_limit:
            state["completion_reason"] = "scan-limit"
            break
        state["scanned_rows"] = row_index + 1
        if row_index % 100 == 0:
            if tree_bytes(cache) + tree_bytes(temp) > config["max_temp_bytes"]:
                raise RuntimeError("Temporary/cache cap exceeded; curation stopped safely and can be resumed.")
            if tree_bytes(output) > config["max_disk_bytes"]:
                raise RuntimeError("Disk cap exceeded; curation stopped safely and can be resumed.")
        newly_accepted = []
        try:
            normalized = validate_and_normalize(row, max_seconds=args.max_seconds, min_mos=args.min_mos)
            split, split_basis = assign_split(
                row, args.seed, args.validation_fraction, state["split_assignments"]
            )
            source = split_basis["source_id"]
            if state["seconds"][split] >= goals[split]:
                raise Rejected("split-target-filled")
            if state["source_counts"].get(source, 0) >= args.max_clips_per_source:
                raise Rejected("source-cap")
            global_speaker = row.get("global_spk_id")
            if global_speaker and state["global_speaker_counts"].get(global_speaker, 0) >= args.max_clips_per_global_speaker:
                raise Rejected("global-speaker-cap")
            utterance = str(row.get("utt_id") or "")
            if not utterance:
                raise Rejected("missing-utterance-id")
            choices = make_counterfactuals(
                normalized["text"], normalized["candidates"], utterance,
                max(args.counterfactuals_per_clip, len(normalized["candidates"]) * 3),
            )
            counterfactuals = []
            for choice in choices:
                pattern = (
                    f"v{choice['vowel_count']}:from-end-"
                    f"{choice['vowel_count'] - 1 - choice['correct_vowel_index']}:ending-{choice['base_word'][-3:]}"
                )
                if state["target_base_counts"].get(choice["base_word"], 0) >= args.max_per_base_word:
                    continue
                if state["stress_pattern_counts"].get(pattern, 0) >= args.max_per_stress_pattern:
                    continue
                choice["balance_pattern"] = pattern
                counterfactuals.append(choice)
                if len(counterfactuals) >= args.counterfactuals_per_clip:
                    break
            if not counterfactuals:
                raise Rejected("no-balanced-counterfactual")
            if args.dry_run:
                wav_data = b""
                wav_info = None
                audio_hash = None
                relative_audio = None
            else:
                wav_data = audio_bytes(row)
                wav_info = validate_wav(wav_data, normalized["duration"])
                projected = tree_bytes(output) + len(wav_data) + 2 * 1024 * 1024
                if projected > config["max_disk_bytes"]:
                    raise RuntimeError("Disk cap would be exceeded before writing the next accepted clip.")
                if tree_bytes(temp) + tree_bytes(cache) > config["max_temp_bytes"]:
                    raise RuntimeError("Temporary/cache cap exceeded; curation stopped safely and can be resumed.")
                audio_hash = sha256_bytes(wav_data)
                relative_audio = f"audio/{audio_hash}.wav"
                audio_path = output / relative_audio
                audio_path.parent.mkdir(exist_ok=True)
                if audio_path.exists() and audio_path.read_bytes() != wav_data:
                    raise RuntimeError(f"Hash collision or changed audio: {relative_audio}")
                if not audio_path.exists():
                    audio_path.write_bytes(wav_data)
            record = {
                "id": utterance,
                "split": split,
                "audio": relative_audio,
                "audio_sha256": audio_hash,
                "audio_bytes": len(wav_data),
                "audio_format": wav_info,
                "text": counterfactuals[0]["correct_text"],
                "text_alignment": normalized["alignment"],
                "counterfactuals": counterfactuals,
                "counterfactual_text": counterfactuals[0]["text"],
                "metadata": {
                    "duration": normalized["duration"],
                    "seconds": normalized["duration"],
                    "mos_score": normalized["mos_score"],
                    "source_text": row.get("text"),
                    "source_text_denorm": row.get("text_denorm"),
                    "source_text_alignment": row.get("text_alignment"),
                    "local_spk_id": row.get("local_spk_id"),
                    "global_spk_id": global_speaker,
                    "split_basis": split_basis,
                    "dataset_id": DATASET_ID,
                    "dataset_revision": revision,
                    "dataset_row_index": row_index,
                    "license": SOURCE_LICENSES,
                    "annotation_status": "automatic-unreviewed-strictly-filtered",
                    "stress_evidence": "shared-ruaccent-automatic-mark-in-text-and-alignment-not-independent",
                    "training_gate": "pending-independent-acoustic-or-text-consensus-filter",
                    "acoustic_verification": {"status": "pending", "method": None},
                    "source_fully_marked_text": normalized["text"],
                    "stress_target": {
                        "word_index": counterfactuals[0]["word_index"],
                        "word": counterfactuals[0]["word"],
                        "base_word": counterfactuals[0]["base_word"],
                        "correct_vowel_index": counterfactuals[0]["correct_vowel_index"],
                        "wrong_vowel_index": counterfactuals[0]["wrong_vowel_index"],
                        "vowel_count": counterfactuals[0]["vowel_count"],
                        "stress_from_end": counterfactuals[0]["vowel_count"] - 1 - counterfactuals[0]["correct_vowel_index"],
                        "balance_pattern": counterfactuals[0]["balance_pattern"],
                    },
                    "counterfactual_alternatives": counterfactuals,
                },
            }
            local_speaker = row.get("local_spk_id")
            if not local_speaker:
                raise Rejected("missing-local-speaker")
            # A Higgs record is emitted only after a different exact clip from
            # the same local (within-video voice-clustered) speaker is known.
            # A first clip is durable but excluded from train/validation until
            # its safe reference arrives.
            existing = state["references"].get(local_speaker)
            pending = state["pending"].pop(local_speaker, None)

            def with_reference(target, reference):
                if target["id"] == reference["id"] or (
                    target["audio_sha256"] is not None and target["audio_sha256"] == reference["audio_sha256"]
                ):
                    raise Rejected("self-reference")
                target["ref_audio"] = reference["audio"]
                target["ref_text"] = reference["text"].replace(ACUTE, "")
                target["metadata"]["ref_audio_sha256"] = reference["audio_sha256"]
                target["metadata"]["reference_id"] = reference["id"]
                target["metadata"]["reference_basis"] = "different-exact-clip-same-local-speaker"
                return target

            if existing is not None:
                newly_accepted.append(with_reference(record, existing))
            elif pending is not None:
                # The first clip is a reserved reference, never a target.  This
                # avoids training and conditioning on the same performance.
                state["references"][local_speaker] = pending
                newly_accepted.append(with_reference(record, pending))
            else:
                state["pending"][local_speaker] = record
            state["accepted"].extend(newly_accepted)
            for accepted in newly_accepted:
                state["seconds"][accepted["split"]] += accepted["metadata"]["seconds"]
                for choice in accepted["metadata"]["counterfactual_alternatives"]:
                    bump(state["target_base_counts"], choice["base_word"])
                    bump(state["stress_pattern_counts"], choice["balance_pattern"])
            bump(state["source_counts"], source)
            if global_speaker:
                bump(state["global_speaker_counts"], global_speaker)
            accepted_this_run += len(newly_accepted)
        except Rejected as exc:
            bump(state["rejections"], str(exc))
        if (not args.dry_run and newly_accepted
                and len(state["accepted"]) % args.checkpoint_every == 0):
            state["updated_unix"] = time.time()
            capture_stream_state(iterable, state, enabled=managed_stream)
            atomic_json(state_path, state)
            write_outputs(output, state)
        if args.limit is not None and accepted_this_run >= args.limit:
            state["completion_reason"] = "run-limit"
            break
        if all(state["seconds"][split] >= goals[split] for split in goals):
            state["complete"] = True
            state["completion_reason"] = "target-hours-reached"
            break
    else:
        if scan_all:
            state["complete"] = True
            state["completion_reason"] = "pinned-source-exhausted"
    state["updated_unix"] = time.time()
    if not args.dry_run:
        capture_stream_state(iterable, state, enabled=managed_stream)
        atomic_json(state_path, state)
        write_outputs(output, state)
        if tree_bytes(output) > config["max_disk_bytes"]:
            raise RuntimeError("Disk cap exceeded unexpectedly; state was preserved for diagnosis.")
    return state


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    root = Path(__file__).resolve().parents[1]
    result.add_argument("--output", type=Path, default=root / "training-work/higgs-stress-pilot/yo-cpt-ru-5h")
    result.add_argument("--target-hours", type=float, default=5.0)
    result.add_argument("--max-seconds", type=float, default=12.0)
    result.add_argument("--min-mos", type=float, default=4.2)
    result.add_argument("--validation-fraction", type=float, default=0.1)
    result.add_argument("--seed", type=int, default=20261018)
    result.add_argument("--revision", help="Hugging Face commit or tag; resolved to an immutable commit.")
    result.add_argument("--max-clips-per-source", type=int, default=8)
    result.add_argument("--max-clips-per-global-speaker", type=int, default=16)
    result.add_argument("--counterfactuals-per-clip", type=int, default=1)
    result.add_argument("--max-per-base-word", type=int, default=4)
    result.add_argument("--max-per-stress-pattern", type=int, default=100)
    result.add_argument("--max-disk-gb", type=float, default=8.0)
    result.add_argument("--max-temp-gb", type=float, default=1.0)
    result.add_argument("--scan-limit", type=int, default=200_000)
    result.add_argument(
        "--scan-all", action="store_true",
        help="Consume the entire pinned source revision and freeze every qualifying pair within the caps.",
    )
    result.add_argument("--limit", type=int, help="Stop after this many newly accepted clips.")
    result.add_argument("--checkpoint-every", type=int, default=10)
    result.add_argument("--dry-run", action="store_true", help="Validate metadata only; download and write no audio.")
    return result


def main() -> int:
    args = parser().parse_args()
    state = curate(args)
    summary = {
        "complete": state["complete"],
        "scanned_rows": state["scanned_rows"],
        "accepted": len(state["accepted"]),
        "hours": {key: round(value / 3600, 4) for key, value in state["seconds"].items()},
        "top_rejections": sorted(state["rejections"].items(), key=lambda item: (-item[1], item[0]))[:12],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if state["complete"] or args.dry_run or args.limit is not None else 2


if __name__ == "__main__":
    sys.exit(main())
