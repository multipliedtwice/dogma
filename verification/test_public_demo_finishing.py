"""Mocked stage sequencing and synthetic completion evidence; no GPU or CLI."""
import importlib.util
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('finisher', ROOT / 'scripts/finish_public_audiobook_demos.py')
finisher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(finisher)


class FakeEngine:
    def __init__(self, folder, succeeds_after=1):
        self.args = SimpleNamespace(preparation_deadline_seconds=1, poll_seconds=.001)
        self.calls, self.attempts, self.published = [], 0, False
        self.succeeds_after, self.fields = succeeds_after, {}
        assignment = {'units': [{'id': 'u1'}, {'id': 'u2'}]}
        manifest = {'opening': {'fixture': True}, 'music': {'bed': {'fixture': True}, 'ending': {'fixture': True}}}
        finisher.supervision.write(folder / 'assignment.json', assignment)
        finisher.supervision.write(folder / 'manifest.json', manifest)
        for n in range(3):
            finisher.supervision.write(folder / 'takes/u1' / ('take-%02d.json' % n), {'fixture': True})

    def book(self, folder, **fields):
        self.fields.update(fields)

    def command(self, script, folder, extra=(), asr=False):
        return [str(script), '--folder', str(folder), *extra]

    def base(self, folder):
        self.calls.append(('base', []))

    def run(self, name, folder, command, kind='normal', **kwargs):
        self.calls.append((name, command))
        if name.startswith('recovery-render-'):
            number = int(finisher.supervision.flag_value(command, '--attempt'))
            self.attempts += 1
            finisher.supervision.write(folder / 'recovery/u1/takes' / ('take-%02d.json' % (number+2)), {'fixture': True})

    def resolve(self, folder, complete=False):
        self.calls.append(('complete-check' if complete else 'resolve', []))
        done = self.attempts >= self.succeeds_after
        selected = {'take': 3, 'audio_sha256': 'a'*64}
        report = {'units': [{'id': 'u1', 'selected': selected if done else None},
                            {'id': 'u2', 'selected': selected}], 'verified_units': 2 if done else 1,
                  'open_recognition_flags': 0 if done else 1, 'pending_units': 0}
        return {'report': report, 'report_sha256': 'fixture', 'publication_eligible': done,
                'completion_valid': complete and done and self.published, 'completion_reason': 'Held fixture'}

    def publish_if_eligible(self, folder, snapshot):
        if snapshot['publication_eligible']:
            self.calls.append(('publish', []))
            self.published = True


