import copy
import sys
import unittest
import tempfile
from unittest.mock import patch
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'));sys.path.insert(0,str(ROOT/'runtime'))
from render_higgs_palette_preview import (chunk_rows, templates, retry_units, check_and_retake,
                                          chapter_work_order, frozen_retry_pending)
from rimsky.util import write_json,file_digest
from render_higgs_book_trial import validate_voice_continuity


class PalettePreviewTests(unittest.TestCase):
    def test_missing_openings_are_attempted_before_later_chapter_work(self):
        work=chapter_work_order(['chapter2','chapter3','chapter1'],{'chapter1':{'complete':True,'word_flags':45}})
        self.assertEqual(work[:2],[('chapter2',True),('chapter3',True)])
        self.assertEqual(work[2:],[('chapter2',False),('chapter3',False),('chapter1',False)])
        self.assertEqual(chapter_work_order(['only'],{'only':{}}),[('only',False)])

    def test_reference_set_changes_delivery_without_recasting(self):
        base={'speaker':'actor','render_voice':'male','voice':'male',
              'synthesis':{'voice_family':'male','reference':{'sha256':'ordinary'}},
              'palette_reference':{'character':'example_actor'},
              'palette_assignment':{'template_id':'one','source_character':'example_actor',
                                    'allowed_reference_sha256':['ordinary','soft']}}
        quiet=copy.deepcopy(base);quiet['synthesis']['reference']['sha256']='soft'
        validate_voice_continuity([base,quiet])
        quiet['palette_assignment']['template_id']='another'
        with self.assertRaisesRegex(ValueError,'reference switch'):validate_voice_continuity([base,quiet])
        quiet=copy.deepcopy(base);quiet['synthesis']['reference']['sha256']='outside'
        with self.assertRaisesRegex(ValueError,'outside'):validate_voice_continuity([base,quiet])

    def test_palette_keeps_character_and_presentation_routes_separate(self):
        bank={'entries':[{'voice_family':'same','character':actor,'route':route,'mood':'calm','candidates':[{'id':actor+route}]}
                         for actor,route in [('one','dialogue'),('two','dialogue'),('one','narration')]]}
        self.assertEqual(len(templates(bank)),3)

    def test_chunking_preserves_every_sentence_and_source_order(self):
        rows=[{'id':str(i),'text':'one two three four five six'} for i in range(250)]
        chunks=chunk_rows(rows)
        self.assertGreater(len(chunks),1)
        self.assertEqual([r for chunk in chunks for r in chunk],rows)

    def test_edge_recovery_uses_same_character_mood_and_stable_unit_seed(self):
        refs=[{'audio_sha256':key,'character':'example','voice_family':'male','mood':'calm'}
              for key in ['ordinary','alternate']]
        row={'id':'one','voice':'male','mood':'calm','text':'unchanged',
             'palette_reference':refs[0],'palette_assignment':{'template_id':'one',
              'source_character':'example','allowed_reference_sha256':['ordinary','alternate']}}
        groups={'one':{'moods':{'calm':refs}}}
        recovered=retry_units([row],3,groups)[0]
        self.assertEqual(recovered['palette_reference'],refs[1])
        self.assertEqual(recovered['text'],row['text'])
        self.assertEqual(recovered['mood'],row['mood'])
        self.assertEqual(row['palette_reference'],refs[0])
        self.assertNotIn('render_seed',row)
        self.assertEqual(recovered['render_seed'],retry_units([{'id':'other'},row],3,groups)[1]['render_seed'])
        self.assertNotEqual(recovered['render_seed'],retry_units([row],4,groups)[0]['render_seed'])
        self.assertNotIn('onset_pause',recovered)
        self.assertEqual(retry_units([row],2,groups)[0],{**row,'retake_number':2})

    def test_edge_recovery_refuses_reference_outside_frozen_voice(self):
        row={'id':'one','voice':'male','mood':'calm','palette_assignment':{'template_id':'one',
             'source_character':'example','allowed_reference_sha256':['approved']}}
        for ref in [{'audio_sha256':'outside','character':'example','voice_family':'male'},
                    {'audio_sha256':'approved','character':'someone_else','voice_family':'male'}]:
            with self.assertRaisesRegex(ValueError,'escaped'):
                retry_units([row],3,{'one':{'moods':{'calm':[ref]}}})

    def test_regenerated_units_use_independent_pinned_early_retry_seeds(self):
        row={'id':'one','render_seed':1234}
        first=retry_units([row],0,{})[0]
        second=retry_units([row],1,{})[0]
        self.assertNotEqual(first['render_seed'],second['render_seed'])
        self.assertEqual(first['render_seed'],retry_units([{'id':'other'},row],0,{})[1]['render_seed'])
        self.assertEqual(row['render_seed'],1234)

    def test_seeded_resume_replays_frozen_take_zero_and_carries_new_pending_units(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'assignment.json'
            original=[{'id':'a'},{'id':'b'},{'id':'c'}]
            write_json(path,{'units':[original[1]]})
            pending,carried=frozen_retry_pending(path,[original[1],original[2]],original,[])
            self.assertEqual([row['id'] for row in pending],['b'])
            self.assertEqual([row['id'] for row in carried],['c'])

    def run_fake_recovery(self,edges,status,omission=False,recover_words=True,timeout_first=False,groups=None,deep=True,witness=None):
        calls=[]
        def render(assignment,take,chapters,**kwargs):
            calls.append(take.name)
            unit={'id':'line'};wav=take/'line.wav';wav.write_bytes(b'bound-take')
            write_json(take/'render-plan.json',{'units':[unit]})
            write_json(take/'receipts.json',{'takes':{'line':{'audio':'line.wav',
                       'audio_sha256':file_digest(wav),'key':take.name}}})
            if timeout_first and len(calls)==1:
                from rimsky.native_progress import CandidateRenderTimeout
                raise CandidateRenderTimeout('Batch stopped after this completed take')
        def audit(take):
            recovered=omission and recover_words and take.parent.name=='word-omission-recovery'
            words={'expected_words':['one','two'],'heard_words':['one','two'] if recovered else ['one']} if omission else {}
            return {'checks':{'line':{'status':'asr_exact_match' if recovered else status,'synthesis_key':take.name,
                    'audio_sha256':file_digest(take/'line.wav'),**words}}}
        with tempfile.TemporaryDirectory() as folder, \
             patch('render_higgs_palette_preview.render',side_effect=render), patch('render_higgs_book_trial.render_many',side_effect=lambda jobs,**kw:[render(a,t,c,**kw) for a,t,c in jobs]), \
             patch('render_higgs_palette_preview.cached_audit',side_effect=audit), \
             patch('render_higgs_palette_preview.boundary_check',side_effect=edges), \
             patch('soundfile.read',return_value=([0.1],24000)),              patch('render_higgs_palette_preview.early_witnesses',side_effect=witness or (lambda out,a,c,selected:selected)):
            selected=check_and_retake(Path(folder),{'units':[{'id':'line'}]},'chapter',groups=groups,deep=deep)
        return calls,selected

    def test_first_take_verified_by_early_witness_is_not_retaken(self):
        seen=[]
        def witness(out,assignment,chapter,selected):
            seen.append(sorted(selected))
            return {k:{**r,'word_check':{**r['word_check'],'status':'asr_exact_match'}} for k,r in selected.items()}
        calls,selected=self.run_fake_recovery([{'unsafe_start':False,'unsafe_end':False}],'spoken_text_mismatch',
                                              groups={},witness=witness)
        self.assertEqual(calls,['take-0']);self.assertEqual(seen,[['line']])
        self.assertEqual(selected[0]['word_check']['status'],'asr_exact_match')

    def test_first_pass_defers_per_unit_recovery_and_holds_the_section(self):
        from higgs_opus_word_recovery import SectionHeld
        with self.assertRaisesRegex(SectionHeld,'Deferred to batched recovery; section held: line'):
            self.run_fake_recovery([{'unsafe_start':False,'unsafe_end':False}]*3,'spoken_text_mismatch',groups={},deep=False)

    def test_unsafe_edges_recover_after_initial_three_takes_without_bypassing_gate(self):
        unsafe={'unsafe_start':True,'unsafe_end':False}
        safe={'unsafe_start':False,'unsafe_end':False}
        calls,selected=self.run_fake_recovery([unsafe,unsafe,unsafe,safe],'asr_exact_match')
        self.assertEqual(calls,['take-0','take-1','take-2','take-3'])
        self.assertEqual(selected[0]['receipt']['key'],'take-3')

    def test_batch_timeout_does_not_discard_a_completed_word_and_edge_verified_take(self):
        calls,selected=self.run_fake_recovery([{'unsafe_start':False,'unsafe_end':False}],
            'asr_exact_match',timeout_first=True)
        self.assertEqual(calls,['take-0']);self.assertEqual(selected[0]['receipt']['key'],'take-0')

    def test_safe_wave_with_substitution_is_held_rather_than_published_with_a_warning(self):
        safe={'unsafe_start':False,'unsafe_end':False}
        with self.assertRaisesRegex(ValueError,'Unverified spoken words remain'):
            self.run_fake_recovery([safe]*3,'spoken_text_mismatch')

    def test_safe_wave_with_missing_word_gets_recovered_instead_of_published(self):
        safe={'unsafe_start':False,'unsafe_end':False}
        calls,selected=self.run_fake_recovery([safe]*4,'spoken_text_mismatch',omission=True)
        self.assertEqual(len(calls),4)
        self.assertEqual(selected[0]['word_check']['status'],'asr_exact_match')
        self.assertIn('word-omission-recovery',selected[0]['path'])

    def test_unresolved_missing_word_holds_section_after_bounded_recovery(self):
        safe={'unsafe_start':False,'unsafe_end':False}
        with self.assertRaisesRegex(ValueError,'Missing spoken words remain'):
            self.run_fake_recovery([safe]*9,'spoken_text_mismatch',omission=True,recover_words=False)


if __name__=='__main__':unittest.main()
