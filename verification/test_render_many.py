import copy,json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
import soundfile as sf
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'runtime'),str(ROOT/'scripts')]
import render_higgs_book_trial as renderer
import higgs_exhausted_word_recovery as recovery
from rimsky.util import write_json,file_digest
from rimsky.native_progress import CandidateRenderFailure

PACK={'speech':{'model':'model'},'files':{'model':'a'*64},'voices':[{'id':'voice'}]}

def synthesis(text,*args,**kwargs):
    return {'text':text,'prompt':text,'voice_family':'voice','reference':{'audio':'ref.wav','text':'Образец.','sha256':'b'*64},
        'sampling':{'seed':1,'temperature':.8,'top_k':50,'top_p':.95,'repetition_penalty':1.1,'do_sample':True},
        'max_tokens':2048,'chunk_mode':'tag_aware','chunk_size':65536}

def unit(uid,seed,text='Слово.'):
    return {'id':uid,'chapter_id':'c1','source_id':'p','text':text,'speaker':'speaker','voice':'voice','mood':'baseline',
            'kind':'paragraph','render_seed':seed}

class Native:
    """Stands in for audiocpp: one WAV per request id, loudness derived from the request's seed."""
    def __init__(self):self.launches=[]
    def __call__(self,command,**kwargs):
        sequence=json.loads(Path(command[command.index('--request-sequence')+1]).read_text('utf-8'))['requests']
        staging=Path(command[command.index('--out-dir')+1]);self.launches.append([r['id'] for r in sequence])
        for r in sequence:sf.write(staging/(r['id']+'.wav'),np.full(24000,.01*r['seed']),24000,subtype='PCM_16')
        class Process:
            returncode=0
            def poll(self):return 0
        return Process()

