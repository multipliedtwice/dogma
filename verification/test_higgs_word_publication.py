import sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
import soundfile as sf
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'runtime'),str(ROOT/'scripts')]
from higgs_verified_word_publication import publish_verified_repairs,checked_take,refresh_saved_witness
from rimsky.util import digest,file_digest,write_json
from rimsky.speech_quality import transcript_check
from rimsky.audio_join import prepare_clip


class WordPublicationTests(unittest.TestCase):
    def test_saved_witness_is_recomputed_from_bound_raw_report_not_relaxed(self):
        with tempfile.TemporaryDirectory() as work:
            root=Path(work);wav=root/'take.wav';sf.write(wav,np.r_[np.zeros(1000),np.full(2000,.02),np.zeros(1000)],24000)
            evidence=root/'report.json';write_json(evidence,{'checks':{}})
            plan={'text':'Words'};key=digest(plan);sha=file_digest(wav)
            primary={'audio_sha256':sha,'synthesis_key':key,'expected_text_sha256':'words','status':'spoken_text_mismatch'}
            old={**primary,'status':'asr_exact_match','verifier_sha256':'old-comparator',
                'independent_report':str(evidence),'independent_report_sha256':file_digest(evidence)}
            row={'unit':{'id':'line','synthesis':plan},'path':str(wav),'receipt':{'key':key,'audio_sha256':sha},'word_check':old}
            fresh={**old,'verifier_sha256':'new-comparator','proper_name_boundaries':[]}
            with patch('higgs_secondary_verification.apply_secondary',return_value={'line':{**row,'word_check':fresh}}) as replay:
                result=refresh_saved_witness(root,row,{root:{'checks':{'line':primary}}})
            self.assertEqual(result['word_check'],fresh);self.assertEqual(row['word_check'],old)
            self.assertEqual(replay.call_args.args[0]['line']['word_check'],primary)
            self.assertFalse(result['witness_revalidation']['recognition_rerun'])
            evidence.write_text('changed')
            with patch('higgs_secondary_verification.apply_secondary') as replay:
                self.assertEqual(refresh_saved_witness(root,row,{}),row);replay.assert_not_called()

    def test_only_verified_flagged_sentence_changes_and_others_remain_held(self):
        with tempfile.TemporaryDirectory() as work:
            root=Path(work);chapter=root/'c1';chapter.mkdir();media=root/'media';media.mkdir();page=root/'page';page.mkdir()
            take=root/'take';take.mkdir();new=take/'line.wav'
            sf.write(new,np.concatenate([np.zeros(2400),.02*np.sin(np.arange(2400)*.08),np.zeros(2400)]),24000)
            prepared,_=prepare_clip(sf.read(new,dtype='float32')[0],24000)
            unit={'id':'line','text':'One two.','spoken':'One two.','speaker':'actor','voice':'male','render_voice':'male',
                'mood':'neutral','synthesis':{'text':'One two.','prompt':'One two.',
                    'effects_policy':'disabled','backend':'higgs_audio_cpp'}}
            key=digest(unit['synthesis']);sha=file_digest(new)
            check={**transcript_check(unit['spoken'],unit['spoken'],sha,'verifier'),'synthesis_key':key}
            row={'unit':unit,'path':str(new),'receipt':{'key':key,'audio_sha256':sha},'word_check':check}
            original=media/'original.wav';sf.write(original,np.concatenate([np.full(12000,.005),np.zeros(2400),np.full(12000,.008)]),24000)
            timeline=[{'id':'line','text':'One two.','speaker':'actor','voice':'male','mood':'neutral',
                'start':.5,'end':.6,'word_status':'spoken_text_mismatch'},
                {'id':'unresolved','text':'Other words.','speaker':'actor','voice':'male','mood':'neutral',
                'start':.6,'end':1.1,'word_status':'spoken_text_mismatch'}]
            write_json(chapter/'c1.timeline.json',timeline)
            state={'status':'rendering','chapters':{'c1':{'stream':'/original.wav','wav_sha256':file_digest(original),
                'word_flags':2,'units':2,'seconds':1.1,'production_approved':False}}}
            with patch('regenerate_higgs_book_trial.cached_audit',return_value={'checks':{'line':check}}),\
                 patch('render_higgs_palette_preview.MEDIA',media),patch('render_higgs_palette_preview.PAGE',page),\
                 patch('render_higgs_palette_preview.page'),patch('higgs_preview_music.attach_music',side_effect=OSError('mix unavailable')) as music:
                write_json(root/'spoken-take-revisions.json',{'units':{'line':{}}})
                self.assertFalse(publish_verified_repairs(root,'c1',{'line':row},state,{'actor:male':1}))
                (root/'spoken-take-revisions.json').unlink()
                bad={**row,'unit':{**unit,'id':'unresolved','text':'Incorrect words.'}}
                self.assertTrue(publish_verified_repairs(root,'c1',{'unresolved':bad,'line':row},state,{'actor:male':1}))
                self.assertFalse(publish_verified_repairs(root,'c1',{'line':row},state,{'actor:male':1}))
                music.assert_called_once()
            ready=state['chapters']['c1'];output,_=sf.read(media/Path(ready['stream']).name,dtype='int16');before,_=sf.read(original,dtype='int16')
            self.assertTrue(np.array_equal(before[:12000],output[:12000]))
            self.assertTrue(np.array_equal(before[14400:],output[12000+len(prepared):]))
            self.assertEqual(1,ready['word_flags']);self.assertFalse(ready['production_approved'])
            self.assertIn('music_pending',ready)
            committed=__import__('json').loads((root/'progress.json').read_text('utf8'))['chapters']['c1']
            self.assertEqual(ready['wav_sha256'],committed['wav_sha256'])
            self.assertTrue(list((chapter/'verified-word-publication/rejections').glob('*.json')))
            self.assertEqual('rendering',state['status'])
            saved=__import__('json').loads((chapter/'c1.timeline.json').read_text('utf8'))
            self.assertEqual('spoken_text_mismatch',saved[1]['word_status'])
            changed={**unit,'spoken':'One.'}
            with self.assertRaisesRegex(ValueError,'source words'):checked_take(root,{**row,'unit':changed},{})
            from higgs_published_timeline import read_timeline
            # A stale compatibility alias cannot change the committed timeline.
            write_json(chapter/'c1.timeline.json',timeline)
            self.assertEqual('asr_exact_match',read_timeline(root,'c1',ready)[0]['word_status'])


if __name__=='__main__':unittest.main()
