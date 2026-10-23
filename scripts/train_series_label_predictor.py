"""Freeze Book 1+2 label predictors for an untouched Book 3 evaluation.

This is a continual-learning checkpoint, not automatic label approval.  The
Book 2 outer chapters have already been inspected during model development, so
the resulting models are candidates for Book 3 rather than a pristine Book 2
promotion claim.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import balanced_accuracy_score, f1_score
from sklearn.model_selection import StratifiedGroupKFold
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

import train_emotion2vec_mood_adapter as base

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "training-work/series-label-predictor/books12-v1"
C_VALUES = (.0001, .0003, .001, .003, .01, .03, .1, .3, 1.)


def supported(rows, split, field, minimum_clips=10, minimum_chapters=3):
    development = [row for row in rows if base.outer_partition(row, split) == "development"]
    counts = Counter(row[field] for row in development)
    chapters = defaultdict(set)
    for row in development:
        chapters[row[field]].add((row["book"], row["chapter_id"]))
    return sorted(label for label, count in counts.items()
                  if count >= minimum_clips and len(chapters[label]) >= minimum_chapters)


def partitions(rows, split, field, labels):
    selected = [i for i, row in enumerate(rows) if row[field] in labels]
    development = [i for i in selected if base.outer_partition(rows[i], split) == "development"]
    outer = [i for i in selected if base.outer_partition(rows[i], split) == "test"]
    y = np.asarray([rows[i][field] for i in development])
    groups = np.asarray([rows[i]["book"] + ":" + rows[i]["chapter_id"]
                         for i in development])
    train_local, validation_local = next(StratifiedGroupKFold(
        5, shuffle=True, random_state=base.SEED).split(np.zeros(len(development)), y, groups))
    return ([development[i] for i in train_local],
            [development[i] for i in validation_local], outer)


def feature(rows, index, task):
    row = rows[index]
    wave = np.load(base.WAVE_CACHE / (row["audio_sha256"] + ".npy"),
                   allow_pickle=False).astype(np.float32, copy=False)
    if task == "voice_family":
        encoded = np.load(base.TIMBRE_CACHE / (row["audio_sha256"] + ".npy"),
                          allow_pickle=False).astype(np.float32, copy=False)
    else:
        frames = np.load(base.FRAME_CACHE / (row["audio_sha256"] + ".npy"),
                         allow_pickle=False).astype(np.float32, copy=False)
        encoded = frames.mean(axis=0)
    return np.concatenate([wave, encoded])


def align(model, matrix, labels):
    probability = model.predict_proba(matrix)
    aligned = np.zeros((len(matrix), len(labels)))
    for column, label in enumerate(model.classes_):
        aligned[:, labels.index(str(label))] = probability[:, column]
    return aligned


def selection_score(actual, probabilities, labels):
    predicted = np.asarray(labels)[probabilities.argmax(1)]
    return float(np.mean([
        np.mean(predicted == actual),
        f1_score(actual, predicted, average="macro", zero_division=0),
        balanced_accuracy_score(actual, predicted),
    ]))


def fit_selected(rows, indices, validation, field, labels, matrices, book2_weight):
    y = np.asarray([rows[i][field] for i in indices])
    validation_y = np.asarray([rows[i][field] for i in validation])
    weights = np.asarray([book2_weight if rows[i]["book"] == "reality-benders-2-full"
                          else 1. for i in indices])
    trials, best = [], None
    for c in C_VALUES:
        model = make_pipeline(SimpleImputer(strategy="median", add_indicator=True),
                              StandardScaler(), LogisticRegression(C=c, max_iter=1500))
        model.fit(matrices[indices], y, logisticregression__sample_weight=weights)
        probability = align(model, matrices[validation], labels)
        score = selection_score(validation_y, probability, labels)
        trial = {"c": c, "selection_score": score,
                 **base.metrics(validation_y, probability, labels)}
        trials.append(trial)
        if best is None or score > best[0]:
            best = (score, c, model)
    return best[2], trials, best[1]


def current_baseline(rows, indices, task):
    model_dir = ROOT / "training-work/character-rb1/label-predictor-3a743ea3d029"
    artifact_name = "voice_family.joblib" if task == "voice_family" else "mood.joblib"
    artifact = joblib.load(model_dir / artifact_name)
    with np.load(ROOT / "training-work/character-rb2/full/features.npz", allow_pickle=False) as data:
        identifiers = data["ids"].astype(str)
        hashes = data["audio_sha256"].astype(str)
        matrix = np.concatenate([np.asarray(data[key]) for key in artifact["features"].split("+")], axis=1)
    saved = {identifier: (audio_hash, value)
             for identifier, audio_hash, value in zip(identifiers, hashes, matrix)}
    exact = [i for i in indices if rows[i]["id"] in saved
             and saved[rows[i]["id"]][0] == rows[i]["audio_sha256"]]
    probability = artifact["model"].predict_proba(
        np.asarray([saved[rows[i]["id"]][1] for i in exact]))
    labels = list(artifact["model"].classes_.astype(str))
    return {"clips": len(exact),
            "metrics": base.metrics([rows[i]["voice" if task == "voice_family" else "mood"]
                                     for i in exact], probability, labels),
            "labels": labels}


def run_task(rows, split, task, matrices):
    field = "voice" if task == "voice_family" else "mood"
    labels = supported(rows, split, field)
    train, validation, outer = partitions(rows, split, field, labels)
    book2_outer = [i for i in outer if rows[i]["book"] == "reality-benders-2-full"]
    scopes = {
        "book1_only": ([i for i in train if rows[i]["book"] == "reality-benders-1"], 1.),
        "books12_half_book2": (train, .5),
        "books12_equal": (train, 1.),
    }
    outcomes, fitted = {}, {}
    for name, (indices, weight) in scopes.items():
        model, trials, c = fit_selected(rows, indices, validation, field, labels, matrices, weight)
        probability = align(model, matrices[book2_outer], labels)
        outcomes[name] = {
            "train_clips": len(indices), "book2_weight": weight, "selected_c": c,
            "validation_trials": trials,
            "book2_outer": base.metrics([rows[i][field] for i in book2_outer], probability, labels),
        }
        fitted[name] = model
        print(json.dumps({"task": task, "scope": name,
                          "book2_outer": outcomes[name]["book2_outer"]}), flush=True)
    # Voice corrections are error-enriched, so retain the validation-selected
    # half-weight model. The mood candidate stays equal-weight to maximize use
    # of the new labels; it remains unpromoted pending untouched Book 3.
    candidate_scope = "books12_half_book2" if task == "voice_family" else "books12_equal"
    all_indices = [i for i, row in enumerate(rows) if row[field] in labels]
    candidate_c = outcomes[candidate_scope]["selected_c"]
    candidate_weight = outcomes[candidate_scope]["book2_weight"]
    final = make_pipeline(SimpleImputer(strategy="median", add_indicator=True),
                          StandardScaler(), LogisticRegression(C=candidate_c, max_iter=1500))
    final.fit(matrices[all_indices], np.asarray([rows[i][field] for i in all_indices]),
              logisticregression__sample_weight=np.asarray([
                  candidate_weight if rows[i]["book"] == "reality-benders-2-full" else 1.
                  for i in all_indices]))
    artifact = {
        "schema": 1, "task": task, "model": final,
        "features": "wave+timbre" if task == "voice_family" else "wave+emotion2vec-frame-mean",
        "supported_labels": labels, "training_books": ["reality-benders-1", "reality-benders-2-full"],
        "candidate_scope": candidate_scope, "book2_weight": candidate_weight,
        "automatic_acceptance": False, "production_promoted": False,
        "next_pristine_gate": "reality-benders-3",
    }
    joblib.dump(artifact, OUT / (task + ".joblib"), compress=3)
    return {"labels": labels, "counts": {"train": len(train), "validation": len(validation),
                                           "book2_outer": len(book2_outer)},
            "outcomes": outcomes, "current_baseline": current_baseline(rows, book2_outer, task),
            "frozen_candidate": candidate_scope, "artifact": task + ".joblib"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify-audio", action="store_true")
    args = parser.parse_args()
    rows, split, provenance = base.load_evidence(args.verify_audio)
    for row in rows:
        for cache in (base.FRAME_CACHE, base.WAVE_CACHE, base.TIMBRE_CACHE):
            if not (cache / (row["audio_sha256"] + ".npy")).is_file():
                raise FileNotFoundError("Missing exact feature cache: " + str(cache / row["audio_sha256"]))
    OUT.mkdir(parents=True, exist_ok=True)
    indices = list(range(len(rows)))
    voice_matrix = np.asarray([feature(rows, i, "voice_family") for i in indices], dtype=np.float32)
    mood_matrix = np.asarray([feature(rows, i, "mood") for i in indices], dtype=np.float32)
    report = {
        "schema": 1, "created_at": datetime.now(timezone.utc).isoformat(),
        "purpose": "freeze Books 1+2 candidates before any Book 3 labels",
        "tasks": {
            "voice_family": run_task(rows, split, "voice_family", voice_matrix),
            "mood": run_task(rows, split, "mood", mood_matrix),
        },
        "evaluation_status": "Book 2 diagnostic; Book 3 is the next pristine promotion gate",
        "provenance": provenance, "automatic_acceptance": False,
        "production_promoted": False,
    }
    base.write(OUT / "report.json", report)
    base.write(OUT / "latest.json", {
        "schema": 1,
        "created_at": report["created_at"],
        "report_sha256": base.sha(OUT / "report.json"),
        "artifacts": {
            "voice_family.joblib": base.sha(OUT / "voice_family.joblib"),
            "mood.joblib": base.sha(OUT / "mood.joblib"),
        },
        "next_pristine_gate": "reality-benders-3",
        "production_promoted": False,
    })
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
