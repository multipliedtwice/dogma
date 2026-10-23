import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'runtime'), str(ROOT / 'scripts')]

from rimsky import stress_witness as sw
from render_higgs_palette_preview import check_and_retake
from render_higgs_book_trial import write_json, file_digest

SAFE = {'unsafe_start': False, 'unsafe_end': False}


def verdict(margin):
    return None if margin is None else {'words': [{'word': 'замок', 'intended': 1, 'predicted': 0, 'margin': margin}],
                                        'suspect': margin < -sw.THETA, 'worst_margin': margin, 'rule': sw.RULE}


class WitnessModule(unittest.TestCase):
    def test_marked_words_need_exactly_one_acute_and_report_vowel_index_and_position(self):
        text = 'Он закры́л зам́ок и сто́роны, но́́ нет'
        self.assertEqual(sw.marked_words('Он закры́л кот'), [('закрыл', 1, 1)])
        self.assertEqual([(w, i) for w, i, _ in sw.marked_words(text)][:1], [('закрыл', 1)])
        self.assertNotIn('но', [w for w, _, _ in sw.marked_words(text)])
        self.assertEqual(sw.marked_words('без ударений'), [])

    def test_margin_is_intended_minus_best_other(self):
        x = [[1.0], [3.0], [2.0]]
        self.assertEqual(sw.margin(x, [1.0], 1), 1.0)
        self.assertEqual(sw.margin(x, [1.0], 0), -2.0)

    def test_judge_flags_only_beyond_theta_and_records_provenance(self):
        text = 'зам́ок'
        for m_target, suspect in ((-sw.THETA - 0.1, True), (-sw.THETA + 0.1, False), (3.0, False)):
            x = [[0.0], [0.0]]
            with patch.object(sw, 'features', return_value={0: ('замок', x)}), \
                 patch.object(sw, 'margin', return_value=m_target), patch.object(sw, 'weights', return_value=([1.0], 'abc')):
                result = sw.judge('x.wav', 'зам́ок')
            self.assertEqual(result['suspect'], suspect)
            self.assertEqual(result['rule'], 'stress-witness-trigger-v1')
            self.assertEqual(result['weights_sha256'], 'abc')

    def test_unmeasured_words_are_never_suspect(self):
        with patch.object(sw, 'features', return_value={}), patch.object(sw, 'weights', return_value=([1.0], 'abc')):
            result = sw.judge('x.wav', 'зам́ок')
        self.assertEqual(result['words'], [])
        self.assertFalse(result['suspect'])
        self.assertIsNone(result['worst_margin'])

    def test_calibrated_weights_are_hashed(self):
        w, digest = sw.weights()
        self.assertEqual(len(w), 8)
        self.assertEqual(digest, file_digest(sw.WEIGHTS))


class RetakeTrigger(unittest.TestCase):
    def run_takes(self, margins, enabled=True, exact=True):
        """margins[n] is the witness worst margin of take-n (None: unmeasured)."""
        calls, judged = [], []
        self.judged = judged

        def render(assignment, take, chapters, **kwargs):
            calls.append(take.name)
            wav = take / 'line.wav'
            wav.write_bytes(take.name.encode())
            write_json(take / 'render-plan.json', {'units': [{'id': 'line', 'synthesis': {'text': 'зам́ок'}}]})
            write_json(take / 'receipts.json', {'takes': {'line': {'audio': 'line.wav', 'audio_sha256': file_digest(wav), 'key': take.name}}})

        def audit(take):
            return {'checks': {'line': {'status': 'asr_exact_match' if exact else 'spoken_text_mismatch', 'synthesis_key': take.name,
                                        'audio_sha256': file_digest(take / 'line.wav')}}}

        def judge(wav, unit):
            judged.append(Path(wav).parent.name)
            return verdict(margins[int(Path(wav).parent.name.split('-')[1])])

        with tempfile.TemporaryDirectory() as folder, \
             patch('render_higgs_palette_preview.render', side_effect=render), \
             patch('render_higgs_palette_preview.cached_audit', side_effect=audit), \
             patch('render_higgs_palette_preview.boundary_check', return_value=SAFE), \
             patch('render_higgs_palette_preview.stress_judge', side_effect=judge), \
             patch('render_higgs_palette_preview.verify_with_prior_takes', side_effect=lambda out, o, c, selected: selected), \
             patch('rimsky.stress_witness.release'), \
             patch('soundfile.read', return_value=([0.1], 24000)):
            if enabled:
                write_json(Path(folder) / 'manifest.json', {'stress_witness': sw.RULE})
            selected = check_and_retake(Path(folder), {'units': [{'id': 'line'}]}, 'chapter')
        return calls, judged, selected

    def test_suspect_exact_take_is_selected_and_retaken(self):
        calls, judged, selected = self.run_takes([-4.0, 1.0])
        self.assertEqual(calls, ['take-0', 'take-1'])
        self.assertEqual(selected[0]['receipt']['key'], 'take-1')
        self.assertFalse(selected[0]['stress_witness']['suspect'])

    def test_later_better_margin_replaces_even_if_still_suspect(self):
        calls, _, selected = self.run_takes([-4.0, -3.0, -2.9])
        self.assertEqual(calls, ['take-0', 'take-1', 'take-2'])
        self.assertEqual(selected[0]['receipt']['key'], 'take-2')

    def test_worse_or_unmeasured_later_take_does_not_replace_a_measured_one(self):
        for margins in ([-3.0, -5.0, -6.0], [1.0, None, None], [1.0, -9.0, -9.0]):
            calls, _, selected = self.run_takes(margins)
            self.assertEqual(selected[0]['receipt']['key'], 'take-0', margins)
            self.assertEqual(calls, ['take-0', 'take-1', 'take-2'] if margins[0] < -sw.THETA else ['take-0'])

    def test_unmeasured_take_may_replace_a_suspect_one(self):
        _, _, selected = self.run_takes([-4.0, None, None])
        self.assertEqual(selected[0]['receipt']['key'], 'take-1')

    def test_stress_suspicion_alone_never_holds_the_section(self):
        calls, _, selected = self.run_takes([-9.0] * 9)
        self.assertEqual(calls, ['take-0', 'take-1', 'take-2'])
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]['word_check']['status'], 'asr_exact_match')
        self.assertTrue(selected[0]['stress_witness']['suspect'])

    def test_witness_never_runs_without_the_manifest_key(self):
        calls, judged, selected = self.run_takes([-9.0] * 9, enabled=False)
        self.assertEqual((calls, judged), (['take-0'], []))
        self.assertNotIn('stress_witness', selected[0])

    def test_witness_skips_takes_that_failed_the_word_check(self):
        from higgs_opus_word_recovery import SectionHeld
        with self.assertRaises((SectionHeld, ValueError)):
            self.run_takes([-9.0] * 9, exact=False)
        self.assertEqual(self.judged, [])


