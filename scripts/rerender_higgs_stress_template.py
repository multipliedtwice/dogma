"""Render a new Higgs stress A/B from a hash-bound prior regression plan.

The frozen sentences, references, marks, seeds and synthesis controls are
reused verbatim.  Only the two model checkpoints are replaced.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "runtime"), str(ROOT / "scripts")]
from rimsky.util import file_digest, write_json
from render_higgs_stress_regression import render_variant


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--template-plan", type=Path, required=True)
    parser.add_argument("--baseline-model", type=Path, required=True)
    parser.add_argument("--candidate-model", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int, help="Render only the first N cases for a pipeline smoke test")
    parser.add_argument(
        "--runner",
        type=Path,
        default=ROOT / "training-work/higgs-tts-3-pilot/audio-cpp-cuda/audiocpp_cli.exe",
    )
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Use a fresh immutable output directory")

    template_path = args.template_plan.resolve()
    frozen = json.loads(template_path.read_text(encoding="utf-8"))
    runner = args.runner.resolve()
    if frozen["runner_sha256"] != file_digest(runner):
        raise ValueError("Frozen production runner changed")
    for case in frozen["cases"]:
        if case["synthesis"].get("effects_policy") != "disabled":
            raise ValueError("Stress regression must keep Higgs effects disabled")
        if case["synthesis"].get("runner_sha256") != frozen["runner_sha256"]:
            raise ValueError("Case runner binding changed: " + case["id"])
    if args.limit is not None:
        if args.limit <= 0:
            parser.error("--limit must be positive")
        frozen["cases"] = frozen["cases"][: args.limit]
        frozen["smoke_limit"] = args.limit

    models = {
        "baseline": args.baseline_model.resolve(),
        "candidate": args.candidate_model.resolve(),
    }
    frozen["models"] = {
        name: {"path": str(path), "sha256": file_digest(path)}
        for name, path in models.items()
    }
    frozen["template_plan_sha256"] = file_digest(template_path)
    frozen["production_approved"] = False
    frozen["sound_input_contract"] = (
        "template text/reference/sampling/seed/runner unchanged; model weights only"
    )

    args.output.mkdir(parents=True)
    write_json(args.output / "plan.json", frozen)
    for name, model in models.items():
        print("RENDER", name, len(frozen["cases"]), "cases", flush=True)
        render_variant(name, model, runner, args.output.resolve(), frozen)
    print(json.dumps({"cases": len(frozen["cases"]), "output": str(args.output.resolve())}, indent=2))


if __name__ == "__main__":
    main()
