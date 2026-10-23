"""Wait for the scale checkpoint and run its generated-audio regression."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import traceback
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "training-work/higgs-stress-pilot"
STATUS = WORK / "scale-postprocess-status.json"
SCALE_STATUS = WORK / "scale-supervisor-status.json"
OUTPUT = WORK / "generated-audio-regression-scale-v1"


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write(value: dict) -> None:
    value["updated_unix"] = time.time()
    STATUS.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATUS.with_name(STATUS.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, STATUS)


def main() -> int:
    python = Path(sys.executable).resolve()
    asr_python = ROOT / "training-work/.venv-asr/Scripts/python.exe"
    tts_python = ROOT / "training-work/.venv-tts/Scripts/python.exe"
    for interpreter in (python, asr_python, tts_python):
        if not interpreter.is_file():
            raise FileNotFoundError("Required pinned interpreter is missing: " + str(interpreter))
    state = {"schema": 1, "status": "running", "stage": "waiting-for-scale-checkpoint",
             "started_unix": time.time(), "production_approved": False}
    write(state)

    def stage(name: str, command: list[str] | None = None) -> None:
        state.update(stage=name)
        if command is not None:
            state["command"] = command
        write(state)
        print("[stress-scale-postprocess]", name, flush=True)

    def run(name: str, command: list[str]) -> None:
        stage(name, command)
        subprocess.run(command, cwd=ROOT, check=True)

    try:
        while True:
            scale = read(SCALE_STATUS)
            status = scale.get("status")
            if status in {"failed", "rejected"}:
                state.update(status="stopped_upstream_" + status, upstream=scale)
                stage("stopped-upstream-" + status)
                return 2
            if status == "pending_generated_audio_regression":
                break
            state["upstream_stage"] = scale.get("stage")
            write(state)
            time.sleep(60)

        candidate = Path(scale["gguf_candidate"])
        baseline = ROOT / "training-work/higgs-production/models/custom-voice-plus-stress-v1-q8_0.gguf"
        if not OUTPUT.exists():
            run("render-paired-regression", [
                str(python), "-X", "utf8", "-u", str(ROOT / "scripts/rerender_higgs_stress_template.py"),
                "--template-plan", str(WORK / "generated-audio-regression-v1/plan.json"),
                "--baseline-model", str(baseline), "--candidate-model", str(candidate),
                "--output", str(OUTPUT),
            ])
        for required in ("baseline/receipts.json", "candidate/receipts.json"):
            if not (OUTPUT / required).is_file():
                raise RuntimeError("Incomplete immutable regression output: " + required)

        if not all((OUTPUT / variant / "word-checks-1.7B.json").is_file()
                   for variant in ("baseline", "candidate")):
            run("primary-asr", [
                str(asr_python), "-X", "utf8", "-u", str(ROOT / "scripts/audit_higgs_book_cuda.py"),
                "--folders", str(OUTPUT / "baseline"), str(OUTPUT / "candidate"),
            ])
        if not (OUTPUT / "stress-witness-report.json").is_file():
            run("stress-witness", [
                str(python), "-X", "utf8", "-u", str(ROOT / "scripts/evaluate_higgs_generated_stress.py"),
                "--folder", str(OUTPUT),
            ])
        if not (OUTPUT / "secondary-asr-whisper-small/report.json").is_file():
            run("secondary-asr", [
                str(asr_python), "-X", "utf8", "-u", str(ROOT / "scripts/run_higgs_stress_secondary_asr.py"),
                "--folder", str(OUTPUT),
            ])
        if not (OUTPUT / "voice-preservation-report.json").is_file():
            run("voice-preservation", [
                str(tts_python), "-X", "utf8", "-u", str(ROOT / "scripts/evaluate_higgs_voice_preservation.py"),
                "--folder", str(OUTPUT),
            ])
        run("automatic-gate", [
            str(python), "-X", "utf8", "-u", str(ROOT / "scripts/evaluate_higgs_stress_ab_gate.py"),
            "--folder", str(OUTPUT),
        ])
        report = read(OUTPUT / "automatic-gate-report.json")
        state.update(status="complete", stage="complete", report=report["summary"],
                     production_approved=False, finished_unix=time.time())
        write(state)
        return 0
    except BaseException as error:
        state.update(status="failed", stage="failed", error=f"{type(error).__name__}: {error}",
                     traceback=traceback.format_exc(), production_approved=False)
        write(state)
        raise


if __name__ == "__main__":
    raise SystemExit(main())
