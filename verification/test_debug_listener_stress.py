"""Human correction generation contracts; no providers, native models or ASR."""
import copy,json,sys,tempfile,unittest,hashlib
from pathlib import Path
from unittest.mock import patch,Mock
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'runtime')]
import repair_debug_listener_stress as w
from rimsky.util import digest,file_digest


class ListenerRepair(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
    def fixture(self,text='Дом стоит.',marked='Дом сто́ит.'):
        s={'backend':'higgs_audio_cpp','text':marked,'prompt':marked,'sampling':{'seed':19},'max_tokens':512,
           'reference':{'audio':'ref.wav','text':'Reference.'},'chunk_mode':'tag_aware','chunk_size':65536,
           'effects_policy':'disabled'}
        u={'id':'u','source_id':'p','chapter_id':'c','start':0,'end':len(text),'text':text,'synthesis':s,'retake_number':0,'key':digest(s)}
        f={'policy':w.listener_stress.POLICY,'names':{},'occurrences':[{'source_id':'p','passage_sha256':digest(text),
            'text_offset':4,'selected_text':'стоит','intended_stress':'стои́т','report_id':'r'}]}
        f['sha256']=digest(f)
        return u,f,{'p':text}
    def test_final_human_overlay_changes_only_native_stress_and_binds_source(self):
        u,f,b=self.fixture();before=copy.deepcopy(u);new,e=w.corrected_unit(u,f,b)
        self.assertEqual(u,before);self.assertEqual(new['text'],u['text'])
        self.assertEqual(new['synthesis']['text'],'Дом стои́т.')
        self.assertEqual(new['synthesis']['reference'],u['synthesis']['reference'])
        self.assertEqual(new['synthesis']['sampling'],u['synthesis']['sampling'])
        self.assertEqual(e['source_range'],[0,len(u['text'])]);self.assertNotEqual(new['key'],u['key'])
        self.assertEqual(new['synthesis']['russian_phonetics']['prephonetic_source'],'Дом стои́т.')
    def restoration_fixture(self,source='Дом стоит.',selected='Дом сто́ит.'):
        from rimsky import contextual_stress as rules
        rows=rules.analyze(source,selected)
        choices={'decisions':[{'id':'c0000','word':'стоит','stressed':'стои́т','basis':'Standing.'}]}
        block={'rule':rules.RULE,'dictionary_sha256':rules.DICTIONARY_SHA256,'answer_sha256':'answer',
            'decisions':rules.decisions_from(rows,choices)}
        after,proof=rules.apply(source,selected,block)
        unit,frozen,blocks=self.fixture(text=source,marked=after)
        unit['synthesis'].update(automatic_pronunciation={},contextual_stress={**proof,'module_sha256':file_digest(Path(rules.__file__))})
        item={'source':source,'selected':selected,'chapter':'c','names':{},'common':[],
            'upstream_evidence':{'automatic_pronunciation':{}}}
        witness={'answer':block,'inputs':[item],'answer_sha256':'answer','inputs_sha256':'inputs',
            'module_sha256':file_digest(Path(rules.__file__))}
        return unit,frozen,blocks,witness
    def test_archived_full_context_replay_restores_baseline_and_human_wins_last(self):
        u,f,b,witness=self.restoration_fixture();baseline,proof=w.restored_baseline(u,witness)
        self.assertEqual(baseline,'Дом сто́ит.');self.assertEqual(proof['input_sha256'],digest(witness['inputs'][0]))
        # Latest explicit human intent remains authoritative over the baseline.
        new,e=w.corrected_unit(u,f,b,True,{'id':'r','audio_hash':'heard'},baseline,proof)
        self.assertEqual(new['synthesis']['text'],'Дом стои́т.')
        self.assertNotIn('contextual_stress',new['synthesis'])
        self.assertEqual(new['synthesis']['stress_baseline_restoration'],proof)
        self.assertEqual(new['synthesis']['reference'],u['synthesis']['reference'])
        self.assertEqual(new['synthesis']['effects_policy'],'disabled')
    def test_context_rollback_rejects_changed_full_text_evidence_or_ambiguous_witness(self):
        u,_,_,witness=self.restoration_fixture()
        for mutate in ('text','automatic','duplicate','answer'):
            bad=copy.deepcopy(witness)
            if mutate=='text':bad['inputs'][0]['selected']='Друг стоит.'
            elif mutate=='automatic':bad['inputs'][0]['upstream_evidence']['automatic_pronunciation']={'changed':True}
            elif mutate=='duplicate':bad['inputs'].append(copy.deepcopy(bad['inputs'][0]))
            else:bad['answer_sha256']='other'
            with self.subTest(mutate=mutate),self.assertRaises(ValueError):w.restored_baseline(u,bad)
    def test_full_input_restoration_also_undoes_context_close_mark_guard(self):
        source='Здесь стоит вышеписанный человек.';selected='Здесь стоит вышепи́санный человек.'
        u,_,_,witness=self.restoration_fixture(source,selected)
        self.assertEqual(u['synthesis']['contextual_stress']['close_mark_guard']['dropped'][0]['dropped'],'вышепи́санный')
        baseline,proof=w.restored_baseline(u,witness)
        self.assertEqual(baseline,selected);self.assertEqual(proof['restored_text_sha256'],digest(selected))
    def test_original_global_deduplication_may_reuse_first_chapter_witness(self):
        u,_,_,witness=self.restoration_fixture();witness['inputs'][0]['chapter']='first-chapter'
        baseline,proof=w.restored_baseline(u,witness)
        self.assertEqual(baseline,'Дом сто́ит.');self.assertEqual(proof['source_unit'],u['id'])
    def test_restored_unchanged_audio_remains_byte_identical_cache(self):
        u,f,b,witness=self.restoration_fixture();baseline,proof=w.restored_baseline(u,witness)
        new,e=w.corrected_unit(u,f,b,baseline=baseline,restoration=proof)
        self.assertEqual(new,u);self.assertIsNone(e)
    def test_already_correct_input_keeps_exact_cache_until_listener_requests_new_take(self):
        u,f,b=self.fixture(marked='Дом стои́т.')
        self.assertEqual(w.corrected_unit(u,f,b),(u,None))
        new,e=w.corrected_unit(u,f,b,True,{'id':'r','audio_hash':'heard'})
        self.assertEqual(new['synthesis']['text'],u['synthesis']['text'])
        self.assertEqual(new['synthesis']['sampling']['seed'],20)
        self.assertEqual(e['reported_audio']['report_id'],'r')
    def test_occurrence_does_not_spread_to_changed_source(self):
        u,f,b=self.fixture();f['occurrences'][0]['passage_sha256']='0'*64;f.pop('sha256');f['sha256']=digest(f)
        self.assertEqual(w.corrected_unit(u,f,b),(u,None))
    def test_full_note_audit_keeps_unstructured_and_superseded_notes(self):
        u,f,b=self.fixture();notes=[{'report':{'id':'r'},'occurrence':f['occurrences'][0]},
            {'report':{'id':'raw','kind':'pronunciation','note':'saved text'}},
            {'report':{'id':'old'},'occurrence':dict(f['occurrences'][0],report_id='old')}]
        rows=w.note_coverage(notes,[u],b,{'u'},f)
        self.assertEqual([r['status'] for r in rows],['applied','unbound','unbound'])
        self.assertIn('preserved',rows[1]['reason']);self.assertIn('Superseded',rows[2]['reason'])
    def test_exact_source_ranges_required_before_freezing_assignment(self):
        u,_,b=self.fixture();a={'units':[u]}
        self.assertIn('listener_stress_snapshot',w.d.listener_assignment(a,b,{}))
        with self.assertRaises(ValueError):w.d.listener_assignment(a,{'p':'Other sentence.'},{})
    def test_technical_delta_preserves_all_other_native_fields(self):
        u,_,_=self.fixture();new=copy.deepcopy(u)
        new['synthesis']['max_tokens']=2048;new['synthesis']['listener_technical_recovery']={'policy':w.POLICY,'previous_session':'failed'}
        new['key']=digest(new['synthesis']);w.validate_effective(u,new)
        for field,value in [('text','Lost source.'),('prompt','Lost prompt.'),('reference',{'audio':'other','text':'Other'})]:
            bad=copy.deepcopy(new);bad['synthesis'][field]=value;bad['key']=digest(bad['synthesis'])
            with self.assertRaises(ValueError):w.validate_effective(u,bad)
        bad=copy.deepcopy(new);bad['synthesis']['sampling']['seed']+=2;bad['key']=digest(bad['synthesis'])
        with self.assertRaises(ValueError):w.validate_effective(u,bad)
    def test_existing_terminal_receipt_never_launches_a_second_physical_attempt(self):
        u,_,_=self.fixture();row={'status':'technical_failure','unit':'u','synthesis_sha256':digest(u['synthesis']),'effective_unit':u}
        w.write(self.root/'units/u.json',row)
        with patch.object(w,'native_attempt')as launch:
            self.assertEqual(w.render_unit(self.root,{},u),(row,u));launch.assert_not_called()
    def test_stop_propagates_without_launching_next_attempt(self):
        u,_,_=self.fixture()
        with patch.object(w,'native_attempt',side_effect=KeyboardInterrupt)as launch:
            with self.assertRaises(KeyboardInterrupt):w.render_unit(self.root,{},u)
            self.assertEqual(launch.call_count,1)
        self.assertFalse((self.root/'units/u.json').exists())
    def test_music_or_missing_coverage_cannot_be_declared_complete(self):
        ready={'chapters':['c'],'unit_count':1,'chapter_counts':{'c':1}}
        sync={'status':'ready','segments':1}
        state={'status':'complete','chapters':{'c':{'complete':True,'units':1,'music':[{'kind':'scene_bed'},{'kind':'ending'}]}}}
        w.write(self.root/'progress.json',state);w.complete_publication(self.root,ready,sync)
        state['chapters']['c']['music_pending']='held';w.write(self.root/'progress.json',state)
        with self.assertRaises(ValueError):w.complete_publication(self.root,ready,sync)
        with self.assertRaises(ValueError):w.complete_publication(self.root,ready,{**sync,'segments':0})
    def test_pending_dispatch_holds_new_human_correction_before_model_or_native(self):
        u,f,b=self.fixture();output=self.root/'generation'
        w.write(output.parent/'source/book.json',{'id':'book','chapters':[{'blocks':[{'id':'p','text':b['p']}]}]})
        with patch.object(w.d,'listener_snapshot',return_value=f),patch.object(w.d.basic.assets,'wait_for_audio_resources')as wait:
            with self.assertRaisesRegex(ValueError,'preparation refresh'):
                w.d.native_batch(output,{'listener_stress_snapshot_sha256':'old'},[u],60)
            wait.assert_not_called()
        self.assertEqual(w.read(output/'listener-preparation-refresh-required.json')['status'],'held')
    def test_fresh_preparation_applies_human_after_auto_before_phonetics(self):
        u,f,b=self.fixture(text='Дом стоит.',marked='Дом сто́ит.')
        u.update(mode='narrator',voice='narrator_baseline',speaker='narrator',mood='neutral',spoken_number_policy='raw-numbers-v1')
        assignment=w.d.listener_assignment({'units':[u],'pronunciation':{'policy':'stress-rare-v4','names':{},'common':[]}},b,f)
        path=self.root/'assignment.json';w.write(path,assignment)
        pack={'files':{'model':'hash'},'speech':{'model':'model'},'voices':[{}]}
        def synthesis(pack,voice,text,*args):
            return dict(u['synthesis'],text=text,prompt=text)
        auto={'policy':'stress-rare-v4','yo_restoration':{'unbound':[]},'homograph_overrides':{'unbound':[]}}
        with patch.object(w.trial,'make_packs',return_value=({'narrator_baseline':pack},{})),\
             patch.object(w.trial,'profile_pack',return_value=pack),patch('rimsky.recovery_decoding.execution_pack',return_value=pack),\
             patch('rimsky.auto_pronunciation.auto_pronunciation',return_value=('Дом сто́ит.',auto)),\
             patch('rimsky.auto_pronunciation.policy_options',return_value={}),\
             patch.object(w.trial,'synthesis_plan',side_effect=synthesis),patch.object(w.trial,'validate_voice_continuity'):
            result=w.trial.prepare(path,self.root/'plan',['c'])
        plan=w.read(result['path'])['units'][0]['synthesis']
        self.assertEqual(plan['text'],'Дом стои́т.')
        self.assertEqual(plan['russian_phonetics']['prephonetic_source'],'Дом стои́т.')
        self.assertEqual(plan['listener_stress']['edits'][0]['report_id'],'r')



class Coalescing(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.base=Path(self.tmp.name)
 def fixtures(self):
  units=[];blocks={};notes=[];segments=[];items=[]
  for n in (1,2):
   sid='p'+str(n);uid='u'+str(n);text='Дом стоит.';audio='heard'+str(n)
   blocks[sid]=text;units.append({'id':uid,'source_id':sid,'start':0,'end':len(text),'text':text,'synthesis':{'backend':'higgs_audio_cpp'}})
   r={'id':'r'+str(n),'source_id':sid,'text_offset':4,'selected_text':'стоит','intended_stress':'стои́т','audio_hash':audio,'generation_id':'g','kind':'stress','created':'2026-10-24T00:00:00Z','passage_text':text}
   notes.append({'report':r});segments.append({'id':uid,'source_id':sid,'audio_hash':audio})
   item={'id':r['id'],'generation_id':'g','status':'queued','origin_report':r,'origin_report_sha256':w.digest(r),'report':dict(r,segment_id=uid)}
   w.write(self.base/'listener-stress-queue'/(r['id']+'.json'),item);items.append(item)
  return units,blocks,notes,{'id':'g','segments':segments},items
 def test_coalesces_and_forces_every_current_heard_report(self):
  u,b,n,g,i=self.fixtures();origins,forced,queued=w.pending_reports(self.base,i[0],n,g,u,b)
  self.assertEqual([x['id']for x in origins],['r1','r2']);self.assertEqual(set(forced),{'u1','u2'});self.assertEqual(len(queued),2)
  changed=copy.deepcopy(i[1]);changed.update(status='running',updated='later');w.write(self.base/'listener-stress-queue/r2.json',changed)
  self.assertEqual(w.pending_reports(self.base,i[0],n,g,u,b),(origins,forced,queued))
 def test_historical_audio_that_changed_does_not_force_unchanged_native(self):
  u,b,n,g,i=self.fixtures();g['segments'][1]['audio_hash']='newer_take'
  _,forced,_=w.pending_reports(self.base,i[0],n,g,u,b);self.assertEqual(set(forced),{'u1'})
 def test_feedback_arriving_after_snapshot_stays_queued_for_next_job(self):
  u,b,n,g,items=self.fixtures();path=self.base/'listener-stress-queue/r2.json';before=path.read_bytes()
  captured,ids=w.frozen_notes({'note_statuses':[{'report_id':'r1'}]},n)
  origins,forced,queued=w.pending_reports(self.base,items[0],captured,g,u,b,ids)
  self.assertEqual([r['id']for r in origins],['r1']);self.assertEqual(set(forced),{'u1'})
  self.assertEqual([r['origin_report_id']for r in queued],['r1']);self.assertEqual(path.read_bytes(),before)
 def test_primary_feedback_newer_than_snapshot_requires_fresh_capture(self):
  u,b,n,g,items=self.fixtures()
  with self.assertRaisesRegex(ValueError,'after the frozen snapshot'):
   w.pending_reports(self.base,items[1],n[:1],g,u,b,{'r1'})
 def test_frozen_note_set_requires_all_exactly_once(self):
  _,_,n,_,_=self.fixtures();f={'note_statuses':[{'report_id':'r1'},{'report_id':'r2'}]}
  for rows in (n[:1],n+[n[0]]):
   with self.assertRaisesRegex(ValueError,'missing or duplicated'):w.frozen_notes(f,rows)
 def test_old_folder_primary_coalesces_siblings_when_rebound_to_latest_base(self):
  u,b,n,g,i=self.fixtures();latest=self.base/'latest';primary=dict(i[0],_queue_item_path=str(self.base/'listener-stress-queue/r1.json'))
  g['id']='latest-generation';origins,forced,queued=w.pending_reports(latest,primary,n,g,u,b)
  self.assertEqual([x['id']for x in origins],['r1','r2']);self.assertEqual(set(forced),{'u1','u2'});self.assertEqual(len(queued),2)
 def test_rejects_fabricated_canonical_intent_and_wrong_source_offset(self):
  u,b,n,g,i=self.fixtures();bad=copy.deepcopy(i[0]);bad['origin_report']['intended_stress']='сто́ит';bad['origin_report_sha256']=w.digest(bad['origin_report'])
  with self.assertRaisesRegex(ValueError,'canonical'):w.pending_reports(self.base,bad,n,g,u,b)
  bad=copy.deepcopy(i[0]);bad['origin_report']['text_offset']=0;bad['origin_report_sha256']=w.digest(bad['origin_report']);n[0]['report']=bad['origin_report']
  with self.assertRaisesRegex(ValueError,'source passage'):w.pending_reports(self.base,bad,n,g,u,b)
 def test_exact_local_source_archive_precedes_live_or_git_sources(self):
  ready={}
  for p,k in [('scripts/debug_higgs_one_shot.py','implementation_sha256'),('scripts/render_public_demo_one_shot.py','generic_primitives_sha256'),('scripts/render_higgs_book_trial.py','trial_preparation_sha256'),('scripts/build_contextual_stress.py','context_builder_sha256')]:
   target=self.base/'effective-implementation'/p;target.parent.mkdir(parents=True,exist_ok=True);raw=('historical '+p).encode();target.write_bytes(raw);ready[k]=hashlib.sha256(raw).hexdigest()
  with patch.object(w.subprocess,'check_output')as git:
   archived=w.archived_preparation(ready,self.base);self.assertEqual(len(archived),4);git.assert_not_called()
 def test_existing_native_receipt_requires_exact_physical_request_proof(self):
  initial={'id':'u','synthesis':{'max_tokens':500,'sampling':{'seed':1}},'key':'ignored'};effective=copy.deepcopy(initial);effective['synthesis'].update(max_tokens=2048,listener_technical_recovery={'policy':'development-private-listener-stress-repair-v1','previous_session':'proof'});effective['key']=w.digest(effective['synthesis'])
  w.validate_effective(initial,effective)
  effective['synthesis']['sampling']['seed']=99;effective['key']=w.digest(effective['synthesis'])
  with self.assertRaises(ValueError):w.validate_effective(initial,effective)
 def test_renamed_display_keeps_immutable_external_generation_identity(self):
  u,b,n,g,items=self.fixtures();base=self.base/'source-generation';data=self.base/'data'
  source={'id':'book','chapters':[{'blocks':[{'id':sid,'text':text}for sid,text in b.items()]}]}
  w.write(base.parent/'source/book.json',source);w.write(base/'manifest.json',{'source_sha256':w.file_digest(base.parent/'source/book.json')});w.write(base/'ready.json',{})
  for segment in g['segments']:
   raw=segment['id'].encode();sha=hashlib.sha256(raw).hexdigest();path=data/'audio'/(sha+'.wav');path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw);segment['audio_hash']=sha
  n[0]['report']['audio_hash']=g['segments'][0]['audio_hash'];items[0]['origin_report']=n[0]['report'];items[0]['origin_report_sha256']=w.digest(n[0]['report']);items[0]['report']['audio_hash']=g['segments'][0]['audio_hash']
  g.update(name='My renamed audiobook',book_id='book',analysis={'external_preview':{'generation':base.name}})
  store=Mock();store.generation.return_value=g;store.book.return_value={'id':'book'}
  frozen={'sha256':'snapshot','note_statuses':[{'report_id':row['report']['id']}for row in n]}
  with patch.object(w,'Store',return_value=store),patch.object(w.listener_stress,'snapshot',return_value=frozen),patch.object(w.listener_stress,'notes',return_value=n),patch.object(w,'load_units',return_value=(u,b)),patch.object(w,'archived_preparation',return_value={}),patch.object(w,'gateway_configuration',return_value=None),patch.object(w,'corrected_unit',side_effect=lambda unit,*a:(unit,None)),patch.object(w,'restored_baseline',return_value=(None,None)):
   self.assertEqual(w.configure(base,data,'g',items[0])[1]['source_generation_id'],'g')
   g['analysis']['external_preview']['generation']='different-folder'
   with self.assertRaisesRegex(ValueError,'identity'):w.configure(base,data,'g',items[0])
 def test_verified_delivery_consumes_all_coalesced_siblings_not_unrelated_queue(self):
  u,b,n,g,items=self.fixtures();_,_,queued=w.pending_reports(self.base,items[0],n,g,u,b)
  output=self.base/'derived';data=self.base/'data';g.update(status='ready',book_id='book')
  for unit,segment in zip(u,g['segments']):
   raw=unit['id'].encode();sha=hashlib.sha256(raw).hexdigest();asset=output/'assets'/(sha+'.wav');asset.parent.mkdir(parents=True,exist_ok=True);asset.write_bytes(raw)
   target=data/'audio'/asset.name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw);segment.update(audio_hash=sha,start=0,end=10,status='ready')
   w.write(output/'units'/(unit['id']+'.json'),{'status':'rendered','audio':'assets/'+asset.name,'audio_sha256':sha})
  w.write(output/'listener-note-coverage.json',{'notes':[{'report_id':'r1','status':'applied','reason':'Exact source occurrence','current_units':['u1']},{'report_id':'r2','status':'nochange','reason':'Exact source occurrence','current_units':['u2']}]})
  result={'generation_id':'g','status':'applied','affected_ids':['u1'],'affected_source_ids':['p1']};w.write(output/'listener-repair-terminal.json',result)
  w.write(self.base/'listener-stress-queue/unrelated.json',{'id':'unrelated','status':'queued'})
  store=Mock();store.generation.return_value=g;store.book.return_value={'id':'book'}
  with patch.object(w,'Store',return_value=store),patch.object(w.listener_stress,'notes',return_value=n):
   w.record_note_outcomes(output,{'unit_count':2,'coalesced_queue_items':queued},result,data)
  self.assertTrue(all(w.read(Path(q['path']))['status']=='applied'for q in queued))
  self.assertEqual(w.read(self.base/'listener-stress-queue/unrelated.json')['status'],'queued')
  self.assertEqual(w.read(output/'listener-note-outcomes.json')['count'],2)
 def test_gateway_never_takes_over_an_unrelated_managed_view(self):
  helper=self.base/'gateway.py';helper.write_text("def generation_id(name): return 'target'\ndef _service(name): return {'args':['--generation-id','other','--book-id','book']}\ndef readiness(*a,**k): raise RuntimeError('must not inspect unrelated audio')\ndef apply(*a): raise RuntimeError('must not stop unrelated service')\n")
  data=self.base/'data';config={'source_generation_id':'source','book_id':'book','gateway_binding':{'helper':str(helper),'sha256':w.file_digest(helper),'config':str(self.base/'config'),'data_dir':str(data.resolve())}}
  result=w.handoff_gateway(self.base/'out',config,{'chapters':['c']},data,final=True)
  self.assertEqual(result['status'],'not_targeted');self.assertFalse((self.base/'out/gateway-repin.json').exists())
 def test_gateway_handoff_requires_library_binding_and_verified_current_source(self):
  helper=self.base/'gateway.py';helper.write_text("def generation_id(name): return 'target'\ndef _service(name): return {'args':['--generation-id','source','--book-id','book']}\ndef readiness(*a,**k): return {'verified':True}\ndef apply(name): return {'repinned':True}\n")
  data=self.base/'data';config={'source_generation_id':'source','book_id':'book','gateway_binding':{'helper':str(helper),'sha256':w.file_digest(helper),'config':str(self.base/'config'),'data_dir':str(data.resolve())}}
  result=w.handoff_gateway(self.base/'out',config,{'chapters':['c']},data,final=True)
  self.assertEqual(result['status'],'applied');self.assertTrue(w.read(self.base/'out/gateway-repin.json')['repinned'])
  with self.assertRaisesRegex(ValueError,'library binding'):w.handoff_gateway(self.base/'out',config,{'chapters':['c']},self.base/'different')
 def test_unbound_pending_intent_cannot_be_declared_delivered(self):
  w.write(self.base/'listener-note-coverage.json',{'notes':[{'report_id':'r','status':'unbound','reason':'Unknown source'}]})
  with self.assertRaisesRegex(ValueError,'unbound'):w.require_pending_coverage(self.base,{'pending_origins':[{'id':'r'}]})
  w.write(self.base/'listener-note-coverage.json',{'notes':[{'report_id':'r','status':'unbound','reason':'Superseded by newer explicit intent'}]})
  w.require_pending_coverage(self.base,{'pending_origins':[{'id':'r'}]})

