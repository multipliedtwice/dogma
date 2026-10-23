import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import build_higgs_stress_scale_dataset as target


def make_dataset(folder: Path, train: list[dict], validation: list[dict]) -> None:
    folder.mkdir()
    files = {}
    for split, items in (("train", train), ("validation", validation)):
        path = folder / f"{split}.jsonl"
        path.write_text("".join(json.dumps(item) + "\n" for item in items), encoding="utf-8")
        files[path.name] = target.sha(path)
    for item in [*train, *validation]:
        for field in ("audio", "ref_audio"):
            path = folder / item[field]
            path.parent.mkdir(exist_ok=True)
            if not path.exists():
                path.write_bytes(item[field].encode())
            files[item[field]] = target.sha(path)
    (folder / "manifest.json").write_text(
        json.dumps({"schema": 1, "files": files}), encoding="utf-8"
    )


def row(identifier: str, suffix: str) -> dict:
    return {
        "id": identifier,
        "text": "сло́во",
        "counterfactual_text": "словó",
        "audio": f"audio/{suffix}.wav",
        "ref_audio": f"audio/{suffix}-ref.wav",
        "ref_text": "пример",
        "metadata": {"seconds": 2.0},
    }


class StressScaleDatasetTests(unittest.TestCase):
    def test_excludes_pilot_and_adds_reviewed_rehearsal(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            stress, exclude, rehearsal, output = (
                root / "stress", root / "exclude", root / "rehearsal", root / "output"
            )
            make_dataset(stress, [row("old", "old"), row("new", "new")], [row("new-val", "val")])
            make_dataset(exclude, [row("old", "old-ex")], [])
            rehearsal_row = row("human", "human")
            rehearsal_row.pop("counterfactual_text")
            make_dataset(rehearsal, [rehearsal_row], [])
            report = target.build(stress, exclude, rehearsal, output)
            self.assertEqual(1, report["counts"]["stress_new_train"])
            self.assertEqual(1, report["counts"]["reviewed_rehearsal_train"])
            train = [json.loads(line) for line in (output / "train.jsonl").read_text().splitlines()]
            self.assertEqual({"stress-new:new", "reviewed-rehearsal:human"}, {item["id"] for item in train})
            self.assertTrue((output / "manifest.json").is_file())


if __name__ == "__main__":
    unittest.main()
