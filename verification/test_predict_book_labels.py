import json
from pathlib import Path
import tempfile
import unittest

import joblib
import numpy as np
from sklearn.linear_model import LogisticRegression

import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from predict_book_labels import predict


class SeriesPredictionTests(unittest.TestCase):
    def fixture(self, feature_kind="frame-mean"):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        ids = np.asarray(["a", "b", "c", "d"])
        hashes = np.asarray([character * 64 for character in "abcd"])
        wave = np.asarray([[0.], [1.], [2.], [3.]], dtype=np.float32)
        timbre = np.asarray([[0., 1.], [1., 0.], [2., 1.], [3., 0.]], dtype=np.float32)
        emotion = np.asarray([[0., 2.], [1., 1.], [2., 0.], [3., 1.]], dtype=np.float32)
        np.savez(root / "features.npz", ids=ids, audio_sha256=hashes, wave=wave,
                 timbre=timbre, emotion=emotion,
                 emotion_feature_kind=np.asarray(feature_kind))
        manifest = root / "manifest.jsonl"
        manifest.write_text("".join(json.dumps({"id": identifier,
                                                "exact_audio_sha256": audio_hash}) + "\n"
                                    for identifier, audio_hash in zip(ids, hashes)), encoding="utf-8")
        labels = np.asarray(["x", "x", "y", "y"])
        for task, values, feature_names in (
                ("voice_family", np.concatenate([wave, timbre], axis=1), "wave+timbre"),
                ("mood", np.concatenate([wave, emotion], axis=1), "wave+emotion2vec-frame-mean")):
            model = LogisticRegression().fit(values, labels)
            joblib.dump({"schema": 1, "task": task, "model": model,
                         "features": feature_names, "training_books": ["b1", "b2"],
                         "production_promoted": False,
                         "next_pristine_gate": "reality-benders-3"}, root / f"{task}.joblib")
        return temporary, root, manifest

    def test_series_artifacts_predict_only_review_proposals(self):
        temporary, root, manifest = self.fixture()
        with temporary:
            result = predict(root, root / "features.npz", manifest)
            self.assertEqual(len(result), 4)
            self.assertTrue(result[0]["human_review_required"])
            self.assertFalse(result[0]["automatic_acceptance"])
            self.assertTrue(result[0]["tasks"]["mood"]["candidate_only"])
            self.assertEqual(result[0]["tasks"]["mood"]["next_pristine_gate"],
                             "reality-benders-3")

    def test_series_mood_rejects_utterance_embeddings(self):
        temporary, root, manifest = self.fixture("utterance")
        with temporary, self.assertRaisesRegex(ValueError, "frame-mean"):
            predict(root, root / "features.npz", manifest)


if __name__ == "__main__":
    unittest.main()