class RenderManyTests(unittest.TestCase):
    def run_jobs(self,root,jobs,many):
        native=Native()
        with patch.object(renderer,'make_packs',return_value=({'voice':PACK},{})),\
             patch.object(renderer,'spoken_text',side_effect=lambda text,*a:text),\
             patch.object(renderer,'synthesis_plan',side_effect=lambda pack,voice,text,*a:synthesis(text)),\
             patch.object(renderer,'STAGING',root/'stg'),patch.object(renderer.subprocess,'Popen',native):
            if many:renderer.render_many(jobs)
            else:
                for job in jobs:renderer.render(*job)
        return native

    def jobs(self,root,tag):
        out=[]
        for name,units in (('a',[unit('u1',3),unit('u2',4)]),('b',[unit('u1',7)])):
            folder=root/(tag+name);folder.mkdir();path=folder/'assignment.json'
            path.write_text(json.dumps({'units':units}),encoding='utf-8');out.append((path,folder,['c1']))
        return out

    def test_two_folders_share_one_launch_with_unique_ids_and_own_receipts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);shared=self.jobs(root,'m');native=self.run_jobs(root,shared,True)
            self.assertEqual(len(native.launches),1)
            ids=native.launches[0];self.assertEqual(len(ids),3);self.assertEqual(len(set(ids)),3)
            self.assertEqual(set(ids),{'0-u1','0-u2','1-u1'})
            a,b=shared[0][1],shared[1][1]
            ra=json.loads((a/'receipts.json').read_text('utf-8'))['takes'];rb=json.loads((b/'receipts.json').read_text('utf-8'))['takes']
            self.assertEqual(sorted(ra),['u1','u2']);self.assertEqual(sorted(rb),['u1'])
            # Same unit id, different seed -> different WAV per folder, each bound to its own receipt.
            self.assertNotEqual(ra['u1']['audio_sha256'],rb['u1']['audio_sha256'])
            for folder,receipts in ((a,ra),(b,rb)):
                for uid,row in receipts.items():self.assertEqual(file_digest(folder/row['audio']),row['audio_sha256'])
            self.assertFalse((a/'candidate-render-failure.json').exists())
            # Each folder documents only its own requests under the original ids, and points at the session.
            for folder,count in ((a,2),(b,1)):
                records=[json.loads(p.read_text('utf-8')) for p in folder.glob('sequence-*.json') if not p.name.endswith(('.session.json','.shared.json'))]
                self.assertEqual([len(r['requests']) for r in records],[count])
                self.assertTrue(all(not r['id'][0].isdigit() for r in records[0]['requests']))
                self.assertEqual(len(list(folder.glob('sequence-*.session.json'))),1)
            # An identical resumed job set does not trip the frozen-sequence check and renders nothing.
            again=self.run_jobs(root,shared,True);self.assertEqual(again.launches,[])

    def test_single_folder_render_is_unchanged_and_matches_the_shared_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);single=self.jobs(root,'s');many=self.jobs(root,'m')
            native=self.run_jobs(root,single,False)
            self.assertEqual(native.launches,[['u1','u2'],['u1']])  # plain unit ids, one launch per folder
            self.run_jobs(root,many,True)
            for (_,s,_),(_,m,_) in zip(single,many):
                # Same sound inputs, keys and audio; only the folder name differs.
                ps=json.loads((s/'render-plan.json').read_text('utf-8'));pm=json.loads((m/'render-plan.json').read_text('utf-8'))
                self.assertEqual(ps,pm)
                rs=json.loads((s/'receipts.json').read_text('utf-8'));rm=json.loads((m/'receipts.json').read_text('utf-8'))
                self.assertEqual(rs['plan_sha256'],rm['plan_sha256'])
                self.assertEqual({k:(v['key'],v['audio_sha256'],v['synthesis_sha256']) for k,v in rs['takes'].items()},
                                 {k:(v['key'],v['audio_sha256'],v['synthesis_sha256']) for k,v in rm['takes'].items()})

    def test_render_candidates_batches_and_honours_preserved_markers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);takes=[]
            for n in range(3):
                take=root/f'take-{n}';take.mkdir();write_json(take/'render-plan.json',{'units':[]});takes.append(take)
            write_json(takes[1]/'candidate-render-failure.json',{'plan_sha256':file_digest(takes[1]/'render-plan.json')})
            calls=[]
            recovery.render_candidates([(t/'assignment.json',t,'c1') for t in takes],lambda jobs,execution_guard=None:calls.append(jobs),None)
            self.assertEqual(len(calls),1);self.assertEqual([j[1] for j in calls[0]],[takes[0],takes[2]]);self.assertEqual(calls[0][0][2],['c1'])
            write_json(takes[2]/'candidate-render-timeout.json',{'plan_sha256':'other'})
            with self.assertRaises(ValueError):
                recovery.render_candidates([(t/'assignment.json',t,'c1') for t in takes],lambda *a,**k:None,None)
            def fail(jobs,execution_guard=None):raise CandidateRenderFailure('x')
            write_json(takes[2]/'candidate-render-timeout.json',{'plan_sha256':file_digest(takes[2]/'render-plan.json')})
            recovery.render_candidates([(t/'assignment.json',t,'c1') for t in takes],fail,None)  # swallowed like render_candidate

    def test_futility_rule_skips_identical_transcripts_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)
            def check(heard,status='spoken_text_mismatch'):return {'status':status,'heard':heard}
            heard={'same':['Хм, Ёж!','хм еж','ХМ... ёж','хм еж'],'varied':['один','два','три','один'],'few':['тот','тот'],
                   'mixed':['тот','тот','тот','verified']}
            for n in range(4):
                take=out/f'take-{n}';take.mkdir()
                write_json(take/'word-checks-1.7B.json',{'checks':{uid:check(h[n],'asr_exact_match' if h[n]=='verified' else 'spoken_text_mismatch')
                                                           for uid,h in heard.items() if n<len(h)}})
            skipped=recovery.futile_units(out,set(heard))
            self.assertEqual(sorted(skipped),['mixed','same'])  # 'mixed': three identical flagged takes; the verified one is not a flag
            self.assertEqual(skipped['same'],['хм еж']*4)
            self.assertEqual(recovery.futile_units(out,{'varied','few'}),{})
