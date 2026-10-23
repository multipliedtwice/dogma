import argparse
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import curate_yo_cpt_stress as curate


def wav_bytes(seconds=1.0, value=0):
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(24000)
        sample = int(value).to_bytes(2, "little", signed=True)
        wav.writeframes(sample * int(seconds * 24000))
    return output.getvalue()


def row(utt="videoA_0_1", speaker=None, text="Молок+о сто+яло."):
    alignment = "молок[+]о(0.00,0.45) сто[+]яло(0.46,1.00)"
    return {
        "utt_id": utt,
        "text": "молоко стояло",
        "text_denorm": text,
        "text_alignment": alignment,
        "duration": 1.0,
        "local_spk_id": utt.rsplit("_", 2)[0] + "__spk0",
        "global_spk_id": speaker,
        "lang": "ru",
        "mos_score": 4.6,
        "audio": {"bytes": wav_bytes(value=int(utt.rsplit("_", 1)[-1]))},
    }


def args(output, **changes):
    values = dict(
        output=output, target_hours=0.0002, max_seconds=12.0, min_mos=4.2,
        validation_fraction=0.1, seed=4, revision=None, max_clips_per_source=8,
        max_clips_per_global_speaker=16, counterfactuals_per_clip=1,
        max_per_base_word=4, max_per_stress_pattern=100,
        max_disk_gb=0.01, max_temp_gb=0.01, scan_limit=100,
        limit=2, checkpoint_every=1, dry_run=False,
        scan_all=False,
    )
    values.update(changes)
    return argparse.Namespace(**values)


