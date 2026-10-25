"""Isolated all-mention human family regeneration; no native/provider calls."""
import copy,tempfile,unittest,sys
from pathlib import Path
ROOT=next(p for p in Path(__file__).resolve().parents if (p/'scripts/repair_debug_listener_stress.py').is_file())
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'runtime')]
import repair_debug_listener_stress as m


class FamilyForces(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.base=Path(self.tmp.name)
    def fixture(self):
        texts=['С Александром.','У Александра.','Александр пришёл.','Александровск.',
            'xАлександр Александр2 _Александр АлександрX.','александр.','С А́лександром.']
        blocks={};units=[];segments=[]
        for i,text in enumerate(texts,1):
            uid='u'+str(i);sid='p'+str(i);blocks[sid]=text
            native=text.replace('Александра','Алекса́ндра') if i==2 else text
            synthesis={'backend':'higgs_audio_cpp','text':native,'prompt':'Reference. '+native,
                'sampling':{'seed':19},'max_tokens':512,'reference':{'audio':'ref','text':'Reference.'},
                'chunk_mode':'tag_aware','chunk_size':65536,'effects_policy':'disabled'}
            units.append({'id':uid,'chapter_id':'c'+str(1+(i%2)),'source_id':sid,'start':0,'end':len(text),
                'text':text,'synthesis':synthesis,'key':m.digest(synthesis)})
            segments.append({'id':uid,'source_id':sid,'audio_hash':'heard'+str(i)})
        raw={'id':'latest','kind':'stress','book_id':'book','generation_id':'g','source_id':'p1',
            'text_offset':2,'selected_text':'Александром','intended_stress':'Алекса́ндром','audio_hash':'heard1',
            'passage_text':texts[0],'created':'2026-10-25T00:01:00Z'}
        old={**raw,'id':'older','created':'2026-10-25T00:00:00Z','intended_stress':'А́лександром'}
        notes=[{'report':old,'family':{'александр':'А́лександр'}},
            {'report':raw,'family':{'александр':'Алекса́ндр'}}]
        frozen={'policy':m.listener_stress.POLICY,'names':{'александр':'Алекса́ндр'},'occurrences':[],
            'family_evidence':{'александр':{'report_id':'latest'}},
            'note_statuses':[{'report_id':'older','status':'superseded'},{'report_id':'latest','status':'family'}]}
        frozen['sha256']=m.digest(frozen)
        items=[]
        for r in (old,raw):
            item={'id':r['id'],'generation_id':'g','status':'queued','origin_report':r,
                'origin_report_sha256':m.digest(r),'report':{**r,'segment_id':'u1'}}
            m.write(self.base/'listener-stress-queue'/(r['id']+'.json'),item);items.append(item)
        return units,blocks,notes,{'id':'g','segments':segments},items,frozen
    def force(self):
        u,b,n,g,items,f=self.fixture()
        origins,forced,queued=m.pending_reports(self.base,items[1],n,g,u,b,{'latest','older'},f)
        return u,b,n,g,items,f,origins,forced,queued
    def test_latest_active_family_forces_all_exact_inflections_across_chapters(self):
        u,b,n,g,items,f,origins,forced,queued=self.force()
        self.assertEqual(set(forced),{'u1','u2','u3','u7'})
        self.assertEqual(len(origins),2);self.assertEqual(len(queued),2)
        self.assertTrue(all(row['origin_report']['id']=='latest'for values in forced.values()for row in values))
        self.assertEqual(forced['u7'][0]['occurrences'][0]['source_text'],'А́лександром')
        self.assertEqual(forced['u7'][0]['occurrences'][0]['intended_stress'],'Алекса́ндром')
    def test_superseded_queued_family_report_does_not_force_even_exact_heard_unit(self):
        u,b,n,g,items,f=self.fixture();(self.base/'listener-stress-queue/latest.json').unlink()
        origins,forced,queued=m.pending_reports(self.base,items[0],n,g,u,b,{'latest','older'},f)
        self.assertEqual([r['id']for r in origins],['older']);self.assertEqual(forced,{})
    def test_same_correct_native_family_member_gets_new_take_without_fake_heard_claim(self):
        u,b,n,g,items,f,origins,forced,queued=self.force();old=copy.deepcopy(u[1])
        new,proof=m.corrected_unit(u[1],f,b,True,forced['u2'])
        self.assertEqual(u[1],old);self.assertEqual(new['synthesis']['text'],old['synthesis']['text'])
        self.assertEqual(new['synthesis']['sampling']['seed'],20)
        self.assertNotIn('reported_audio',proof)
        target=proof['family_regeneration']['targets'][0]
        self.assertFalse(target['heard_target']);self.assertEqual(target['target_source_id'],'p2')
        self.assertEqual(target['origin_report_sha256'],m.digest(items[1]['origin_report']))
        self.assertEqual(target['target_passage_sha256'],m.digest(b['p2']))
        self.assertEqual(new['synthesis']['reference'],old['synthesis']['reference'])
        self.assertEqual(new['synthesis']['effects_policy'],'disabled')
    def test_actually_heard_member_retains_both_true_heard_and_family_provenance(self):
        u,b,n,g,items,f,origins,forced,queued=self.force()
        new,proof=m.corrected_unit(u[0],f,b,True,forced['u1'])
        self.assertEqual(proof['reported_audio']['report_id'],'latest')
        self.assertEqual(proof['reported_audio']['audio_sha256'],'heard1')
        self.assertTrue(proof['family_regeneration']['targets'][0]['heard_target'])
        self.assertIn('Алекса́ндром',new['synthesis']['text'])
    def test_family_evidence_report_id_is_required_even_when_stress_spelling_matches(self):
        u,b,n,g,items,f=self.fixture();f['family_evidence']['александр']['report_id']='newer-unrelated-id'
        _,forced,_=m.pending_reports(self.base,items[1],n,g,u,b,{'latest','older'},f)
        self.assertEqual(forced,{})
    def test_ordinary_context_report_does_not_force_same_word_elsewhere(self):
        u,b,n,g,items,f=self.fixture();n[1]['family']=None
        _,forced,_=m.pending_reports(self.base,items[1],n,g,u,b,{'latest','older'},f)
        self.assertEqual(set(forced),{'u1'})
    def test_same_origin_stale_queue_does_not_repeat_completed_family_wave(self):
        u,b,n,g,items,f,origins,forced,queued=self.force()
        delivered=[]
        for unit in u:
            if unit['id']in forced:
                unit,_=m.corrected_unit(unit,f,b,True,forced[unit['id']])
            delivered.append(unit)
        for segment in g['segments']:segment['audio_hash']='newly-delivered-'+segment['id']
        _,again,_=m.pending_reports(self.base,items[1],n,g,delivered,b,{'latest','older'},f)
        self.assertEqual(again,{})
    def test_new_report_id_always_forces_new_wave_even_if_marks_identical(self):
        u,b,n,g,items,f,origins,forced,queued=self.force()
        delivered=[m.corrected_unit(unit,f,b,True,forced[unit['id']])[0]if unit['id']in forced else unit for unit in u]
        raw={**items[1]['origin_report'],'id':'new-explicit-report','created':'2026-10-25T00:02:00Z'}
        n.append({'report':raw,'family':{'александр':'Алекса́ндр'}})
        f=copy.deepcopy(f);f['family_evidence']['александр']['report_id']=raw['id']
        item={'id':raw['id'],'generation_id':'g','status':'queued','origin_report':raw,
            'origin_report_sha256':m.digest(raw),'report':{**raw,'segment_id':'u1'}}
        _,again,_=m.pending_reports(self.base,item,n,g,delivered,b,{'latest','older',raw['id']},f)
        self.assertEqual(set(again),{'u1','u2','u3','u7'})
    def test_old_family_proof_cannot_consume_another_source_target(self):
        u,b,n,g,items,f,origins,forced,queued=self.force()
        delivered,_=m.corrected_unit(u[1],f,b,True,forced['u2'])
        bad=copy.deepcopy(delivered);bad['source_id']='p3'
        with self.assertRaisesRegex(ValueError,'exact target source'):
            m.family_already_regenerated(bad,items[1]['origin_report'],{'александр':'Алекса́ндр'},b)
        bad=copy.deepcopy(delivered);bad['synthesis']['listener_stress']['sha256']='tampered'
        with self.assertRaisesRegex(ValueError,'checksum'):
            m.family_already_regenerated(bad,items[1]['origin_report'],{'александр':'Алекса́ндр'},b)


if __name__=='__main__':unittest.main()
