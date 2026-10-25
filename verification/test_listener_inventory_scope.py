"""Isolated generation inventory contracts; no real library, providers or native."""
from pathlib import Path
import tempfile
import unittest

import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'runtime')]
import repair_debug_listener_stress as m


class InventoryScope(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
    def put(self,name,value='original'):
        path=self.root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(value);return path
    def test_live_supervisor_changes_and_new_audits_do_not_invalidate_sound(self):
        self.put('native/session/requests.json');self.put('assets/audio.wav')
        control=self.put('supervision/state.json');before=m.inventory(self.root)
        control.write_text('new heartbeat');self.put('supervision/new-audit/result.json')
        self.assertEqual(m.inventory(self.root),before)
    def test_historical_inventory_is_projected_without_mutation(self):
        self.put('ready.json');self.put('supervision/terminal.json')
        old={p.relative_to(self.root).as_posix():m.file_digest(p)for p in self.root.rglob('*')if p.is_file()}
        retained=dict(old);self.put('supervision/terminal.json','new supervisor outcome')
        self.assertEqual(m.inventory(self.root),m.immutable_inventory(old));self.assertEqual(old,retained)
    def test_source_sound_and_physical_evidence_mutations_are_detected(self):
        for name in ('source/book.json','ready.json','manifest.json','prepared-inputs.json','source-coverage.json',
                     'c0001/chunk-000/render-plan.json','effective-listener-units.json','units/u.json',
                     'assets/a.wav','native/session/requests.json','native/session/physical-start.json',
                     'native/session/physical-result.json','native/session/worker.json','native/session/native.log'):
            with self.subTest(name=name):
                path=self.put(name);before=m.inventory(self.root);path.write_text('changed')
                self.assertNotEqual(m.inventory(self.root),before)
    def test_only_exact_root_namespaces_are_live(self):
        for name in ('supervision-audio/file.wav','native/supervision/requests.json','plans/supervision.json'):
            self.put(name)
        self.assertEqual(len(m.inventory(self.root)),3)
    def test_added_and_removed_protected_files_are_detected(self):
        path=self.put('native/session/physical-result.json');old=m.inventory(self.root)
        path.unlink();self.assertNotEqual(m.inventory(self.root),old)
        self.put('native/another/requests.json');self.assertNotEqual(m.inventory(self.root),old)
    def test_queue_remains_live_but_immutable_queue_evidence_is_bound(self):
        self.put('listener-stress-queue/q.json');path=self.put('queue-evidence/q.json');before=m.inventory(self.root)
        self.put('listener-stress-queue/q.json','updated');self.assertEqual(m.inventory(self.root),before)
        path.write_text('altered origin');self.assertNotEqual(m.inventory(self.root),before)
    def test_invalid_recorded_paths_and_hashes_fail_closed(self):
        for name,sha in (('../supervision/a','0'*64),('/supervision/a','0'*64),('a\\b','0'*64),('a//b','0'*64),('a','bad')):
            with self.subTest(name=name),self.assertRaises(ValueError):m.immutable_inventory({name:sha})


if __name__=='__main__':unittest.main()