class StressCurationTests(unittest.TestCase):
    def test_exact_stream_state_resumes_at_next_row(self):
        class FakeStream:
            def __init__(self, values):
                self.values = values
                self.position = 0

            def __iter__(self):
                while self.position < len(self.values):
                    value = self.values[self.position]
                    self.position += 1
                    yield value

            def state_dict(self):
                return {"position": self.position}

            def load_state_dict(self, value):
                self.position = value["position"]

        first = FakeStream(["a", "b", "c", "d"])
        iterator, exact = curate.resume_stream(first, {"scanned_rows": 0}, enabled=True)
        self.assertFalse(exact)
        self.assertEqual((0, "a"), next(iterator))
        self.assertEqual((1, "b"), next(iterator))
        state = {"scanned_rows": 2}
        curate.capture_stream_state(first, state, enabled=True)

        resumed = FakeStream(["a", "b", "c", "d"])
        iterator, exact = curate.resume_stream(resumed, state, enabled=True)
        self.assertTrue(exact)
        self.assertEqual([(2, "c"), (3, "d")], list(iterator))

    def test_normalizes_and_builds_same_audio_negative(self):
        normalized = curate.validate_and_normalize(row(), max_seconds=12, min_mos=4.2)
        self.assertEqual("Молоко́ стоя́ло.", normalized["text"])
        negatives = curate.make_counterfactuals(normalized["text"], normalized["candidates"], "fixed", 1)
        self.assertTrue(negatives[0]["same_audio_negative"])
        self.assertNotEqual(normalized["text"], negatives[0]["text"])
        self.assertEqual(
            normalized["text"].replace(curate.ACUTE, ""),
            negatives[0]["text"].replace(curate.ACUTE, ""),
        )
        self.assertEqual(1, negatives[0]["correct_text"].count(curate.ACUTE))
        self.assertEqual(1, negatives[0]["text"].count(curate.ACUTE))

    def test_rejects_alignment_stress_or_words_that_do_not_match(self):
        changed = row()
        changed["text_alignment"] = "мол[+]око(0.00,0.45) сто[+]яло(0.46,1.00)"
        with self.assertRaisesRegex(curate.Rejected, "stress-mismatch"):
            curate.validate_and_normalize(changed, max_seconds=12, min_mos=4.2)
        changed = row()
        changed["text_alignment"] = "молок[+]о(0.00,0.45) проп[+]ало(0.46,1.00)"
        with self.assertRaisesRegex(curate.Rejected, "word-mismatch"):
            curate.validate_and_normalize(changed, max_seconds=12, min_mos=4.2)

    def test_source_and_speaker_component_assignments_propagate_without_bias(self):
        assignments = {}
        first = row("videoA_0_1", speaker="spk1")
        split, basis = curate.assign_split(first, 4, 0.1, assignments)
        self.assertEqual(split, basis["assignment"])
        self.assertEqual(split, assignments["source:videoA"])
        self.assertEqual(split, assignments["speaker:spk1"])

        same_speaker_new_source = row("videoB_0_1", speaker="spk1")
        propagated, propagated_basis = curate.assign_split(
            same_speaker_new_source, 4, 0.1, assignments
        )
        self.assertEqual(split, propagated)
        self.assertEqual("propagated-observed-component", propagated_basis["decision"])
        self.assertEqual(split, assignments["source:videoB"])

    def test_component_bridge_between_existing_opposite_splits_is_rejected(self):
        assignments = {"source:videoA": "train", "speaker:spk1": "validation"}
        with self.assertRaisesRegex(curate.Rejected, "split-conflict"):
            curate.assign_split(row("videoA_0_1", speaker="spk1"), 4, 0.1, assignments)

    def test_curator_writes_exact_audio_provenance_and_resumes(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "pilot"
            # Avoid network in the unit test; a pre-existing pinned state is a valid resume.
            configuration = curate.config_from_args(args(output), "commit123")
            output.mkdir()
            state = curate.initial_state(configuration)
            curate.atomic_json(output / "curation-state.json", state)
            second = row("videoA_0_2")
            result = curate.curate(args(output), rows_iter=[row(), second], revision="commit123")
            self.assertEqual(1, len(result["accepted"]))
            record = result["accepted"][0]
            self.assertEqual(second["audio"]["bytes"], (output / record["audio"]).read_bytes())
            self.assertEqual("commit123", record["metadata"]["dataset_revision"])
            self.assertEqual(curate.sha256_bytes(second["audio"]["bytes"]), record["audio_sha256"])
            self.assertIn("counterfactual_text", record)
            self.assertNotEqual(record["audio"], record["ref_audio"])
            self.assertEqual("Молоко стояло.", record["ref_text"])
            self.assertEqual("different-exact-clip-same-local-speaker", record["metadata"]["reference_basis"])
            self.assertEqual("стояло", record["metadata"]["stress_target"]["base_word"])
            self.assertEqual(1, record["metadata"]["stress_target"]["stress_from_end"])
            self.assertEqual(1, record["text"].count(curate.ACUTE))
            self.assertEqual(1, record["counterfactual_text"].count(curate.ACUTE))
            manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
            self.assertIn(record["split"], ("train", "validation"))
            self.assertEqual(1, sum(manifest["counts"].values()))
            self.assertIn("train.jsonl", manifest["files"])
            self.assertIn("validation.jsonl", manifest["files"])
            self.assertIn(record["audio"], manifest["files"])
            self.assertIn(record["ref_audio"], manifest["files"])
            resumed = curate.curate(args(output), rows_iter=[row(), second], revision="commit123")
            self.assertEqual(1, len(resumed["accepted"]))

    def test_unpaired_clip_is_excluded_from_training_manifests(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "pilot"
            configuration = curate.config_from_args(args(output, limit=None), "commit123")
            output.mkdir()
            curate.atomic_json(output / "curation-state.json", curate.initial_state(configuration))
            result = curate.curate(args(output, limit=None), rows_iter=[row()], revision="commit123")
            self.assertEqual([], result["accepted"])
            self.assertEqual(1, len(result["pending"]))
            self.assertEqual("", (output / "train.jsonl").read_text(encoding="utf-8"))
            self.assertEqual("", (output / "validation.jsonl").read_text(encoding="utf-8"))

    def test_scan_all_marks_natural_source_exhaustion_complete(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "all"
            options = args(output, target_hours=999.0, limit=None, scan_all=True)
            configuration = curate.config_from_args(options, "commit123")
            output.mkdir()
            curate.atomic_json(output / "curation-state.json", curate.initial_state(configuration))
            result = curate.curate(
                options, rows_iter=[row(), row("videoA_0_2")], revision="commit123"
            )
            self.assertTrue(result["complete"])
            self.assertEqual("pinned-source-exhausted", result["completion_reason"])
            manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual("pinned-source-exhausted", manifest["completion_reason"])

    def test_checkpoint_is_not_rewritten_for_every_rejected_row(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "pilot"
            options = args(
                output, target_hours=1.0, limit=None, checkpoint_every=1,
            )
            configuration = curate.config_from_args(options, "commit123")
            output.mkdir()
            curate.atomic_json(output / "curation-state.json", curate.initial_state(configuration))
            too_long = row("videoB_0_3")
            too_long["duration"] = 20.0
            low_mos = row("videoC_0_4")
            low_mos["mos_score"] = 1.0
            with patch.object(curate, "write_outputs") as write_outputs:
                result = curate.curate(
                    options,
                    rows_iter=[row(), row("videoA_0_2"), too_long, low_mos],
                    revision="commit123",
                )
            self.assertEqual(1, len(result["accepted"]))
            # One checkpoint for the newly accepted pair plus the final flush.
            self.assertEqual(2, write_outputs.call_count)

    def test_protected_output_and_disk_cap_are_enforced(self):
        protected = ROOT / "manual-labeling" / "should-not-write"
        with self.assertRaisesRegex(ValueError, "protected"):
            curate.curate(args(protected), rows_iter=[], revision="commit123")
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "pilot"
            constrained = args(output, max_disk_gb=0.000001)
            configuration = curate.config_from_args(constrained, "commit123")
            output.mkdir()
            curate.atomic_json(output / "curation-state.json", curate.initial_state(configuration))
            with self.assertRaisesRegex(RuntimeError, "Disk cap"):
                curate.curate(constrained, rows_iter=[row(), row("videoA_0_2")], revision="commit123")


if __name__ == "__main__":
    unittest.main()
