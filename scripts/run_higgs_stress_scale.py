"""Supervise the production-scale Higgs stress continuation through merge.

The successful five-hour candidate is the starting model. The full pinned
source is exhausted, every retained item passes the same three-way consensus,
old stress clips are excluded from continuation training, and the protected
reviewed voice corpus is replayed as ordinary CE rehearsal. The result is never
installed automatically; generated-audio and listener gates remain mandatory.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "training-work/higgs-stress-pilot"
PRODUCTION = ROOT / "training-work/higgs-production"
REVISION = "f2f92c5ecf6b0323cf7e7f9599f0e4852f6c536b"


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def line_count(path: Path) -> int:
    with path.open("r", encoding="utf-8") as stream:
        return sum(bool(line.strip()) for line in stream)


class Scale:
    def __init__(self) -> None:
        self.python = Path(sys.executable).resolve()
        self.status_path = WORK / "scale-supervisor-status.json"
        self.state = {
            "schema": 1,
            "status": "running",
            "stage": "starting",
            "started_unix": time.time(),
            "updated_unix": time.time(),
            "production_approved": False,
            "starting_model": str(PRODUCTION / "models/custom-voice-plus-stress-v1-native"),
            "policy": "new full-source consensus data plus protected reviewed rehearsal",
        }
        atomic(self.status_path, self.state)

    def update(self, stage: str, **values) -> None:
        self.state.update(stage=stage, updated_unix=time.time(), **values)
        atomic(self.status_path, self.state)
        print(f"[stress-scale] {stage}", flush=True)

    def run(self, stage: str, command: list[str], accepted: tuple[int, ...] = (0,)) -> int:
        self.update(stage, command=command)
        result = subprocess.run(command, cwd=ROOT)
        if result.returncode not in accepted:
            raise RuntimeError(f"{stage} exited with {result.returncode}")
        return result.returncode

    def curate(self, candidate: Path) -> None:
        manifest = candidate / "manifest.json"
        if manifest.is_file() and read(manifest).get("complete") is True:
            self.update("curation-reused", candidate_manifest=str(manifest))
            return
        command = [
            str(self.python), "-X", "utf8", "-u", str(ROOT / "scripts/curate_yo_cpt_stress.py"),
            "--output", str(candidate), "--target-hours", "1000", "--scan-all",
            "--revision", REVISION, "--scan-limit", "2000000", "--checkpoint-every", "20",
            "--max-disk-gb", "24", "--max-temp-gb", "2",
        ]
        for attempt in range(1, 13):
            code = self.run(f"curation-attempt-{attempt}", command, accepted=(0, 1))
            if code == 0:
                return
            delay = min(60, attempt * 5)
            self.update("curation-retry-wait", attempt=attempt, retry_delay_seconds=delay)
            time.sleep(delay)
        raise RuntimeError("Full-source curation exhausted its bounded retries.")

    def execute(self) -> None:
        candidate = WORK / "yo-cpt-ru-candidates-full-v4"
        consensus = WORK / "yo-cpt-ru-consensus-full-v3"
        dataset = WORK / "production-scale-dataset-v1"
        current_model = PRODUCTION / "models/custom-voice-plus-stress-v1-native"
        old_dataset = WORK / "yo-cpt-ru-consensus-5h-v2"
        rehearsal = PRODUCTION / "datasets/reviewed-20261012-v1"
        self.curate(candidate)

        consensus_report = consensus / "report.json"
        if not (consensus_report.is_file() and read(consensus_report).get("complete") is True):
            if consensus.exists():
                raise RuntimeError(f"Incomplete consensus output requires diagnosis: {consensus}")
            self.run("full-consensus", [
                str(self.python), "-X", "utf8", "-u",
                str(ROOT / "scripts/filter_higgs_stress_consensus.py"),
                "--input", str(candidate), "--output", str(consensus),
                "--tools", str(WORK / "tools/meter2"), "--consume-all",
            ])
        else:
            self.update("full-consensus-reused", report=str(consensus_report))

        if not (dataset / "manifest.json").is_file():
            self.run("build-continuation-dataset", [
                str(self.python), "-X", "utf8", "-u",
                str(ROOT / "scripts/build_higgs_stress_scale_dataset.py"),
                "--stress", str(consensus), "--exclude", str(old_dataset),
                "--rehearsal", str(rehearsal), "--output", str(dataset),
            ])
        training_clips = line_count(dataset / "train.jsonl")
        steps = math.ceil(training_clips / 8)
        self.update("continuation-dataset-ready", training_clips=training_clips, optimizer_steps=steps,
                    dataset_report=read(dataset / "report.json"))

        baseline = WORK / "evaluation-production-scale-baseline-v1"
        if not (baseline / "report.json").is_file():
            self.run("new-holdout-baseline", [
                str(self.python), "-X", "utf8", "-u",
                str(ROOT / "scripts/evaluate_higgs_stress_preference.py"),
                "--dataset", str(dataset), "--output", str(baseline),
                "--model", str(current_model), "--limit", "200",
            ])

        run = WORK / "runs/production-scale-continuation-v1"
        report = run / "training-report.json"
        if not (report.is_file() and read(report).get("status") == "trained_pending_validation"):
            if run.exists() and any(run.iterdir()):
                raise RuntimeError(f"Incomplete continuation run requires diagnosis: {run}")
            self.run("one-epoch-production-continuation", [
                str(self.python), "-X", "utf8", "-u", str(ROOT / "scripts/train_higgs_local.py"),
                "--dataset", str(dataset), "--model", str(current_model), "--output", str(run),
                "--steps", str(steps), "--accumulation", "8", "--seed", "20261019",
                "--counterfactual-weight", "0.1", "--counterfactual-margin", "5",
                "--counterfactual-codebook", "0",
            ])

        post_new = WORK / "evaluation-production-scale-one-epoch-v1"
        post_old = WORK / "evaluation-production-scale-old-holdout-v1"
        adapter = run / "adapter"
        for stage, source, output in (
            ("new-holdout-post", dataset, post_new),
            ("old-holdout-post", old_dataset, post_old),
        ):
            if not (output / "report.json").is_file():
                self.run(stage, [
                    str(self.python), "-X", "utf8", "-u",
                    str(ROOT / "scripts/evaluate_higgs_stress_preference.py"),
                    "--dataset", str(source), "--output", str(output),
                    "--model", str(current_model), "--adapter", str(adapter), "--limit", "200",
                ])

        before = read(baseline / "report.json")["summary"]
        after = read(post_new / "report.json")["summary"]
        old = read(post_old / "report.json")["summary"]
        gates = {
            "new_correct_not_worse": after["correct_preferred_fraction"] >= before["correct_preferred_fraction"],
            "new_margin_not_worse": after["margin_pass_fraction"] >= before["margin_pass_fraction"],
            "old_correct_preserved": old["correct_preferred_fraction"] >= 0.935,
            "old_margin_preserved": old["margin_pass_fraction"] >= 0.80,
        }
        if not all(gates.values()):
            self.update("rejected-by-mechanistic-gate", status="rejected", gates=gates,
                        new_baseline=before, new_after=after, old_after=old)
            return

        native = PRODUCTION / "models/custom-voice-plus-stress-scale-v1-native"
        gguf = PRODUCTION / "models/custom-voice-plus-stress-scale-v1-q8_0.gguf"
        if not native.exists() and not gguf.exists():
            self.run("merge-scale-candidate", [
                str(self.python), "-X", "utf8", "-u", str(ROOT / "scripts/merge_higgs_lora.py"),
                "--base", str(current_model), "--run", str(run), "--output", str(native),
                "--gguf", str(gguf),
            ])
        self.update(
            "pending-generated-audio-regression", status="pending_generated_audio_regression",
            gates=gates, new_baseline=before, new_after=after, old_after=old,
            native_candidate=str(native), gguf_candidate=str(gguf), production_approved=False,
            finished_unix=time.time(),
        )


def main() -> int:
    scale = Scale()
    try:
        scale.execute()
        return 0
    except BaseException as error:
        scale.update("failed", status="failed", error=f"{type(error).__name__}: {error}",
                     traceback=traceback.format_exc(), production_approved=False)
        raise


if __name__ == "__main__":
    raise SystemExit(main())
