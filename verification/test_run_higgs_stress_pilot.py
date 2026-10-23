import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import run_higgs_stress_pilot as pilot


class StressPilotSupervisorTests(unittest.TestCase):
    def test_optimizer_steps_cover_requested_passes(self):
        self.assertEqual(250, pilot.optimizer_steps(1000, epochs=2, accumulation=8))
        self.assertEqual(3, pilot.optimizer_steps(9, epochs=2, accumulation=8))
        with self.assertRaises(ValueError):
            pilot.optimizer_steps(0)

    def test_gpu_csv_parser_accepts_nvidia_units(self):
        self.assertEqual((2956, 6141, 100), pilot.parse_gpu_csv("2956 MiB, 6141 MiB, 100 %\n"))
        with self.assertRaises(ValueError):
            pilot.parse_gpu_csv("unknown")

    def test_completed_accepts_explicit_status_or_complete_flag(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "report.json"
            path.write_text(json.dumps({"status": "smoke_passed"}), encoding="utf-8")
            self.assertTrue(pilot.completed(path, ("smoke_passed",)))
            self.assertFalse(pilot.completed(path))
            path.write_text(json.dumps({"complete": True}), encoding="utf-8")
            self.assertTrue(pilot.completed(path))


if __name__ == "__main__":
    unittest.main()
