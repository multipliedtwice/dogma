"""Merge independently validated, contiguous context-analysis lanes."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from prepare_book_label_pilot import save


def merge(folder: Path, lane_names: list[str], result_name: str = "context.json") -> dict:
    results = [folder / f"context-lane-{name}.json" for name in lane_names]
    missing = [path for path in results if not path.is_file()]
    if missing:
        raise FileNotFoundError("Missing context lanes: " + ", ".join(map(str, missing)))
    expected = {json.loads(line)["id"] for line in
                (folder / "source/speech-inputs.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()}
    analyzed = set()
    characters = {}
    passages = []
    passage_ids = set()
    for name, path in zip(lane_names, results):
        document = json.loads(path.read_text(encoding="utf-8"))
        for checkpoint in (folder / f"context-lane-{name}").glob("*.json"):
            saved = json.loads(checkpoint.read_text(encoding="utf-8"))
            analyzed.update(saved.get("target_ids") or
                            [passage["id"] for passage in saved.get("raw", {}).get("passages", [])])
        for character in document["characters"]:
            current = characters.get(character["id"])
            if current is None:
                characters[character["id"]] = character
            elif current["name"] != character["name"]:
                raise ValueError("Character ID collision across context lanes")
            else:
                for field in ("aliases", "evidence"):
                    current[field] = list(dict.fromkeys(current.get(field, []) + character.get(field, [])))
        for passage in document["passages"]:
            if passage["id"] in passage_ids:
                raise ValueError("Duplicate analyzed passage across lanes: " + passage["id"])
            passage_ids.add(passage["id"])
            passages.append(passage)
    if analyzed != expected:
        raise ValueError(f"Context lanes do not cover the full source: missing={sorted(expected-analyzed)[:10]}, extra={sorted(analyzed-expected)[:10]}")
    passages.sort(key=lambda row: (row["ordinal"], row["id"]))
    payload = {"schema": 1,
               "basis": "Contiguous production Codex analysis lanes, validated separately then merged; text intentions are not recorded audio labels",
               "characters": list(characters.values()), "passages": passages,
               "coverage": {"source_segments": len(expected),
                            "analyzed_target_ids": len(analyzed), "lanes": len(results)}}
    save(folder / result_name, payload)
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--folder", type=Path, required=True)
    parser.add_argument("--lanes", nargs="+", default=["a", "b", "c"])
    parser.add_argument("--result-name", default="context.json")
    args = parser.parse_args()
    payload = merge(args.folder.resolve(), args.lanes, args.result_name)
    print(f"Merged {len(payload['passages'])} passages covering {payload['coverage']['source_segments']} source segments.")


if __name__ == "__main__":
    main()