class ModelLoads(unittest.TestCase):
    def run_section(self, n, rescue):
        """n marked units in one take folder; with rescue the last one is word-verified only by a secondary witness."""
        state = {'loaded': False, 'loads': 0, 'judged': 0}
        ids = [f'u{i}' for i in range(n)]

        def judge(wav, text):
            if not state['loaded']:
                state['loaded'] = True; state['loads'] += 1
            state['judged'] += 1
            return {**verdict(1.0), 'suspect': False}

        def release():
            state['loaded'] = False

        def render(assignment, take, chapters, **kwargs):
            plan, receipts = [], {}
            for uid in ids:
                wav = take / (uid + '.wav'); wav.write_bytes((take.name + uid).encode())
                plan.append({'id': uid, 'synthesis': {'text': 'зам́ок'}})
                receipts[uid] = {'audio': uid + '.wav', 'audio_sha256': file_digest(wav), 'key': take.name}
            write_json(take / 'render-plan.json', {'units': plan}); write_json(take / 'receipts.json', {'takes': receipts})

        def audit(take):
            return {'checks': {uid: {'status': 'spoken_text_mismatch' if rescue and uid == ids[-1] else 'asr_exact_match',
                                     'synthesis_key': take.name, 'audio_sha256': file_digest(take / (uid + '.wav'))} for uid in ids}}

        def secondary(out, selected):
            return {k: {**r, 'word_check': {**r['word_check'], 'status': 'asr_exact_match'}} for k, r in selected.items()}

        with tempfile.TemporaryDirectory() as folder,              patch('render_higgs_palette_preview.render', side_effect=render),              patch('render_higgs_palette_preview.cached_audit', side_effect=audit),              patch('render_higgs_palette_preview.cached_secondary', side_effect=secondary),              patch('render_higgs_palette_preview.boundary_check', return_value=SAFE),              patch('render_higgs_palette_preview.verify_with_prior_takes', side_effect=lambda out, o, c, selected: selected),              patch('rimsky.stress_witness.judge', side_effect=judge), patch('rimsky.stress_witness.release', side_effect=release),              patch('soundfile.read', return_value=([0.1], 24000)):
            write_json(Path(folder) / 'manifest.json', {'stress_witness': sw.RULE})
            selected = check_and_retake(Path(folder), {'units': [{'id': u} for u in ids]}, 'chapter')
        self.assertFalse(state['loaded'])
        return state, selected

    def test_one_load_per_take_folder_and_every_unit_is_judged(self):
        state, selected = self.run_section(80, rescue=False)
        self.assertEqual((state['loads'], state['judged']), (1, 80))
        self.assertTrue(all('stress_witness' in r for r in selected))

    def test_secondary_verified_takes_are_judged_in_one_more_load(self):
        state, selected = self.run_section(80, rescue=True)
        self.assertEqual((state['loads'], state['judged']), (2, 80))
        self.assertTrue(all('stress_witness' in r for r in selected))


