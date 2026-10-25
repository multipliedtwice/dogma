"""Strict immutable native ancestry fixtures; no models, providers or real profiles."""
import copy,json,shutil,tempfile,unittest,sys
from pathlib import Path
from unittest.mock import patch
ROOT=next(p for p in Path(__file__).resolve().parents if (p/'scripts/repair_debug_listener_stress.py').is_file())
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'runtime')]
import repair_debug_listener_stress as m

class EvidenceInheritance(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.request=patch.object(m.trial,'sequence_request',side_effect=lambda uid,s:{'id':uid,'text':s['text']});self.request.start();self.addCleanup(self.request.stop)
        self.stopped=patch.object(m,'stopped',return_value=True);self.stopped.start();self.addCleanup(self.stopped.stop)
        self.model=patch.object(m,'evidence_model_paths',return_value=('model','runner'));self.model.start();self.addCleanup(self.model.stop)
    def fixture(self):
        parent=self.root/'ancestor';child=self.root/'child'
        unit={'id':'u','source_id':'p','text':'Source.','start':0,'end':7,'chapter_id':'c','retake_number':0,
            'synthesis':{'text':'Source.','sampling':{'seed':1},'max_tokens':500}}
        unit['key']=m.digest(unit['synthesis']);pin={'runner':{'path':'runner'},'speech_model':{'sha256':'pinned'}}
        manifest={'source_sha256':'source'};m.write(parent/'manifest.json',manifest)
        ready={'manifest_sha256':m.file_digest(parent/'manifest.json'),'native_manifest':pin,'listener_stress_derivation':{'helper_sha256':'helper'}}
        m.write(parent/'ready.json',ready);request=m.trial.sequence_request('u',unit['synthesis'])
        sid=m.digest({'request':request,'unit':unit,'ready_sha256':m.file_digest(parent/'ready.json'),'attempt':1,'helper_sha256':'helper'})
        native=parent/'native'/sid;native.mkdir(parents=True);(native/'u.wav').write_bytes(b'immutable audio')
        row={'unit':'u','session':sid,'effective_unit':unit,'status':'rendered','request_sha256':m.digest(request),
            'audio_sha256':m.file_digest(native/'u.wav'),'synthesis_sha256':m.digest(unit['synthesis'])}
        m.write(parent/'units/u.json',row);m.write(native/'unit-result.json',row)
        m.write(native/'physical-start.json',{'id':sid,'unit':unit,'request_sha256':m.digest(request),'attempt':1,'helper_sha256':'helper'})
        m.write(native/'physical-result.json',{'returncode':0});m.write(native/'requests.json',{'requests':[request]})
        m.write(native/'worker.json',{'pid':999999,'create_time':1,'command':['runner','--task','tts','--family','higgs_audio_tts','--model','model',
            '--backend','cuda','--device','0','--threads','4','--request-sequence',str(native/'requests.json'),'--out-dir',str(native),'--out-format','pcm16','--metrics']})
        m.write(child/'manifest.json',manifest);m.write(child/'units/u.json',row)
        c=copy.deepcopy(ready);c['listener_stress_derivation']={'helper_sha256':'next','base':str(parent),'base_ready_sha256':m.file_digest(parent/'ready.json')}
        m.write(child/'ready.json',c);m.write(child/'repair.json',{**c['listener_stress_derivation'],
            'derived_ready_sha256':m.file_digest(child/'ready.json'),'base_inventory':m.inventory(parent)})
        return parent,child,c,unit,row
    def test_pinned_ancestor_resolves_without_mutating_any_bytes(self):
        parent,child,ready,unit,row=self.fixture();before=m.inventory(parent),m.inventory(child)
        proof=m.native_evidence(child,ready,unit,row)
        self.assertEqual(proof['copy_from'],str(parent));self.assertEqual(proof['session'],row['session'])
        self.assertEqual((m.inventory(parent),m.inventory(child)),before)
    def test_matching_sibling_is_not_searched(self):
        parent,child,ready,unit,row=self.fixture();shutil.move(parent/'native',self.root/'unrelated-native')
        with self.assertRaises(ValueError):m.native_evidence(child,ready,unit,row)
    def test_parent_ready_hash_change_is_rejected(self):
        parent,child,ready,unit,row=self.fixture();m.write(parent/'ready.json',{'changed':True})
        with self.assertRaises(ValueError):m.native_evidence(child,ready,unit,row)
    def test_mismatched_copied_receipt_is_rejected(self):
        parent,child,ready,unit,row=self.fixture();bad={**row,'audio_sha256':'changed'};m.write(child/'units/u.json',bad)
        with self.assertRaises(ValueError):m.native_evidence(child,ready,unit,bad)
    def test_corrupt_local_claim_does_not_fall_back(self):
        parent,child,ready,unit,row=self.fixture();(child/'native'/row['session']).mkdir(parents=True)
        with self.assertRaises(FileNotFoundError):m.native_evidence(child,ready,unit,row)
    def test_worker_model_request_and_returncode_are_bound(self):
        for changed in ('request','command','exit','source','pin','reservation'):
            with self.subTest(changed=changed):
                parent,child,ready,unit,row=self.fixture();native=parent/'native'/row['session']
                if changed=='request':m.write(native/'requests.json',{'requests':[{'changed':True}]})
                elif changed=='command':
                    w=m.read(native/'worker.json');w['command'][0]='other-runner';m.write(native/'worker.json',w)
                elif changed=='exit':m.write(native/'physical-result.json',{'returncode':1})
                elif changed=='source':
                    m.write(parent/'manifest.json',{'source_sha256':'other'})
                elif changed=='pin':ready['native_manifest']={'runner':{'path':'different'}}
                else:
                    w=m.read(native/'physical-start.json');w['attempt']=2;m.write(native/'physical-start.json',w)
                with self.assertRaises((ValueError,KeyError)):m.native_evidence(child,ready,unit,row)
                shutil.rmtree(parent);shutil.rmtree(child)
    def test_live_worker_is_rejected(self):
        parent,child,ready,unit,row=self.fixture()
        with patch.object(m,'stopped',return_value=False),self.assertRaisesRegex(ValueError,'still lives'):
            m.native_evidence(child,ready,unit,row)
    def test_copied_bundle_remains_self_contained_and_hash_checked(self):
        parent,child,ready,unit,row=self.fixture();proof=m.native_evidence(child,ready,unit,row)
        copied=self.root/'new';m.write(copied/'manifest.json',m.read(child/'manifest.json'));m.write(copied/'units/u.json',row)
        m.copy_native_evidence(copied,proof)
        newready=copy.deepcopy(ready);newready['listener_stress_derivation']['native_evidence_bindings']={'u':proof}
        m.write(copied/'ready.json',newready);m.write(copied/'repair.json',{**newready['listener_stress_derivation'],
            'derived_ready_sha256':m.file_digest(copied/'ready.json'),'base_inventory':m.inventory(parent)})
        shutil.rmtree(parent)
        self.assertEqual(m.native_evidence(copied,newready,unit,row)['origin'],str(parent))
        (copied/'native'/row['session']/'u.wav').write_bytes(b'corrupt')
        with self.assertRaises(ValueError):m.native_evidence(copied,newready,unit,row)
    def test_parent_inventory_and_derived_ready_are_required(self):
        for changed in ('inventory','derived_ready','extra_body'):
            with self.subTest(changed=changed):
                parent,child,ready,unit,row=self.fixture();repair=m.read(child/'repair.json')
                if changed=='inventory':repair['base_inventory']={}
                elif changed=='derived_ready':repair['derived_ready_sha256']='0'*64
                else:repair['unbound']='extra'
                m.write(child/'repair.json',repair)
                with self.assertRaises(ValueError):m.native_evidence(child,ready,unit,row)
                shutil.rmtree(parent);shutil.rmtree(child)
    def test_coherent_ancestor_audio_rewrite_is_held_by_recorded_inventory(self):
        parent,child,ready,unit,row=self.fixture();native=parent/'native'/row['session']
        (native/'u.wav').write_bytes(b'coherent replacement');row['audio_sha256']=m.file_digest(native/'u.wav')
        m.write(parent/'units/u.json',row);m.write(child/'units/u.json',row);m.write(native/'unit-result.json',row)
        with self.assertRaisesRegex(ValueError,'bytes changed'):m.native_evidence(child,ready,unit,row)
    def test_missing_declared_bundle_never_falls_back_even_when_parent_exists(self):
        parent,child,ready,unit,row=self.fixture();proof=m.native_evidence(child,ready,unit,row)
        ready['listener_stress_derivation']['native_evidence_bindings']={'u':proof}
        # A declaration without its local copy must hold, even if the old native directory is reachable.
        with self.assertRaisesRegex(ValueError,'missing locally'):m.native_evidence(child,ready,unit,row)
        shutil.rmtree(parent)
        with self.assertRaisesRegex(ValueError,'missing locally'):m.native_evidence(child,ready,unit,row)

if __name__=='__main__':unittest.main()