class AbortDrainTests(unittest.TestCase):
    def test_requests_relaunched_after_an_abort_render_before_the_failure_is_reported(self):
        # Full run 2026-10-17: a runaway hiss aborted the session; its lone retry failed and the
        # 67 relaunched first takes were never rendered.
        class Aborting(Native):
            def __call__(self,command,**kwargs):
                sequence=json.loads(Path(command[command.index('--request-sequence')+1]).read_text('utf-8'))['requests']
                staging=Path(command[command.index('--out-dir')+1]);self.launches.append([r['id'] for r in sequence])
                for r in sequence:
                    if r['id']=='u2':break
                    sf.write(staging/(r['id']+'.wav'),np.full(24000,.01*r['seed']),24000,subtype='PCM_16')
                class Process:
                    returncode=1
                    def poll(self):return 1
                return Process()
        def retry(command,**kwargs):raise renderer.subprocess.CalledProcessError(1,command)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);folder=root/'take-0';folder.mkdir();path=folder/'assignment.json'
            path.write_text(json.dumps({'units':[unit('u1',3),unit('u2',4),unit('u3',5)]}),encoding='utf-8')
            native=Aborting()
            with patch.object(renderer,'make_packs',return_value=({'voice':PACK},{})),                 patch.object(renderer,'spoken_text',side_effect=lambda text,*a:text),                 patch.object(renderer,'synthesis_plan',side_effect=lambda pack,voice,text,*a:synthesis(text)),                 patch.object(renderer,'STAGING',root/'stg'),patch.object(renderer.subprocess,'Popen',native),                 patch.object(renderer.subprocess,'run',side_effect=retry):
                with self.assertRaises(CandidateRenderFailure):renderer.render(path,folder,['c1'])
            takes=json.loads((folder/'receipts.json').read_text('utf-8'))['takes']
            self.assertEqual(sorted(takes),['u1','u3'])
            self.assertEqual(native.launches,[['u1','u2','u3'],['u3']])


if __name__=='__main__':unittest.main()


class UnmarkedVariantTests(unittest.TestCase):
    def render_text(self,unit_extra,auto):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);folder=root/'t';folder.mkdir();path=folder/'assignment.json'
            u={**unit('u1',3,'Я тебе позавидую, Гундахар.'),**unit_extra}
            path.write_text(json.dumps({'units':[u],'pronunciation':{'policy':'stress-rare-v2','names':{},'common':[]}}),encoding='utf-8')
            with patch.object(renderer,'make_packs',return_value=({'voice':PACK},{})),\
                 patch.object(renderer,'spoken_text',side_effect=lambda text,*a:text),\
                 patch.object(renderer,'synthesis_plan',side_effect=lambda pack,voice,text,*a:synthesis(text)),\
                 patch('rimsky.auto_pronunciation.auto_pronunciation',side_effect=lambda t,*a,**k:auto),\
                 patch('rimsky.auto_pronunciation.policy_options',return_value={}),\
                 patch.object(renderer,'STAGING',root/'stg'),patch.object(renderer.subprocess,'Popen',Native()):
                renderer.render(path,folder,['c1'])
            return json.loads((folder/'render-plan.json').read_text('utf-8'))['units'][0]['synthesis']

    def test_ordinary_marks_removed_name_marks_kept_and_unflagged_units_untouched(self):
        marked='Я тебе́ позави́дую, Гунда́хар.';auto=(marked,{'name_edits':[{'word':'Гундахар','marked':'Гунда́хар'}]})
        plain=self.render_text({},auto)
        self.assertEqual(plain['text'],marked);self.assertNotIn('stress_marks_suppressed',plain)
        varied=self.render_text({'stress_marks':'suppress-automatic-v1'},auto)
        self.assertEqual(varied['text'],'Я тебе позавидую, Гунда́хар.')
        self.assertEqual(varied['stress_marks_suppressed']['marked_text'],marked)
