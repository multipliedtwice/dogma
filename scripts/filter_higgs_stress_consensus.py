"""Build an immutable high-precision Higgs stress dataset from YO-CPT candidates.

RUAccent supplied the source marks, so text/alignment agreement alone is not an
independent label.  This stage additionally requires an unambiguous matching
Silero dictionary stress and a matching meter2 acoustic prediction.  Failed or
unmeasurable rows are quarantined; automatic evidence is never described as a
human verdict.

meter2 is restricted here to an internal research pilot because its published
head used Silero-generated training audio under non-commercial model terms.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import time
import unicodedata

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from export_higgs_training import read, rows, sha, write
from train_higgs_local import verify_dataset

ACUTE = "\u0301"
VOWELS = "аеёиоуыэюя"
METER_REPO = "siriusfreak/qwen3-tts-12hz-1.7b-ru-stress-cf"
METER_REVISION = "c16b3af3db3095120efc1ebb73f6522442bcb3b1"
CTC_REPO = "jonatasgrosman/wav2vec2-large-xlsr-53-russian"
CTC_REVISION = "2329100508896c6d9b157019803ab5601e6f3406"


def atomic_json(path: Path, value: object) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def plain(text: str) -> str:
    return unicodedata.normalize("NFC", text.replace(ACUTE, ""))


def stress_index(marked: str) -> int | None:
    marked = unicodedata.normalize("NFC", marked)
    vowel = -1
    found = []
    for index, char in enumerate(marked):
        if char.lower() in VOWELS:
            vowel += 1
        if char == ACUTE:
            if index == 0 or marked[index - 1].lower() not in VOWELS:
                return None
            found.append(vowel)
    return found[0] if len(found) == 1 else None


def silero_dictionary_index(word: str, accentor) -> int | None:
    """Return only an unambiguous Silero dictionary stress for one plain word."""
    word = plain(word).lower()
    if not word or "ё" in word or "-" in word:
        return None
    homograph = accentor(
        word,
        put_stress=False,
        put_stress_homo=True,
        put_yo=False,
        put_yo_homo=False,
        stress_single_vowel=False,
    )
    if "+" in homograph:
        return None
    marked = accentor(
        word,
        put_stress=True,
        put_stress_homo=True,
        put_yo=False,
        put_yo_homo=False,
        stress_single_vowel=False,
    )
    if marked.count("+") != 1:
        return None
    plus = marked.index("+")
    if plus + 1 >= len(marked) or marked[plus + 1].lower() not in VOWELS:
        return None
    return sum(char.lower() in VOWELS for char in marked[:plus])


def acoustic_decision(judgement: list[dict], target: dict, *, min_align: float,
                      min_margin: float) -> tuple[bool, str, dict | None]:
    word_index = target.get("word_index")
    correct = target.get("correct_vowel_index")
    if not isinstance(word_index, int) or not isinstance(correct, int):
        return False, "missing-target-index", None
    if word_index < 0 or word_index >= len(judgement):
        return False, "meter-word-index", None
    evidence = judgement[word_index]
    if plain(str(evidence.get("word", ""))).lower() != plain(str(target.get("base_word", ""))).lower():
        return False, "meter-word-mismatch", evidence
    if evidence.get("best") is None or evidence.get("align") is None or evidence.get("margin") is None:
        return False, "meter-unmeasured", evidence
    if float(evidence["align"]) < min_align:
        return False, "meter-low-alignment", evidence
    if int(evidence["best"]) != correct:
        return False, "meter-stress-disagreement", evidence
    if float(evidence["margin"]) < min_margin:
        return False, "meter-low-margin", evidence
    return True, "accepted", evidence


def ensure_meter2(folder: Path) -> dict:
    """Fetch exact research-tool revisions without downloading either TTS model."""
    from huggingface_hub import hf_hub_download, snapshot_download

    meter = folder / "meter2.py"
    head = folder / "head.pt"
    ctc = folder / "ctc"
    folder.mkdir(parents=True, exist_ok=True)
    if not meter.exists():
        shutil.copyfile(hf_hub_download(METER_REPO, "meter/meter2.py", revision=METER_REVISION), meter)
    if not head.exists():
        shutil.copyfile(hf_hub_download(METER_REPO, "meter/head.pt", revision=METER_REVISION), head)
    snapshot_download(
        CTC_REPO,
        revision=CTC_REVISION,
        local_dir=ctc,
        allow_patterns=("config.json", "preprocessor_config.json", "pytorch_model.bin", "vocab.json", "README.md"),
    )
    return {
        "meter_repository": METER_REPO,
        "meter_revision": METER_REVISION,
        "meter_py_sha256": sha(meter),
        "meter_head_sha256": sha(head),
        "ctc_repository": CTC_REPO,
        "ctc_revision": CTC_REVISION,
        "research_only": True,
        "reason": "meter2 head training includes Silero-rendered audio under non-commercial model terms",
    }


def load_meter2(folder: Path):
    os.environ["METER2_DEVICE"] = "cpu"
    os.environ["METER2_THREADS"] = "4"
    os.environ["METER2_HEAD"] = str((folder / "head.pt").resolve())
    os.environ["METER2_CTC"] = str((folder / "ctc").resolve())
    spec = importlib.util.spec_from_file_location("rimsky_research_meter2", folder / "meter2.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load pinned meter2 module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _install_local_ctc_loader(module, (folder / "ctc").resolve())
    return module


def _install_local_ctc_loader(module, ctc_directory: Path) -> None:
    """Keep meter2 fully pinned/offline when its CTC source is a local folder.

    Upstream meter2 loads the model and processor from ``METER2_CTC`` but asks
    ``hf_hub_download`` for vocab.json.  On Windows, an absolute local path is
    therefore misread as a Hub repository id.  Override only that loader and
    read the already-pinned local vocabulary directly; leave the downloaded
    research tool byte-for-byte unchanged for provenance.
    """
    ctc_directory = ctc_directory.resolve()

    def load_ctc_local():
        if module._Models.ctc is None:
            with module._Models.lock:
                if module._Models.ctc is None:
                    from transformers import Wav2Vec2ForCTC, Wav2Vec2FeatureExtractor

                    module._threads()
                    model = Wav2Vec2ForCTC.from_pretrained(
                        str(ctc_directory), local_files_only=True
                    ).eval().to(module.DEVICE)
                    module._Models.proc = Wav2Vec2FeatureExtractor.from_pretrained(
                        str(ctc_directory), local_files_only=True
                    )
                    module._Models.vocab = json.loads(
                        (ctc_directory / "vocab.json").read_text(encoding="utf-8")
                    )
                    module._Models.ctc = model
        return module._Models.ctc

    module.load_ctc = load_ctc_local
    module._rimsky_local_ctc_directory = str(ctc_directory)


def link_or_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if sha(destination) != sha(source):
            raise ValueError(f"Existing linked audio changed: {destination}")
        return
    try:
        os.link(source, destination)
    except OSError:
        shutil.copyfile(source, destination)


def consensus_goals(target_hours: float, validation_fraction: float, consume_all: bool) -> dict[str, float]:
    if consume_all:
        return {"train": float("inf"), "validation": float("inf")}
    return {
        "train": target_hours * 3600 * (1 - validation_fraction),
        "validation": target_hours * 3600 * validation_fraction,
    }


def consensus_complete(seconds: dict[str, float], goals: dict[str, float], consume_all: bool) -> bool:
    if consume_all:
        return seconds["train"] > 0 and seconds["validation"] > 0
    return all(seconds[split] >= goals[split] for split in goals)


def filter_dataset(args: argparse.Namespace, *, meter=None, accentor=None) -> dict:
    source = args.input.resolve()
    output = args.output.resolve()
    if output.exists():
        raise ValueError("Use a fresh output directory for an immutable consensus dataset.")
    protected = (ROOT / "manual-labeling").resolve()
    if output == protected or protected in output.parents:
        raise ValueError("Output may not be inside protected manual-labeling data.")
    input_hash = verify_dataset(source)
    tools = ensure_meter2(args.tools.resolve()) if meter is None else {"injected_for_test": True}
    meter = meter or load_meter2(args.tools.resolve())
    if accentor is None:
        from silero_stress import load_accentor
        accentor = load_accentor()

    output.mkdir(parents=True)
    consume_all = bool(getattr(args, "consume_all", False))
    goals = consensus_goals(args.target_hours, args.validation_fraction, consume_all)
    seconds = {"train": 0.0, "validation": 0.0}
    accepted = {"train": [], "validation": []}
    rejected = Counter()
    quarantine = []
    started = time.monotonic()
    for split in ("train", "validation"):
        for row in rows(source / f"{split}.jsonl"):
            if seconds[split] >= goals[split]:
                break
            metadata = row.get("metadata") or {}
            target = metadata.get("stress_target")
            if not isinstance(target, dict):
                rejected["missing-stress-target"] += 1
                continue
            if silero_dictionary_index(str(target.get("base_word", "")), accentor) != target.get("correct_vowel_index"):
                rejected["silero-disagreement-or-ambiguous"] += 1
                continue
            wav = source / row["audio"]
            judgement = meter.judge_file(str(wav), plain(row["text"]))
            good, reason, evidence = acoustic_decision(
                judgement, target, min_align=args.min_align, min_margin=args.min_margin
            )
            if not good:
                rejected[reason] += 1
                if reason == "meter-stress-disagreement" and len(quarantine) < args.quarantine_limit:
                    quarantine.append({
                        "id": row.get("id"), "split": split, "text": row.get("text"),
                        "audio": row.get("audio"), "target": target, "meter2": evidence,
                        "reason": reason,
                    })
                continue
            evidence_bound = {
                "rule": "yo-ruaccent+silero-unambiguous+meter2-v1",
                "silero_vowel_index": target["correct_vowel_index"],
                "meter2": evidence,
                "human_reviewed": False,
                "scope": "internal-research-pilot",
            }
            item = {**row, "metadata": {
                **metadata,
                "annotation_status": "automatic-consensus-filtered-not-human-reviewed",
                "stress_evidence": evidence_bound,
            }}
            accepted[split].append(item)
            seconds[split] += float(metadata["seconds"])
            print(
                f"Consensus {split}: {len(accepted[split])} clips, {seconds[split] / 3600:.3f}/{goals[split] / 3600:.3f} h",
                flush=True,
            )

    complete = consensus_complete(seconds, goals, consume_all)
    if not complete:
        raise RuntimeError(
            "Candidate corpus exhausted before the consensus target; curate a larger immutable candidate set. "
            + json.dumps({key: seconds[key] / 3600 for key in seconds})
        )
    files: dict[str, str] = {}
    wanted_audio = set()
    for split, items in accepted.items():
        path = output / f"{split}.jsonl"
        path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in items), encoding="utf-8")
        files[path.relative_to(output).as_posix()] = sha(path)
        for row in items:
            wanted_audio.update((row["audio"], row["ref_audio"]))
    for name in sorted(wanted_audio):
        destination = output / name
        link_or_copy(source / name, destination)
        files[name] = sha(destination)
    quarantine_path = output / "quarantine.jsonl"
    quarantine_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in quarantine), encoding="utf-8"
    )
    files["quarantine.jsonl"] = sha(quarantine_path)
    report = {
        "schema": 1,
        "status": "complete",
        "complete": True,
        "input_manifest_sha256": input_hash,
        "target_hours": None if consume_all else args.target_hours,
        "selection_mode": "all-consensus-passing-input" if consume_all else "target-hours",
        "hours": {key: value / 3600 for key, value in seconds.items()},
        "counts": {key: len(value) for key, value in accepted.items()},
        "rejections": dict(rejected),
        "quarantine_count": len(quarantine),
        "thresholds": {"meter2_min_alignment": args.min_align, "meter2_min_margin": args.min_margin},
        "tools": tools,
        "license_scope": "internal research pilot; not cleared for a distributable/commercial checkpoint",
        "elapsed_seconds": time.monotonic() - started,
    }
    write(output / "report.json", report)
    files["report.json"] = sha(output / "report.json")
    write(output / "manifest.json", {
        "schema": 1,
        "builder_sha256": sha(Path(__file__)),
        "input_manifest_sha256": input_hash,
        "files": files,
    })
    return report


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    base = ROOT / "training-work/higgs-stress-pilot"
    result.add_argument("--input", type=Path, required=True)
    result.add_argument("--output", type=Path, required=True)
    result.add_argument("--tools", type=Path, default=base / "tools/meter2")
    result.add_argument("--target-hours", type=float, default=5.0)
    result.add_argument(
        "--consume-all", action="store_true",
        help="Filter every record in the immutable input instead of stopping at an hour target.",
    )
    result.add_argument("--validation-fraction", type=float, default=0.1)
    result.add_argument("--min-align", type=float, default=0.65)
    result.add_argument("--min-margin", type=float, default=0.15)
    result.add_argument("--quarantine-limit", type=int, default=60)
    return result


def main() -> None:
    args = parser().parse_args()
    if args.target_hours <= 0 or not 0 < args.validation_fraction < 0.5:
        raise SystemExit("Invalid target hours or validation fraction")
    if not 0 <= args.min_align <= 1 or not 0 <= args.min_margin <= 1:
        raise SystemExit("meter2 thresholds must be in [0,1]")
    report = filter_dataset(args)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
