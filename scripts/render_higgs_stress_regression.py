"""Render a paired production-Higgs stress regression without changing production.

Every case reuses the frozen sentence, voice reference, sampling controls and
seed from the production take.  The intended U+0301 mark and model checkpoint
are the only sound-affecting changes.  Baseline and candidate outputs are kept
in separate immutable folders for ASR, stress and speaker-similarity checks.
"""
from __future__ import annotations

import argparse
import copy
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "runtime"), str(ROOT / "scripts")]
from rimsky.util import digest, file_digest, write_json
from render_higgs_book_trial import quality, sequence_request

ACUTE = "\u0301"
WORD = re.compile(r"[А-Яа-яЁё\u0301]+(?:-[А-Яа-яЁё\u0301]+)*")


def plain(value: str) -> str:
    return value.replace(ACUTE, "")


def apply_mark(text: str, selected: str, intended: str) -> str:
    """Replace the unique matching word, preserving every unrelated mark.

    A reported proper name can be stored in base form while the frozen sentence
    contains its inflected suffix (Хангвил -> Хангвила).  In that one guarded
    case the base-form mark is projected onto the unique prefixed surface.
    """
    matches = [match for match in WORD.finditer(text)
               if plain(match.group()).casefold() == selected.casefold()]
    projected_suffix = ""
    if not matches and selected[:1].isupper():
        matches = [match for match in WORD.finditer(text)
                   if plain(match.group()).casefold().startswith(selected.casefold())]
        if len(matches) == 1:
            projected_suffix = plain(matches[0].group())[len(selected):]
    if len(matches) != 1:
        raise ValueError(f"Expected one {selected!r} in synthesis text, found {len(matches)}: {text!r}")
    match = matches[0]
    if plain(intended).casefold() != selected.casefold() or intended.count(ACUTE) != 1:
        raise ValueError("Invalid intended stress: " + intended)
    replacement = intended + projected_suffix
    if match.group()[:1].isupper() and replacement[:1].islower():
        replacement = replacement[:1].upper() + replacement[1:]
    return text[:match.start()] + replacement + text[match.end():]


def source_units(folder: Path) -> dict[str, list[dict]]:
    result: dict[str, list[dict]] = {}
    for path in sorted(folder.glob("c0*/chunk-*/**/selected.json")):
        for identifier, row in json.loads(path.read_text(encoding="utf-8")).items():
            result.setdefault(identifier, []).append(row)
    return result


def plan(suite: dict, source: dict[str, list[dict]], models: dict[str, Path], runner: Path) -> dict:
    model_hashes = {name: file_digest(path) for name, path in models.items()}
    runner_hash = file_digest(runner)
    cases = []
    for case in suite["cases"]:
        if case["status"] != "adjudicated":
            continue
        choices = source.get(case["segment_id"], [])
        exact = [row for row in choices if (row.get("receipt") or {}).get("audio_sha256") == case["audio_sha256"]]
        if exact:
            selected = exact[-1]
        elif len(choices) == 1:
            selected = choices[0]
        else:
            selected = None
        if selected is None:
            raise ValueError("No frozen production synthesis for " + case["segment_id"])
        synthesis = copy.deepcopy(selected["unit"]["synthesis"])
        if synthesis.get("effects_policy") != "disabled":
            raise ValueError("Stress regression must not enable Higgs effects")
        before = synthesis["text"]
        after = apply_mark(before, case["selected_text"], case["intended_stress"])
        if synthesis.get("prompt") != before:
            raise ValueError("Unexpected prompt prefix in regression source: " + case["segment_id"])
        synthesis["text"] = synthesis["prompt"] = after
        if synthesis["runner_sha256"] != runner_hash:
            raise ValueError("Frozen production runner changed")
        cases.append({
            "id": case["id"], "segment_id": case["segment_id"],
            "selected_text": case["selected_text"], "intended_stress": case["intended_stress"],
            "historical_audio_sha256": case["audio_sha256"],
            "source_synthesis_sha256": digest(selected["unit"]["synthesis"]),
            "synthesis": synthesis,
        })
    return {
        "schema": 1,
        "purpose": "paired_generated_audio_stress_regression",
        "production_approved": False,
        "suite_sha256": digest(suite),
        "runner_sha256": runner_hash,
        "models": {name: {"path": str(path.resolve()), "sha256": model_hashes[name]}
                   for name, path in models.items()},
        "cases": cases,
        "sound_input_contract": "same text/reference/sampling/runner; model weights only",
    }


def render_variant(name: str, model: Path, runner: Path, output: Path, frozen: dict) -> None:
    folder = output / name
    if folder.exists():
        raise ValueError("Use a fresh immutable render folder: " + str(folder))
    folder.mkdir(parents=True)
    model_hash = frozen["models"][name]["sha256"]
    units, requests = [], []
    for case in frozen["cases"]:
        synthesis = copy.deepcopy(case["synthesis"])
        synthesis["model_sha256"] = model_hash
        identifier = case["id"]
        units.append({
            "id": identifier, "text": synthesis["text"], "spoken": synthesis["text"],
            "speaker": synthesis["voice_family"], "render_voice": synthesis["voice_family"],
            "mode": "speech", "synthesis": synthesis, "key": digest(synthesis),
            "audio": identifier + ".wav", "regression_case": case["id"],
        })
        requests.append(sequence_request(identifier, synthesis))
    render_plan = {"schema": 1, "purpose": frozen["purpose"], "variant": name, "units": units}
    write_json(folder / "render-plan.json", render_plan)
    sequence = {"requests": requests}
    write_json(folder / "sequence.json", sequence)
    command = [str(runner), "--task", "tts", "--family", "higgs_audio_tts",
               "--model", str(model), "--backend", "cuda", "--device", "0", "--threads", "4",
               "--request-sequence", str(folder / "sequence.json"), "--out-dir", str(folder),
               "--out-format", "pcm16", "--metrics", "--batch-manifest-out", str(folder / "native-manifest.json")]
    with (folder / "native.log").open("w", encoding="utf-8") as log:
        subprocess.run(command, cwd=runner.parent, stdout=log, stderr=subprocess.STDOUT,
                       timeout=max(1800, len(units) * 90), check=True)
    takes = {}
    for unit in units:
        path = folder / unit["audio"]
        measured = quality(path, unit["synthesis"])
        takes[unit["id"]] = {**measured, "audio": unit["audio"], "key": unit["key"],
                              "synthesis_sha256": digest(unit["synthesis"]), "word_status": "pending"}
    write_json(folder / "receipts.json", {"plan_sha256": file_digest(folder / "render-plan.json"), "takes": takes})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--source-generation", type=Path, required=True)
    parser.add_argument("--baseline-model", type=Path, required=True)
    parser.add_argument("--candidate-model", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--runner", type=Path, default=ROOT / "training-work/higgs-tts-3-pilot/audio-cpp-cuda/audiocpp_cli.exe")
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Use a fresh immutable output directory")
    suite = json.loads(args.suite.read_text(encoding="utf-8"))
    models = {"baseline": args.baseline_model.resolve(), "candidate": args.candidate_model.resolve()}
    frozen = plan(suite, source_units(args.source_generation.resolve()), models, args.runner.resolve())
    args.output.mkdir(parents=True)
    write_json(args.output / "plan.json", frozen)
    for name, model in models.items():
        print("RENDER", name, len(frozen["cases"]), "cases", flush=True)
        render_variant(name, model, args.runner.resolve(), args.output.resolve(), frozen)
    print(json.dumps({"cases": len(frozen["cases"]), "output": str(args.output.resolve())}, indent=2))


if __name__ == "__main__":
    main()