class DeferredGuard(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.out=Path(self.tmp.name)/'generation'
  self.book={'id':'book'};self.unit={'id':'u'};self.origin={'id':'r','source_id':'p','text_offset':4,'selected_text':'стоит','intended_stress':'стои́т','audio_hash':'heard','generation_id':'old','kind':'stress','created':'2026-10-24T00:00:00Z','passage_text':'Дом стоит.'}
  self.item={'status':'queued','phase':'awaiting_render_completion','generation_id':w.d.digest(('book:'+self.out.name+':higgs_audio_tts_q8_external_preview').encode())[:24],'pending_segment_ids':['u'],'origin_report':self.origin,'origin_report_sha256':w.d.digest(self.origin)}
  self.evidence={'edits':[{'start':4,'end':9}],'applications':[{'start':4,'end':9,'report_id':'r'}]}
  self.path=self.out/'listener-stress-queue/r.json';w.d.write(self.path,self.item)
 def check(self):
  with patch.object(w.d,'listener_notes',return_value=[{'report':self.origin}]):return w.d.queued_listener_refresh(self.out,self.book,self.unit,self.evidence)
 def test_valid_durable_canonical_intent_allows_its_own_pending_unit(self):self.assertTrue(self.check())
 def test_wrong_origin_hash_cannot_bypass_hold(self):
  self.item['origin_report_sha256']='bad';w.d.write(self.path,self.item);self.assertFalse(self.check())
 def test_noncanonical_intent_cannot_bypass_hold(self):
  bad=copy.deepcopy(self.item);bad['origin_report']['intended_stress']='сто́ит';bad['origin_report_sha256']=w.d.digest(bad['origin_report']);w.d.write(self.path,bad);self.assertFalse(self.check())
 def test_other_pending_unit_or_generation_cannot_bypass_hold(self):
  self.item['pending_segment_ids']=['other'];w.d.write(self.path,self.item);self.assertFalse(self.check())
  self.item['pending_segment_ids']=['u'];self.item['generation_id']='different';w.d.write(self.path,self.item);self.assertFalse(self.check())
 def test_every_changed_word_requires_its_own_queued_canonical_witness(self):
  self.evidence['edits'].append({'start':20,'end':25});self.evidence['applications'].append({'start':20,'end':25,'report_id':'not-queued'});self.assertFalse(self.check())
 def test_unqueued_or_completed_intent_cannot_bypass_hold(self):
  self.item['status']='applied';w.d.write(self.path,self.item);self.assertFalse(self.check())

if __name__=='__main__':unittest.main()
