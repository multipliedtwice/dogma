"""Build and evaluate the 40-hour Higgs Russian-stress continuation.

The current production checkpoint has already seen the strict full-source
YO-CPT subset.  This run widens only acoustic-quality/diversity sampling, keeps
the 12-second training limit and the independent lexical/acoustic consensus,
and removes every old stress clip before continuation training.  Forty hours
means unique external *training* exposure after the continuation, not forty
hours of duplicated rehearsal or a forty-hour listening task.

The script is deliberately resumable between immutable stages and never
installs the resulting model.  Generated-audio regression remains a required
final gate and human listening remains required before production promotion.
"""
from __future__ import annotations

import argparse
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


def complete_report(path: Path, status: str = "complete") -> bool:
    if not path.is_file():
        return False
    value = read(path)
    return value.get("complete") is True or value.get("status") == status


class Expansion:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.python = Path(sys.executable).resolve()
        self.status_path = WORK / "stress-40h-status.json"
        self.state = {
            "schema": 1,
            "status": "running",
            "stage": "starting",
            "started_unix": time.time(),
            "updated_unix": time.time(),
            "source_revision": REVISION,
            "production_approved": False,
            "starting_model": str(PRODUCTION / "models/custom-voice-plus-stress-scale-v1-native"),
            "target": {
                "candidate_hours": args.candidate_hours,
                "consensus_hours": args.consensus_hours,
                "expected_unique_external_train_hours_after_exclusion": 40.0,
                "max_seconds": 12.0,
                "min_mos": args.min_mos,
            },
            "policy": (
                "exclude all stress audio already seen by the current model; replay protected reviewed "
                "performances; preserve 12-second and three-way consensus correctness gates"
            ),
        }
        atomic(self.status_path, self.state)

    def update(self, stage: str, **values) -> None:
        self.state.update(stage=stage, updated_unix=time.time(), **values)
        atomic(self.status_path, self.state)
        print("[stress-40h]", stage, flush=True)

    def run(self, stage: str, command: list[str], accepted: tuple[int, ...] = (0,)) -> int:
        self.update(stage, command=command)
        result = subprocess.run(command, cwd=ROOT)
        if result.returncode not in accepted:
            raise RuntimeError(f"{stage} exited with {result.returncode}")
        return result.returncode

    def curate(self, output: Path) -> None:
        manifest = output / "manifest.json"
        if manifest.is_file() and read(manifest).get("complete") is True:
            self.update("curation-reused", candidate_manifest=str(manifest))
            return
        command = [
            str(self.python), "-X", "utf8", "-u", str(ROOT / "scripts/curate_yo_cpt_stress.py"),
            "--output", str(output), "--target-hours", str(self.args.candidate_hours),
            "--revision", REVISION, "--scan-limit", "2000000", "--checkpoint-every", "20",
            "--max-seconds", "12", "--min-mos", str(self.args.min_mos),
            "--max-clips-per-source", "16", "--max-clips-per-global-speaker", "32",
            "--max-per-base-word", "8", "--max-per-stress-pattern", "200",
            "--max-disk-gb", "32", "--max-temp-gb", "2",
        ]
        for attempt in range(1, 13):
            code = self.run(f"curation-attempt-{attempt}", command, accepted=(0, 1, 2))
            if code == 0:
                return
            if code == 2:
                raise RuntimeError("Pinned YO-CPT source was exhausted before the 54-hour candidate target.")
            delay = min(60, attempt * 5)
            self.update("curation-retry-wait", attempt=attempt, retry_delay_seconds=delay)
            time.sleep(delay)
        raise RuntimeError("Curation exhausted its bounded network retries.")

    def wait_for_gpu(self) -> None:
        consecutive = 0
        while consecutive < 3:
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.used,memory.total,utilization.gpu", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, check=True,
            )
            used, total, utilization = [int(value.strip()) for value in result.stdout.splitlines()[0].split(",")]
            free = used <= 700 and utilization <= 20
            consecutive = consecutive + 1 if free else 0
            self.update(
                "confirming-free-gpu" if free else "waiting-for-free-gpu",
                gpu={"used_mb": used, "total_mb": total, "utilization_percent": utilization},
                free_checks=consecutive,
            )
            if consecutive < 3:
                time.sleep(60)

    def evaluate(self, stage: str, dataset: Path, output: Path, model: Path, adapter: Path | None = None) -> dict:
        report = output / "report.json"
        if complete_report(report):
            self.update(stage + "-reused", report=str(report))
            return read(report)["summary"]
        if output.exists() and any(output.iterdir()):
            raise RuntimeError(f"Incomplete immutable evaluation needs diagnosis: {output}")
        command = [
            str(self.python), "-X", "utf8", "-u", str(ROOT / "scripts/evaluate_higgs_stress_preference.py"),
            "--dataset", str(dataset), "--output", str(output), "--model", str(model), "--limit", "200",
        ]
        if adapter is not None:
            command.extend(("--adapter", str(adapter)))
        self.run(stage, command)
        return read(report)["summary"]

    def execute(self) -> None:
        candidate = WORK / "yo-cpt-ru-candidates-54h-v1"
        consensus = WORK / "yo-cpt-ru-consensus-45h-v1"
        previous = WORK / "yo-cpt-ru-consensus-full-v3"
        rehearsal = PRODUCTION / "datasets/reviewed-20261012-v1"
        dataset = WORK / "production-stress-40h-dataset-v1"
        current_native = PRODUCTION / "models/custom-voice-plus-stress-scale-v1-native"
        current_gguf = PRODUCTION / "models/custom-voice-plus-stress-scale-v1-q8_0.gguf"

        self.curate(candidate)
        consensus_report = consensus / "report.json"
        if not complete_report(consensus_report):
            if consensus.exists():
                raise RuntimeError(f"Incomplete immutable consensus output needs diagnosis: {consensus}")
            self.run("consensus-45h", [
                str(self.python), "-X", "utf8", "-u", str(ROOT / "scripts/filter_higgs_stress_consensus.py"),
                "--input", str(candidate), "--output", str(consensus),
                "--tools", str(WORK / "tools/meter2"), "--target-hours", str(self.args.consensus_hours),
            ])
        else:
            self.update("consensus-reused", report=str(consensus_report))

        if not (dataset / "manifest.json").is_file():
            self.run("build-unique-continuation-dataset", [
                str(self.python), "-X", "utf8", "-u", str(ROOT / "scripts/build_higgs_stress_scale_dataset.py"),
                "--stress", str(consensus), "--exclude", str(previous),
                "--rehearsal", str(rehearsal), "--output", str(dataset),
            ])
        dataset_report = read(dataset / "report.json")
        previous_report = read(previous / "report.json")
        unique_external_train_hours = (
            float(previous_report["hours"]["train"]) + float(dataset_report["hours"]["stress_new_train"])
        )
        if unique_external_train_hours < 39.5:
            raise RuntimeError(
                f"Unique external training exposure is only {unique_external_train_hours:.3f} h; "
                "do not call this the 40-hour run."
            )
        training_clips = line_count(dataset / "train.jsonl")
        steps = math.ceil(training_clips / 8)
        self.update(
            "continuation-dataset-ready", dataset_report=dataset_report,
            unique_external_train_hours=unique_external_train_hours,
            training_clips=training_clips, optimizer_steps=steps,
        )

        self.wait_for_gpu()
        baseline_new = self.evaluate("new-holdout-baseline", dataset, WORK / "evaluation-stress-40h-baseline-v1", current_native)
        baseline_old = self.evaluate("old-holdout-baseline", previous, WORK / "evaluation-stress-40h-old-baseline-v1", current_native)

        smoke = WORK / "runs/stress-40h-smoke-v1"
        if not complete_report(smoke / "training-report.json", "smoke_passed"):
            if smoke.exists() and any(smoke.iterdir()):
                raise RuntimeError(f"Incomplete GPU smoke needs diagnosis: {smoke}")
            self.run("gpu-smoke", [
                str(self.python), "-X", "utf8", "-u", str(ROOT / "scripts/train_higgs_local.py"),
                "--dataset", str(dataset), "--model", str(current_native), "--output", str(smoke),
                "--smoke", "--counterfactual-weight", "0.1", "--counterfactual-margin", "5",
                "--counterfactual-codebook", "0", "--seed", "20261022",
            ])

        run = WORK / "runs/production-stress-40h-v1"
        if not complete_report(run / "training-report.json", "trained_pending_validation"):
            if run.exists() and any(run.iterdir()):
                raise RuntimeError(f"Incomplete long training run needs diagnosis: {run}")
            self.run("one-epoch-unique-continuation", [
                str(self.python), "-X", "utf8", "-u", str(ROOT / "scripts/train_higgs_local.py"),
                "--dataset", str(dataset), "--model", str(current_native), "--output", str(run),
                "--steps", str(steps), "--accumulation", "8", "--seed", "20261022",
                "--counterfactual-weight", "0.1", "--counterfactual-margin", "5",
                "--counterfactual-codebook", "0",
            ])

        adapter = run / "adapter"
        post_new = self.evaluate(
            "new-holdout-post", dataset, WORK / "evaluation-stress-40h-post-v1", current_native, adapter
        )
        post_old = self.evaluate(
            "old-holdout-post", previous, WORK / "evaluation-stress-40h-old-post-v1", current_native, adapter
        )
        gates = {
            "new_correct_not_worse": post_new["correct_preferred_fraction"] >= baseline_new["correct_preferred_fraction"],
            "new_margin_not_worse": post_new["margin_pass_fraction"] >= baseline_new["margin_pass_fraction"],
            "old_correct_within_one_point": post_old["correct_preferred_fraction"] >= baseline_old["correct_preferred_fraction"] - 0.01,
            "old_margin_within_two_points": post_old["margin_pass_fraction"] >= baseline_old["margin_pass_fraction"] - 0.02,
        }
        if not all(gates.values()):
            self.update(
                "rejected-by-mechanistic-gate", status="rejected", gates=gates,
                new_baseline=baseline_new, new_after=post_new,
                old_baseline=baseline_old, old_after=post_old,
            )
            return

        native = PRODUCTION / "models/custom-voice-plus-stress-40h-v1-native"
        gguf = PRODUCTION / "models/custom-voice-plus-stress-40h-v1-q8_0.gguf"
        if not native.exists() and not gguf.exists():
            self.run("merge-40h-candidate", [
                str(self.python), "-X", "utf8", "-u", str(ROOT / "scripts/merge_higgs_lora.py"),
                "--base", str(current_native), "--run", str(run), "--output", str(native), "--gguf", str(gguf),
            ])

        generated = WORK / "generated-audio-regression-40h-v1"
        if not generated.exists():
            self.run("render-paired-regression", [
                str(self.python), "-X", "utf8", "-u", str(ROOT / "scripts/rerender_higgs_stress_template.py"),
                "--template-plan", str(WORK / "generated-audio-regression-v1/plan.json"),
                "--baseline-model", str(current_gguf), "--candidate-model", str(gguf), "--output", str(generated),
            ])
        asr_python = ROOT / "training-work/.venv-asr/Scripts/python.exe"
        tts_python = ROOT / "training-work/.venv-tts/Scripts/python.exe"
        if not all((generated / variant / "word-checks-1.7B.json").is_file() for variant in ("baseline", "candidate")):
            self.run("primary-asr", [
                str(asr_python), "-X", "utf8", "-u", str(ROOT / "scripts/audit_higgs_book_cuda.py"),
                "--folders", str(generated / "baseline"), str(generated / "candidate"),
            ])
        if not (generated / "stress-witness-report.json").is_file():
            self.run("stress-witness", [
                str(self.python), "-X", "utf8", "-u", str(ROOT / "scripts/evaluate_higgs_generated_stress.py"),
                "--folder", str(generated),
            ])
        if not (generated / "secondary-asr-whisper-small/report.json").is_file():
            self.run("secondary-asr", [
                str(asr_python), "-X", "utf8", "-u", str(ROOT / "scripts/run_higgs_stress_secondary_asr.py"),
                "--folder", str(generated),
            ])
        if not (generated / "voice-preservation-report.json").is_file():
            self.run("voice-preservation", [
                str(tts_python), "-X", "utf8", "-u", str(ROOT / "scripts/evaluate_higgs_voice_preservation.py"),
                "--folder", str(generated),
            ])
        self.run("automatic-audio-gate", [
            str(self.python), "-X", "utf8", "-u", str(ROOT / "scripts/evaluate_higgs_stress_ab_gate.py"),
            "--folder", str(generated),
        ])
        audio_report = read(generated / "automatic-gate-report.json")
        self.update(
            "complete-pending-human-listening", status="complete_pending_human_listening",
            unique_external_train_hours=unique_external_train_hours,
            gates=gates, generated_audio=audio_report["summary"],
            native_candidate=str(native), gguf_candidate=str(gguf),
            research_only=True,
            research_only_reason=(
                "The acoustic consensus meter contains a head trained with non-commercial Silero-rendered audio."
            ),
            production_approved=False, finished_unix=time.time(),
        )


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--candidate-hours", type=float, default=54.0)
    result.add_argument("--consensus-hours", type=float, default=45.0)
    result.add_argument("--min-mos", type=float, default=4.0)
    return result


def main() -> int:
    args = parser().parse_args()
    if args.candidate_hours <= args.consensus_hours or not 3.0 <= args.min_mos <= 5.0:
        raise SystemExit("Candidate hours must exceed consensus hours and MOS must be between 3 and 5.")
    expansion = Expansion(args)
    try:
        expansion.execute()
        return 0
    except BaseException as error:
        expansion.update(
            "failed", status="failed", error=f"{type(error).__name__}: {error}",
            traceback=traceback.format_exc(), production_approved=False,
        )
        raise


if __name__ == "__main__":
    raise SystemExit(main())