class ReuseCandidates(unittest.TestCase):
    def reuse(self, margins, key=True, older=1):
        """margins: worst margin per earlier-generation candidate (one per uid, `older` older generations each)."""
        import higgs_previous_verified_takes as hv
        from render_higgs_book_trial import spoken_text
        from rimsky.spoken_pronunciation import phrase_stress
        from rimsky.util import digest
        text = 'зам́ок'
        native, _ = phrase_stress(spoken_text(text))
        state = {'loaded': False, 'loads': 0}
        by_wav = {}

        def judge(wav, t):
            if not state['loaded']:
                state['loaded'] = True; state['loads'] += 1
            return verdict(by_wav[Path(wav).name])

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = {'source_sha256': 's', **({'stress_witness': sw.RULE} if key else {})}
            new = root / 'audio-palette-z'
            write_json(new / 'manifest.json', manifest)
            out = new / 'c0001/chunk-001'
            out.mkdir(parents=True)
            units, n = [], 0
            for i, m in enumerate(margins):
                uid = f'u{i}'
                units.append({'id': uid, 'text': text, 'source_id': 'x', 'start': 0, 'end': 1, 'speaker': 'a', 'voice': 'v', 'mood': 'calm',
                              'mode': 'm', 'palette_assignment': {'template_id': 't'}})
                for g in range(older):
                    old = root / f'audio-palette-a{g}'
                    write_json(old / 'manifest.json', {'source_sha256': 's'})
                    take = old / 'c0001/chunk-001/take-0'
                    take.mkdir(parents=True, exist_ok=True)
                    name = f'{uid}-{g}.wav'
                    by_wav[name] = m[g] if isinstance(m, list) else m
                    (take / name).write_bytes(name.encode())
                    plan = {'backend': 'higgs_audio_cpp', 'effects_policy': 'disabled', 'prompt': native, 'text': native, 'model_sha256': 'm',
                            'runner_sha256': 'm', 'reference': {'sha256': 'ref', 'text': 'rt'}, 'sampling': {'seed': 1}}
                    key_ = digest(plan)
                    unit = {**{k: units[-1][k] for k in ('text', 'source_id', 'start', 'end', 'speaker', 'voice', 'mood', 'mode')},
                            'id': uid, 'synthesis': plan, 'key': key_, 'spoken': 'x', 'render_voice': 'v', 'audio': name}
                    write_json(take / 'render-plan.json', {'units': [unit]})
                    row = {'unit': unit, 'path': str(take / name), 'receipt': {'key': key_, 'audio_sha256': file_digest(take / name)},
                           'word_check': {'status': 'asr_exact_match'}}
                    sel = old / 'c0001/chunk-001/selected.json'
                    prior = json.loads(sel.read_text('utf8')) if sel.exists() else {}
                    write_json(sel, {**prior, uid: row})
            groups = {'t': {'moods': {'calm': [{'audio_sha256': 'ref', 'text': 'rt'}]}}}
            with patch('higgs_file_checks.cached_sha', return_value='m'),                  patch('render_higgs_book_trial.RUNNER', 'r'),                  patch('regenerate_higgs_book_trial.cached_audit', side_effect=lambda take: {'checks': {}}),                  patch('higgs_secondary_verification.cached_secondary', side_effect=lambda o, c: c),                  patch('rimsky.audio_join.boundary_check', return_value=SAFE),                  patch('rimsky.stress_witness.judge', side_effect=judge),                  patch('rimsky.stress_witness.release', side_effect=lambda: state.update(loaded=False)),                  patch('soundfile.read', return_value=([0.1], 24000)):
                # audits must report each uid as exact
                def audit(take):
                    return {'checks': {u['id']: {'status': 'asr_exact_match'} for u in units}}
                with patch('regenerate_higgs_book_trial.cached_audit', side_effect=audit):
                    found = hv.reuse(out, {'source_sha256': 's', 'units': units}, groups)
            self.assertFalse(state['loaded'])
            saved = json.loads((out / 'earlier-verified-takes.json').read_text('utf8')) if found else None
        return found, state, saved

    def test_suspect_candidate_is_not_reused_and_clean_one_is(self):
        found, state, saved = self.reuse([-4.0, 1.0])
        self.assertEqual(sorted(found), ['u1'])
        self.assertEqual(found['u1']['stress_witness']['worst_margin'], 1.0)
        self.assertEqual(saved['u1']['stress_witness']['rule'], sw.RULE)

    def test_one_load_for_many_candidates(self):
        found, state, _ = self.reuse([1.0] * 40)
        self.assertEqual((len(found), state['loads']), (40, 1))

    def test_next_older_clean_candidate_replaces_a_suspect_one(self):
        found, _, _ = self.reuse([[-4.0, 2.0]], older=2)
        self.assertEqual(found['u0']['earlier_generation_reuse']['generation'], 'audio-palette-a1')

    def test_unchanged_without_the_manifest_key(self):
        found, state, _ = self.reuse([-9.0, 1.0], key=False)
        self.assertEqual(sorted(found), ['u0', 'u1'])
        self.assertEqual(state['loads'], 0)
        self.assertNotIn('stress_witness', found['u0'])


if __name__ == '__main__':
    unittest.main()
