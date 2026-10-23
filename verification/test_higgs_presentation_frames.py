import copy,sys,tempfile,unittest
from unittest.mock import patch
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'runtime'),str(ROOT/'scripts')]
from higgs_presentation_frames import apply_frames,publish_checked_readouts,repair_requested_readouts
from render_higgs_palette_preview import assignments
from rimsky.util import write_json,file_digest,digest
from rimsky.speech_quality import transcript_check

class PresentationPaletteTests(unittest.TestCase):
    def test_priority_display_uses_normal_gate_and_never_publishes_a_held_block(self):
        from higgs_opus_word_recovery import SectionHeld
        with tempfile.TemporaryDirectory() as work:
            root=Path(work);bank=root/'bank.json';write_json(bank,{})
            book={'source_hash':'book-sha','chapters':[{'id':'c1','blocks':[
                {'id':'p1','text':'Header'},{'id':'p2','text':'Field'},{'id':'p3','text':'Story'}]}]}
            rows=[{'id':'u'+str(i),'chapter_id':'c1','source_id':'p'+str(i)} for i in (1,2,3)]
            frame={'first':'p1','last':'p2','register':'mechanical','status':'checked_proposal'}
            frames={'p1':frame,'p2':frame};state={'chapters':{'c1':{}}}
            request={'source_sha256':'book-sha','ranges':[{'chapter':'c1','first':'p1','last':'p2'}]}
            write_json(root/'presentation-priority.json',request)
            with patch('render_higgs_palette_preview.assignments',side_effect=lambda c,o,r,*a:r) as assign,\
                 patch('render_higgs_palette_preview.check_and_retake',side_effect=SectionHeld('missing word')) as gate,\
                 patch('higgs_presentation_frames.publish_checked_readouts') as publish:
                repair_requested_readouts(root,book,rows,frames,None,{}, {},{},state,{},bank)
                self.assertEqual(['u1','u2'],[r['id'] for r in assign.call_args.args[2]])
                gate.assert_called_once();publish.assert_not_called()
                self.assertEqual(1,len(list(root.glob('c1/readout-repair-*/held.json'))))
                write_json(root/'presentation-priority.json',{**request,'source_sha256':'another-book'})
                with self.assertRaisesRegex(ValueError,'another book'):
                    repair_requested_readouts(root,book,rows,frames,None,{}, {},{},state,{},bank)

    def test_checked_display_publishes_independently_and_preserves_other_pcm_and_flags(self):
        import numpy as np
        import soundfile as sf
        from rimsky.audio_join import prepare_clip
        with tempfile.TemporaryDirectory() as work:
            root=Path(work);generation=root/'generation';chapter=generation/'c0001';chapter.mkdir(parents=True)
            media=root/'media';media.mkdir();page=root/'page';page.mkdir()
            (generation/'presentation-routes-v1/c0001').mkdir(parents=True)
            selected={};frames={};timeline=[];old=[];position=.5
            for i in (1,2):
                uid='u'+str(i);source='p'+str(i);text='Header' if i==1 else 'Field'
                frame={'register':'mechanical','status':'checked_proposal','first':'p1','last':'p2'};frames[source]=frame
                native=np.concatenate([np.zeros(2000),.03*np.sin(np.arange(2400*i)*.06),np.zeros(2000)]).astype('float32')
                take=root/uid;take.mkdir();wave=take/'audio.wav';sf.write(wave,native,24000)
                unit={'id':uid,'source_id':source,'text':text,'spoken':text,'voice':'mechanical_stats','render_voice':'mechanical_stats',
                    'speaker':'narrator','mood':'neutral','presentation_route':'mechanical_stats','synthesis':{'text':text},
                    'palette_assignment':{'presentation_frame_sha256':digest(frame)}}
                key=digest(unit['synthesis']);sha=file_digest(wave)
                check={**transcript_check(text,text,sha,'verifier'),'synthesis_key':key}
                selected[uid]={'unit':unit,'path':str(wave),'receipt':{'key':key,'audio_sha256':sha},'word_check':check}
                speech,_=prepare_clip(sf.read(wave,dtype='float32')[0],24000)
                # First waveform is already correct, but its recorded route is
                # wrong. Second waveform changes length after the correction.
                before=speech if i==1 else speech[:len(speech)//2]
                old.extend([before,np.zeros(1200,dtype='float32')])
                timeline.append({'id':uid,'text':text,'speaker':'narrator','voice':'normal','mood':'neutral',
                    'start':position,'end':position+len(before)/24000,'word_status':'spoken_text_mismatch'})
                position+=len(before)/24000+.05
            tail=np.full(12000,.005,dtype='float32');original=media/'old.wav'
            sf.write(original,np.concatenate([np.zeros(12000),*old,tail]),24000)
            timeline.append({'id':'ordinary','text':'Other words','speaker':'narrator','voice':'normal','mood':'neutral',
                'start':position,'end':position+.5,'word_status':'spoken_text_mismatch'})
            write_json(chapter/'c0001.timeline.json',timeline)
            state={'generation':'generation','status':'rendering','chapters':{'c0001':{'units':3,'complete':True,
                'stream':'/old.wav','wav_sha256':file_digest(original),'word_flags':3,'production_approved':False}}}
            before=copy.deepcopy(state)
            audit=lambda take:{'checks':{take.name:selected[take.name]['word_check']}}
            with patch('render_higgs_palette_preview.MEDIA',media),patch('render_higgs_palette_preview.PAGE',page),\
                 patch('render_higgs_palette_preview.page'),patch('higgs_preview_music.attach_music'),\
                 patch('regenerate_higgs_book_trial.cached_audit',side_effect=audit):
                self.assertFalse(publish_checked_readouts(generation,'c0001',{'u1':selected['u1']},frames,state,{'narrator:mechanical_stats':1}))
                self.assertEqual(before,state)
                self.assertTrue(publish_checked_readouts(generation,'c0001',selected,frames,state,{'narrator:mechanical_stats':1}))
            ready=state['chapters']['c0001'];result,_=sf.read(media/Path(ready['stream']).name,dtype='int16')
            initial,_=sf.read(original,dtype='int16')
            self.assertTrue(np.array_equal(result[:12000],initial[:12000]))
            self.assertTrue(np.array_equal(result[-12000:],initial[-12000:]))
            self.assertEqual(1,ready['word_flags']);self.assertFalse(ready['production_approved'])
            self.assertEqual('rendering',state['status'])

    def test_complete_readout_locks_headers_rows_and_parenthetical_counts_to_one_reference(self):
        frame={'register':'mechanical','status':'checked_proposal','role':'system_readout','first':'p1','last':'p3'}
        reference={'id':'ref','audio_sha256':'audio','text':'Reviewed mechanical performance'}
        groups={'system':{'voice_family':'mechanical_stats','character':'narrator','moods':{'neutral':[reference]}},
            'other':{'voice_family':'mechanical_stats','character':'unknown','moods':{'neutral':[reference]}},
            'normal':{'voice_family':'narrator_baseline','character':'narrator','moods':{'neutral':[reference]}}}
        rows=[{'id':'u'+str(i),'source_id':'p'+str(i),'speaker':'narrator','mode':'narrator','text':text,'_template':'normal'}
            for i,text in enumerate(['Эо. Человек 162-го уровня.','Основные параметры:', '- Сила 100 (ячеек — 7);'],1)]
        blocks={r['source_id']:{'text':r['text'],'formatting':{'full':['emphasis']}} for r in rows}
        class NoCalls:
            def call(self,*args):raise AssertionError('Checked readout should not receive sentence-level mood guesses')
        cast={'narrator':{'template_id':'normal','baseline_mood':'neutral','needs_review':False}}
        context={'directions':None,'mood_system':'test','presentation_frames':{k:frame for k in blocks}}
        original=copy.deepcopy(rows)
        with tempfile.TemporaryDirectory() as work:
            result=assignments(NoCalls(),Path(work),rows,blocks,cast,groups,context)
        self.assertEqual(original,rows)
        self.assertEqual({r['voice'] for r in result},{'mechanical_stats'})
        self.assertEqual({r['mood'] for r in result},{'neutral'})
        self.assertEqual({r['palette_reference']['audio_sha256'] for r in result},{'audio'})
        self.assertTrue(all(r['presentation_route']=='mechanical_stats' for r in result))
        unresolved={**frame,'status':'needs_adjudication'}
        with self.assertRaisesRegex(ValueError,'adjudication'):apply_frames(copy.deepcopy(rows),{'p1':unresolved},groups)