class FinishingGates(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)

    def test_all_original_turbo_precedes_campaign_and_fresh_take_gets_full_checks(self):
        engine = FakeEngine(self.folder)
        self.assertTrue(finisher.finish_book(engine, self.folder))
        names = [c[0] for c in engine.calls]
        self.assertLess(names.index('turbo-original-all-takes'), names.index('recovery-plan-u1'))
        self.assertIn('--all-takes', next(c[1] for c in engine.calls if c[0] == 'turbo-original-all-takes'))
        self.assertLess(names.index('recovery-propose-u1'), next(i for i, n in enumerate(names) if n.startswith('recovery-render-1-')))
        self.assertLess(names.index('recovery-verify-0'), names.index('recovery-witness-0'))
        self.assertLess(names.index('recovery-witness-0'), names.index('turbo-recovery-0'))
        self.assertEqual(engine.attempts, 1)
        self.assertEqual(engine.fields['status'], 'complete')

    def test_three_failed_recoveries_remain_held_without_fourth_strategy(self):
        engine = FakeEngine(self.folder, succeeds_after=99)
        self.assertFalse(finisher.finish_book(engine, self.folder))
        self.assertEqual(engine.attempts, 3)
        self.assertEqual(engine.fields['status'], 'held')
        self.assertEqual(engine.fields['holds'][0]['reason'], 'three_explicit_recovery_takes_exhausted')
        self.assertNotIn('publish', [c[0] for c in engine.calls])

    def test_resolved_units_never_get_a_recovery_campaign(self):
        engine = FakeEngine(self.folder, succeeds_after=0)
        self.assertTrue(finisher.finish_book(engine, self.folder))
        self.assertFalse(any(name.startswith('recovery-') for name, _ in engine.calls))

    def test_native_failure_is_unit_isolated_and_existing_attempts_are_not_reset(self):
        class IsolatedEngine(FakeEngine):
            def __init__(self,folder):
                super().__init__(folder)
                self.recovered = set()

            def run(self,name,folder,command,kind='normal',**kwargs):
                self.calls.append((name,command))
                if not name.startswith('recovery-render-'):
                    return
                # A render invocation must never consume another unit's slot.
                if command.count('--unit') != 1:
                    raise AssertionError('Grouped native recovery request')
                uid = finisher.supervision.flag_value(command,'--unit')
                attempt = int(finisher.supervision.flag_value(command,'--attempt'))
                finisher.supervision.write(folder/'recovery'/uid/'takes'/('take-%02d.json' % (attempt+2)),
                                           {'fixture':True,'quality_passed':uid != 'u1'})
                if uid == 'u1':
                    raise finisher.StageFailure('Synthetic first native request exceeded its unchanged token cap')
                self.recovered.add(uid)

            def resolve(self,folder,complete=False):
                self.calls.append(('complete-check' if complete else 'resolve',[]))
                report = {'units':[{'id':uid,'selected':{'audio_sha256':'a'*64} if uid in self.recovered else None}
                                   for uid in ('u1','u2')], 'verified_units':len(self.recovered),
                          'open_recognition_flags':2-len(self.recovered),'pending_units':0}
                return {'report':report,'report_sha256':'fixture','publication_eligible':False,
                        'completion_valid':False,'completion_reason':'First unit remains held'}

        engine = IsolatedEngine(self.folder)
        for n in range(3):
            finisher.supervision.write(self.folder/'takes/u2'/('take-%02d.json' % n),{'parent':n})
        for uid in ('u1','u2'):
            finisher.supervision.write(self.folder/'recovery'/uid/'takes/take-03.json',{'old_failed_evidence':uid})
        protected = {p:p.read_bytes() for p in self.folder.rglob('*.json')}
        self.assertFalse(finisher.finish_book(engine,self.folder))
        render = [(name,command) for name,command in engine.calls if name.startswith('recovery-render-')]
        self.assertEqual([name for name,_ in render],['recovery-render-2-u1','recovery-render-2-u2'])
        self.assertEqual([finisher.supervision.flag_value(command,'--attempt') for _,command in render],['2','2'])
        self.assertEqual(engine.recovered,{'u2'})
        names = [name for name,_ in engine.calls]
        self.assertLess(names.index('recovery-render-2-u2'),names.index('recovery-verify-0'))
        self.assertIn('recovery-witness-0',names);self.assertIn('turbo-recovery-0',names)
        self.assertEqual([row['id'] for row in engine.fields['campaign_holds']],['u1'])
        self.assertEqual(engine.fields['campaign_holds'][0]['attempt'],2)
        self.assertEqual(engine.fields['status'],'held')
        self.assertEqual({p:p.read_bytes() for p in protected},protected)

    def test_unknown_adopted_exit_resumes_pending_base_once_then_holds(self):
        finisher.supervision.write(self.folder / 'assignment.json', {'units': [{'id': 'u1'}]})
        finisher.supervision.write(self.folder / 'state.json', {'units': [{'id': 'u1', 'status': 'pending'}]})
        engine = object.__new__(finisher.Engine)
        engine.args = SimpleNamespace(adopt_pid=None, folder=[self.folder], batch_size=32, stage_retries=1,
                                      adopt_log=None)
        engine.wait_external_base = Mock(return_value=False)
        engine.book, engine.run = Mock(), Mock()
        engine.command = Mock(return_value=['python', 'renderer', '--batch-size', '32'])
        process, identity = Mock(), {'pid': 42, 'create_time': 123}
        with patch.object(finisher.supervision, 'existing_renderer', return_value=(process, identity)):
            with self.assertRaisesRegex(finisher.StageFailure, 'pending units remain held'):
                engine.base(self.folder)
        self.assertEqual([c.args[0] for c in engine.run.call_args_list], ['base-all', 'base-resume-1'])
        self.assertEqual(engine.run.call_args_list[0].kwargs['adopt'][:2], (process, identity))
        finisher.supervision.write(self.folder / 'state.json', {'units': [{'id': 'other', 'status': 'verified'}]})
        self.assertFalse(finisher.base_terminal(self.folder))
        finisher.supervision.write(self.folder / 'state.json', {'units': [{'id': 'u1', 'status': 'held'}]})
        self.assertTrue(finisher.base_terminal(self.folder))

    def test_completion_accepts_report_bound_publication_and_rejects_changed_choice(self):
        context = SimpleNamespace(generation='frozen-generation', assignment={'units': [{'id': 'u1'}]}, recheck=Mock())
        report = {'units': [{'id': 'u1', 'selected': {'audio_sha256': 'a'*64}}],
                  'verified_units': 1, 'open_recognition_flags': 0, 'pending_units': 0}
        report_hash = finisher.supervision.digest(report)
        publication = finisher.supervision.digest({'frozen_generation': context.generation, 'recognition_report_sha256': report_hash})
        proof = {'frozen_generation': context.generation, 'generation': publication, 'recognition_report_sha256': report_hash,
                 'complete': True, 'music_ready': True, 'opening_seconds': 8, 'total_units': 1, 'verified_units': 1,
                 'open_recognition_flags': 0, 'pending_recognition_units': 0,
                 'captions': [{'id': 'u1', 'raw_audio_sha256': 'a'*64}],
                 'recognition_report': 'recognition-'+report_hash+'.json'}
        public = self.folder / 'public'
        public.mkdir()
        finisher.supervision.write(public / proof['recognition_report'], report)
        for kind in ('wav', 'mp3'):
            path = public / ('fixture.'+kind)
            path.write_bytes(b'synthetic completed export')
            proof[kind], proof[kind+'_sha256'] = path.name, finisher.supervision.file_hash(path)
        finisher.supervision.write(self.folder / 'complete.json', proof)
        finisher.supervision.write(self.folder / 'state.json', {'generation': publication, 'counts': {'verified': 1, 'held': 0},
                                      'units': [{'id': 'u1', 'status': 'verified'}]})
        self.assertNotEqual(publication, context.generation)
        self.assertTrue(finisher.validate_completion(self.folder, context, report)[0])
        self.assertFalse(finisher.validate_completion(self.folder, context, report, workers_stopped=False)[0])
        proof['captions'][0]['raw_audio_sha256'] = 'wrong-take'
        finisher.supervision.write(self.folder / 'complete.json', proof)
        self.assertFalse(finisher.validate_completion(self.folder, context, report)[0])


if __name__ == '__main__':
    unittest.main()
