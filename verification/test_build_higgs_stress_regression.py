import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.build_higgs_stress_regression import build


class StressRegressionBuilderTests(unittest.TestCase):
    def test_freezes_audio_and_quarantines_conflicting_labels(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            audio = root / "audio"
            audio.mkdir()
            data = b"RIFF-frozen-test"
            sha = hashlib.sha256(data).hexdigest()
            (audio / f"{sha}.wav").write_bytes(data)
            database = root / "library.sqlite3"
            db = sqlite3.connect(database)
            db.executescript("""
                CREATE TABLE stress_reports (
                    id TEXT, book_id TEXT, generation_id TEXT, segment_id TEXT,
                    chapter_id TEXT, source_id TEXT, selected_text TEXT,
                    text_offset INTEGER, audio_offset REAL, audio_hash TEXT,
                    passage_text TEXT, spoken_text TEXT, created TEXT, kind TEXT,
                    comment TEXT, intended_stress TEXT);
                CREATE TABLE segments (
                    generation_id TEXT, id TEXT, text TEXT, spoken TEXT,
                    speaker TEXT, delivery TEXT, duration REAL, performance TEXT);
            """)
            db.execute("INSERT INTO segments VALUES (?,?,?,?,?,?,?,?)",
                       ("g", "s", "у самой границы", "у самой границы", "narrator", "calm", 1.0, "{}"))
            rows = [
                ("r1", "b", "g", "s", "c", "p", "самой", 2, .3, sha,
                 "у самой границы", "у самой границы", "1", "stress", "", "само́й"),
                ("r2", "b", "g", "s", "c", "p", "самой", 2, .3, sha,
                 "у самой границы", "у самой границы", "2", "stress", "", "са́мой"),
            ]
            db.executemany("INSERT INTO stress_reports VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
            db.commit()
            db.close()
            result = build(database, root / "suite", [("во плоти", "во плоти́")])
            self.assertEqual(result["reported_rows"], 2)
            self.assertEqual(result["distinct_reported_cases"], 1)
            self.assertEqual(result["adjudicated_case_ids"], [])
            self.assertEqual(result["cases"][0]["status"], "pending_context_adjudication")
            self.assertEqual(result["cases"][0]["label_candidates"], ["са́мой", "само́й"])
            self.assertEqual(result["required_external_cases"][0]["status"],
                             "pending_source_context_and_audio")
            self.assertTrue((root / "suite" / "manifest.json").is_file())

    def test_context_adjudication_resolves_only_the_exact_conflict(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            audio = root / "audio"
            audio.mkdir()
            data = b"RIFF-frozen-test"
            sha = hashlib.sha256(data).hexdigest()
            (audio / f"{sha}.wav").write_bytes(data)
            database = root / "library.sqlite3"
            db = sqlite3.connect(database)
            db.executescript("""
                CREATE TABLE stress_reports (id TEXT, book_id TEXT, generation_id TEXT,
                    segment_id TEXT, chapter_id TEXT, source_id TEXT, selected_text TEXT,
                    text_offset INTEGER, audio_offset REAL, audio_hash TEXT, passage_text TEXT,
                    spoken_text TEXT, created TEXT, kind TEXT, comment TEXT, intended_stress TEXT);
                CREATE TABLE segments (generation_id TEXT, id TEXT, text TEXT, spoken TEXT,
                    speaker TEXT, delivery TEXT, duration REAL, performance TEXT);
            """)
            db.execute("INSERT INTO segments VALUES ('g','s','к самой границе','к самой границе','n','calm',1,'{}')")
            common = ("b", "g", "s", "c", "p", "самой", 2, .3, sha,
                      "к самой границе", "к самой границе", "stress", "")
            db.execute("INSERT INTO stress_reports VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       ("r1", *common[:11], "1", *common[11:], "само́й"))
            db.execute("INSERT INTO stress_reports VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       ("r2", *common[:11], "2", *common[11:], "са́мой"))
            db.commit()
            db.close()
            decision = [{"report_ids": ["r2", "r1"], "selected_text": "самой",
                         "intended_stress": "са́мой", "basis": "contextual morphology"}]
            result = build(database, root / "suite", [], decision)
            self.assertEqual(result["adjudicated_case_ids"], ["reported-001"])
            self.assertEqual(result["cases"][0]["intended_stress"], "са́мой")
            self.assertEqual(result["cases"][0]["adjudication"]["basis"], "contextual morphology")

    def test_rejects_changed_audio(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "audio").mkdir()
            claimed = "0" * 64
            (root / "audio" / f"{claimed}.wav").write_bytes(b"different")
            database = root / "library.sqlite3"
            db = sqlite3.connect(database)
            db.executescript("""
                CREATE TABLE stress_reports (id TEXT, book_id TEXT, generation_id TEXT,
                    segment_id TEXT, chapter_id TEXT, source_id TEXT, selected_text TEXT,
                    text_offset INTEGER, audio_offset REAL, audio_hash TEXT, passage_text TEXT,
                    spoken_text TEXT, created TEXT, kind TEXT, comment TEXT, intended_stress TEXT);
                CREATE TABLE segments (generation_id TEXT, id TEXT, text TEXT, spoken TEXT,
                    speaker TEXT, delivery TEXT, duration REAL, performance TEXT);
            """)
            db.execute("INSERT INTO segments VALUES ('g','s','слово','слово','n','calm',1,'{}')")
            db.execute("INSERT INTO stress_reports VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       ("r", "b", "g", "s", "c", "p", "слово", 0, 0, claimed,
                        "слово", "слово", "1", "stress", "", "сло́во"))
            db.commit()
            db.close()
            with self.assertRaisesRegex(ValueError, "missing or changed"):
                build(database, root / "suite", [])


if __name__ == "__main__":
    unittest.main()
