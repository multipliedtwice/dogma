"""Supervise the bounded internal Higgs Russian-stress pilot end to end.

The supervisor resumes immutable CPU/network curation, creates a separately
hashed consensus dataset, then waits for the local GPU instead of competing with
foreground work.  It runs the teacher-forced baseline, a one-step memory smoke,
two training passes and the matching post-training evaluation.  It never
promotes the adapter into production; generated-audio regression gates remain a
separate required stage.
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
REVISION = "f2f92c5ecf6b0323cf7e7f9599f0e4852f6c536b"


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def completed(path: Path, statuses: tuple[str, ...] = ("complete",)) -> bool:
    if not path.is_file():
        return False
    value = read_json(path)
    return value.get("status") in statuses or value.get("complete") is True


def count_jsonl(path: Path) -> int:
    with path.open("r", encoding="utf-8") as source:
        return sum(bool(line.strip()) for line in source)


def optimizer_steps(training_clips: int, *, epochs: int = 2, accumulation: int = 8) -> int:
    if min(training_clips, epochs, accumulation) <= 0:
        raise ValueError("Training clips, epochs and accumulation must be positive.")
    return math.ceil(training_clips * epochs / accumulation)


def parse_gpu_csv(text: str) -> tuple[int, int, int]:
    fields = [field.strip() for field in text.strip().split(",")]
    if len(fields) != 3:
        raise ValueError(f"Unexpected nvidia-smi output: {text!r}")
    numbers = []
    for field in fields:
        token = field.split()[0].replace("%", "")
        numbers.append(int(token))
    return numbers[0], numbers[1], numbers[2]


class Supervisor:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.python = Path(sys.executable).resolve()
        self.work = args.work.resolve()
        self.status_path = self.work / "supervisor-status.json"
        self.started = time.time()
        self.state: dict = {
            "schema": 1,
            "status": "running",
            "stage": "starting",
            "started_unix": self.started,
            "updated_unix": self.started,
            "python": str(self.python),
            "source_revision": REVISION,
            "candidate_hours": args.candidate_hours,
            "consensus_hours": args.consensus_hours,
            "promotion_allowed": False,
        }
        atomic_json(self.status_path, self.state)

    def update(self, stage: str, **values) -> None:
        self.state.update(stage=stage, updated_unix=time.time(), **values)
        atomic_json(self.status_path, self.state)
        print(f"[stress-pilot] {stage}", flush=True)

    def run(self, label: str, command: list[str], *, accepted=(0,)) -> int:
        self.update(label, command=command)
        result = subprocess.run(command, cwd=ROOT)
        if result.returncode not in accepted:
            raise RuntimeError(f"{label} exited with {result.returncode}")
        return result.returncode

    def curate(self, candidate: Path) -> None:
        manifest = candidate / "manifest.json"
        if manifest.is_file() and read_json(manifest).get("complete") is True:
            self.update("curation-reused", candidate_manifest=str(manifest))
            return
        command = [
            str(self.python), "-X", "utf8", "-u", str(ROOT / "scripts/curate_yo_cpt_stress.py"),
            "--output", str(candidate), "--target-hours", str(self.args.candidate_hours),
            "--revision", REVISION, "--scan-limit", "1700000", "--checkpoint-every", "10",
        ]
        for attempt in range(1, self.args.curation_attempts + 1):
            code = self.run(f"curation-attempt-{attempt}", command, accepted=(0, 1, 2))
            if code == 0:
                return
            if code == 2:
                raise RuntimeError("Pinned source exhausted before the candidate-hour target.")
            if attempt == self.args.curation_attempts:
                raise RuntimeError("Curation exhausted its bounded transient-failure retries.")
            delay = min(60, 5 * attempt)
            self.update("curation-retry-wait", attempt=attempt, retry_delay_seconds=delay)
            time.sleep(delay)

    def consensus(self, candidate: Path, dataset: Path) -> None:
        report = dataset / "report.json"
        if completed(report):
            self.update("consensus-reused", consensus_report=str(report))
            return
        if dataset.exists():
            raise RuntimeError(f"Incomplete consensus output exists; preserve and diagnose it: {dataset}")
        self.run("consensus-filter", [
            str(self.python), "-X", "utf8", "-u", str(ROOT / "scripts/filter_higgs_stress_consensus.py"),
            "--input", str(candidate), "--output", str(dataset),
            "--tools", str(self.work / "tools/meter2"),
            "--target-hours", str(self.args.consensus_hours),
        ])

    def gpu_state(self) -> tuple[int, int, int]:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total,utilization.gpu", "--format=csv,noheader"],
            capture_output=True, text=True, check=True,
        )
        return parse_gpu_csv(result.stdout.splitlines()[0])

    def wait_for_gpu(self) -> None:
        consecutive = 0
        while consecutive < self.args.gpu_free_checks:
            used, total, utilization = self.gpu_state()
            free = used <= self.args.max_gpu_used_mb and utilization <= self.args.max_gpu_utilization
            consecutive = consecutive + 1 if free else 0
            self.update(
                "waiting-for-free-gpu" if not free else "confirming-free-gpu",
                gpu={"used_mb": used, "total_mb": total, "utilization_percent": utilization},
                free_checks=consecutive,
            )
            if consecutive < self.args.gpu_free_checks:
                time.sleep(self.args.gpu_poll_seconds)

    def evaluate(self, label: str, dataset: Path, output: Path, adapter: Path | None = None) -> None:
        report = output / "report.json"
        if completed(report):
            self.update(label + "-reused", report=str(report))
            return
        if output.exists() and any(output.iterdir()):
            raise RuntimeError(f"Incomplete evaluation output exists; preserve and diagnose it: {output}")
        command = [
            str(self.python), "-X", "utf8", "-u", str(ROOT / "scripts/evaluate_higgs_stress_preference.py"),
            "--dataset", str(dataset), "--output", str(output), "--limit", "200",
        ]
        if adapter is not None:
            command.extend(("--adapter", str(adapter)))
        self.run(label, command)

    def train(self, dataset: Path, output: Path, *, smoke: bool, steps: int | None = None) -> None:
        report = output / "training-report.json"
        expected = ("smoke_passed",) if smoke else ("trained_pending_validation",)
        if completed(report, expected):
            self.update(("smoke" if smoke else "training") + "-reused", report=str(report))
            return
        if output.exists() and any(output.iterdir()):
            raise RuntimeError(f"Incomplete training output exists; preserve and diagnose it: {output}")
        command = [
            str(self.python), "-X", "utf8", "-u", str(ROOT / "scripts/train_higgs_local.py"),
            "--dataset", str(dataset), "--output", str(output),
            "--counterfactual-weight", "0.1", "--counterfactual-margin", "5",
            "--counterfactual-codebook", "0", "--accumulation", "8",
            "--seed", "20261018",
        ]
        if smoke:
            command.append("--smoke")
        else:
            command.extend(("--steps", str(steps)))
        self.run("gpu-smoke" if smoke else "two-epoch-training", command)

    def execute(self) -> None:
        candidate = self.work / "yo-cpt-ru-candidates-8h-v2"
        dataset = self.work / "yo-cpt-ru-consensus-5h-v2"
        baseline = self.work / "evaluation-base-v2"
        smoke = self.work / "runs/stress-smoke-v2"
        trained = self.work / "runs/stress-two-epoch-v2"
        post = self.work / "evaluation-two-epoch-v2"
        self.curate(candidate)
        self.consensus(candidate, dataset)
        clips = count_jsonl(dataset / "train.jsonl")
        steps = optimizer_steps(clips)
        self.update("dataset-ready", training_clips=clips, optimizer_steps=steps)
        self.wait_for_gpu()
        self.evaluate("baseline-preference", dataset, baseline)
        self.train(dataset, smoke, smoke=True)
        self.train(dataset, trained, smoke=False, steps=steps)
        self.evaluate("post-training-preference", dataset, post, trained / "adapter")
        before = read_json(baseline / "report.json")["summary"]
        after = read_json(post / "report.json")["summary"]
        self.update(
            "preference-complete-pending-generated-audio-regression",
            status="pending_generated_audio_regression",
            baseline_summary=before,
            trained_summary=after,
            adapter=str(trained / "adapter"),
            promotion_allowed=False,
            finished_unix=time.time(),
        )


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--work", type=Path, default=ROOT / "training-work/higgs-stress-pilot")
    result.add_argument("--candidate-hours", type=float, default=8.0)
    result.add_argument("--consensus-hours", type=float, default=5.0)
    result.add_argument("--curation-attempts", type=int, default=12)
    result.add_argument("--gpu-poll-seconds", type=int, default=60)
    result.add_argument("--gpu-free-checks", type=int, default=3)
    result.add_argument("--max-gpu-used-mb", type=int, default=700)
    result.add_argument("--max-gpu-utilization", type=int, default=20)
    return result


def main() -> int:
    args = parser().parse_args()
    if min(args.candidate_hours, args.consensus_hours, args.curation_attempts,
           args.gpu_poll_seconds, args.gpu_free_checks) <= 0:
        raise SystemExit("Pilot sizes, retries and GPU wait settings must be positive.")
    supervisor = Supervisor(args)
    try:
        supervisor.execute()
        return 0
    except BaseException as error:
        supervisor.update(
            "failed",
            status="failed",
            error=f"{type(error).__name__}: {error}",
            traceback=traceback.format_exc(),
            promotion_allowed=False,
        )
        raise


if __name__ == "__main__":
    raise SystemExit(main())
