import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'runtime'),str(ROOT/'verification')]
import rimsky
from rimsky import listener_stress as live
from rimsky.store import Store
from rimsky.util import digest
from test_series_character_state import fb2
candidate=live

class ListenerStressChronologyTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.profile=self.root/'shared.sqlite3';self.stores=[]
    def tearDown(self):
        for store in self.stores:store.close()
        self.temp.cleanup()
    def store(self,name,text='Она гуляла с Маришкой.',cast=(),volume=4,series='Элирм'):
        store=Store(self.root/name,correction_profile=self.profile);self.stores.append(store)
        source=fb2('Synthetic IV' if volume==4 else f'Synthetic volume {volume}',volume,text).replace('Элирм'.encode(),series.encode())
        book=store.import_data(source,'fixture.fb2')
        generation=store.create_generation(book['id'],{})
        store.execute('UPDATE generations SET cast=? WHERE id=?',(json.dumps(list(cast)),generation['id']))
        store.execute("UPDATE segments SET status='ready',audio_hash=?,duration=10 WHERE generation_id=?",('a'*64,generation['id']))
        return store,book,generation
    def report(self,store,generation,selected,intended,when='2026-10-24T00:00:00Z'):
        segment=next(s for s in store.generation(generation['id'])['segments'] if selected in s['text'])
        with patch('rimsky.store.now',return_value=when):
            return store.report_stress(generation['id'],segment['id'],segment['source_id'],selected,
                segment['start']+segment['text'].index(selected),intended_stress=intended)
    def render(self,store,book):
        frozen=candidate.snapshot(store,book)
        block=next(b for c in book['chapters'] for b in c['blocks'] if 'Маришкой' in b['text'] or 'мира' in b['text'] or 'Стоит' in b['text'])
        return candidate.apply(block['text'],frozen,source_id=block['id'],source_text=block['text']),frozen
    def test_old_shared_capital_occurrence_loses_to_new_family(self):
        first,book,generation=self.store('one')
        with patch.object(live,'_proper_family',return_value=None):
            old=self.report(first,generation,'Маришкой','Ма́ришкой','2026-10-23T00:00:00Z')
        second,book,generation=self.store('two')
        new=self.report(second,generation,'Маришкой','Мари́шкой')
        actual_resolver=candidate._proper_family
        with patch.object(candidate,'_proper_family',side_effect=lambda b,r:None if r['id']==old['id'] else actual_resolver(b,r)):
            (native,_),frozen=self.render(second,book)
        self.assertEqual('Она гуляла с Мари́шкой.',native)
        self.assertEqual([],frozen['occurrences'])
        self.assertEqual('superseded',next(row['status'] for row in frozen['note_statuses'] if row['report_id']==old['id']))
        self.assertEqual(new['id'],frozen['family_evidence']['маришка']['report_id'])
    def test_equal_timestamps_use_journal_order(self):
        one,book,generation=self.store('one')
        with patch.object(live,'_proper_family',return_value=None):old=self.report(one,generation,'Маришкой','Ма́ришкой')
        two,book,generation=self.store('two');new=self.report(two,generation,'Маришкой','Мари́шкой')
        resolver=candidate._proper_family
        with patch.object(candidate,'_proper_family',side_effect=lambda b,r:None if r['id']==old['id'] else resolver(b,r)):
            (native,_),frozen=self.render(two,book)
        self.assertEqual('Она гуляла с Мари́шкой.',native)
        self.assertGreater(frozen['family_evidence']['маришка']['journal_order'],0)
    @patch('rimsky.series_characters._canonical_family_binding',return_value=None)
    def test_newer_source_occurrence_wins_older_family(self,_identity_adapter):
        one,book,generation=self.store('one');old=self.report(one,generation,'Маришкой','Мари́шкой','2026-10-23T00:00:00Z')
        two,book,generation=self.store('two')
        with patch.object(live,'_proper_family',return_value=None):new=self.report(two,generation,'Маришкой','Ма́ришкой')
        resolver=candidate._proper_family
        with patch.object(candidate,'_proper_family',side_effect=lambda b,r:None if r['id']==new['id'] else resolver(b,r)):
            (native,_),frozen=self.render(two,book)
        self.assertEqual('Она гуляла с Ма́ришкой.',native)
        self.assertEqual(new['id'],frozen['occurrences'][0]['report_id'])
    def test_lowercase_context_survives_new_name_and_bad_cast_projection_is_removed(self):
        text='Она увидела Мира у границы мира.';cast=[{'id':'mira','name':'мира'}]
        one,book,generation=self.store('one',text,cast)
        old=self.report(one,generation,'мира','мира́','2026-10-23T00:00:00Z')
        # Reproduce the pre-guard derived-family bug without changing raw input.
        with closing(sqlite3.connect(self.profile)) as db:
            with db:db.execute('UPDATE corrections SET family=?,occurrence=NULL WHERE report_id=?',
                (json.dumps({'мира':'Мира́'}),old['id']))
        two,book,generation=self.store('two',text,cast)
        new=self.report(two,generation,'Мира','Ми́ра')
        (native,_),frozen=self.render(two,book)
        self.assertEqual('Она увидела Ми́ра у границы мира́.',native)
        self.assertEqual('мира',frozen['occurrences'][0]['selected_text'])
        with closing(sqlite3.connect(self.profile)) as db:
            row=db.execute('SELECT raw,family,occurrence FROM corrections WHERE report_id=?',(old['id'],)).fetchone()
        self.assertIsNone(row[1]);self.assertEqual('мира́',json.loads(row[0])['intended_stress'])
    def test_ordinary_initial_common_word_is_not_family_even_with_cast_match(self):
        one,book,generation=self.store('one','Стоит человек.',[{'id':'stoi','name':'Стоит'}])
        report=self.report(one,generation,'Стоит','Стои́т')
        (native,_),frozen=self.render(one,book)
        self.assertEqual({},frozen['names'])
        self.assertEqual('Стои́т человек.',native)
        self.assertEqual(report['id'],frozen['occurrences'][0]['report_id'])
    def test_older_sentence_initial_ordinary_occurrence_survives_new_family(self):
        one,book,generation=self.store('one','Стоит человек. Он видит Стоит.',[{'id':'stoi','name':'Стоит'}])
        old=self.report(one,generation,'Стоит','Стои́т','2026-10-23T00:00:00Z')
        candidate.snapshot(one,book)
        # A later mid-sentence character-family note shares the same spelling.
        from rimsky.listener_stress import _book_key
        from rimsky.series_characters import identity
        raw={**old,'id':'later-family','created':'2026-10-24T00:00:00Z','text_offset':old['passage_text'].rindex('Стоит'),'intended_stress':'Сто́ит'}
        raw.pop('character_state',None);raw.pop('retake_queue',None)
        with closing(sqlite3.connect(self.profile)) as db:
            with db:db.execute('INSERT INTO corrections(report_id,book_key,series_id,created,raw,family,occurrence) VALUES(?,?,?,?,?,?,NULL)',
                (raw['id'],_book_key(book),identity(one,book)['id'],raw['created'],json.dumps(raw),json.dumps({'стоит':'Сто́ит'})))
        frozen=candidate.snapshot(one,book)
        block=next(b for c in book['chapters'] for b in c['blocks'] if 'Стоит' in b['text'])
        native,_=candidate.apply(block['text'],frozen,source_id=block['id'],source_text=block['text'])
        self.assertEqual('Стои́т человек. Он видит Сто́ит.',native)
        self.assertIn(old['id'],[row['report_id'] for row in frozen['occurrences']])
    def test_unbacked_series_state_is_not_human_authority(self):
        one,book,generation=self.store('one')
        from rimsky.series_characters import identity,upsert_character
        upsert_character(one,identity(one,book),{'name':'Маришка'},state={'pronunciation_source':'listener','pronunciations':{'маришка':'Ма́ришка'}})
        self.assertEqual({},candidate.snapshot(one,book)['names'])
    def test_v1_frozen_apply_retains_original_semantics_and_receipt(self):
        source='Она гуляла с Маришкой.'
        frozen={'policy':'listener-stress-v1','names':{'маришка':'Мари́шка'},'occurrences':[{
            'source_id':'p','selected_text':'Маришкой','intended_stress':'Ма́ришкой','text_offset':source.index('Маришкой'),
            'passage_sha256':digest(source),'report_id':'old'}]}
        start=source.index('Маришкой');end=start+len('Маришкой')
        occurrence={'start':start,'end':end,'intended_stress':'Ма́ришкой','source':'listener_source_occurrence','report_id':'old'}
        expected={'policy':'listener-stress-v1','edits':[{**occurrence,'before':'Маришкой'}],
            'applications':[{'start':start,'end':end,'intended_stress':'Мари́шкой','source':'listener_character_family','family_evidence':[]},occurrence]}
        self.assertEqual(('Она гуляла с Ма́ришкой.',{**expected,'sha256':digest(expected)}),
            candidate.apply(source,frozen,source_id='p',source_text=source))
    def test_unrelated_revision_does_not_change_unit_receipt(self):
        one,book,generation=self.store('one');self.report(one,generation,'Маришкой','Мари́шкой')
        frozen=candidate.snapshot(one,book);original=candidate.apply('Мари́шкой.',frozen)
        frozen['names']['гундахар']='Гунда́хар';frozen['family_evidence']['гундахар']={'report_id':'other','created':'later','journal_order':999,'stressed':'Гунда́хар'}
        frozen['sha256']=digest({k:v for k,v in frozen.items() if k!='sha256'})
        self.assertEqual(original,candidate.apply('Мари́шкой.',frozen))
    def test_foreign_validated_family_base_is_not_replaced_by_morphology(self):
        one,book,generation=self.store('one');self.report(one,generation,'Маришкой','Мари́шкой')
        two,book,generation=self.store('two')
        with patch.object(candidate,'_proper_family',return_value={'unrelated':'Друго́й'}):
            frozen=candidate.snapshot(two,book)
        self.assertEqual({'маришка':'Мари́шка'},frozen['names'])

    def test_feminine_name_paradigm_resolves_from_exclusive_source_form(self):
        one,book,generation=self.store('one','Она говорила с Алексой. Алекса вошла. Александр пришёл.')
        report=self.report(one,generation,'Алекса','Але́кса')
        frozen=candidate.snapshot(one,book)
        self.assertEqual('Але́кса',frozen['names']['алекса'])
        native,_=candidate.apply('Алекса Алексой Алексы Алексу Александр.',frozen)
        self.assertEqual('Але́кса Але́ксой Але́ксы Але́ксу Александр.',native)
        self.assertEqual(report['id'],frozen['family_evidence']['алекса']['report_id'])

    def test_masculine_name_paradigm_resolves_from_exclusive_nominative(self):
        one,book,generation=self.store('one','Я видел Алекса. Алекс вошёл.')
        self.report(one,generation,'Алекса','Але́кса')
        self.assertEqual('Але́кс',candidate.snapshot(one,book)['names']['алекс'])

    def test_rival_exclusive_name_paradigms_keep_occurrence_only(self):
        one,book,generation=self.store('one','Я видел Алекса. Алекс вошёл. Она говорила с Алексой.')
        report=self.report(one,generation,'Алекса','Але́кса')
        frozen=candidate.snapshot(one,book)
        self.assertEqual({},frozen['names']);self.assertEqual(report['id'],frozen['occurrences'][0]['report_id'])

    def test_longer_prefix_and_lowercase_forms_cannot_resolve_name_identity(self):
        for index,text in enumerate(('Я видел Алекса. Александр вошёл.','Я видел Алекса. Она говорила с алексой.')):
            # Separate profiles prevent a prior family from settling this case.
            self.profile=self.root/f'profile-{index}.sqlite3'
            one,book,generation=self.store(f'one-{index}',text)
            self.report(one,generation,'Алекса','Але́кса')
            self.assertEqual({},candidate.snapshot(one,book)['names'])

    def test_sentence_initial_ordinary_word_is_not_exclusive_name_evidence(self):
        from types import SimpleNamespace
        parses=[SimpleNamespace(normal_form='first',lexeme=[SimpleNamespace(word='стоит')]),
            SimpleNamespace(normal_form='second',lexeme=[SimpleNamespace(word='ждёт')])]
        self.assertIsNone(candidate._attested_name_lemma({'chapters':[{'blocks':[{'text':'Стоит человек.'}]}]},parses,['first','second']))

    def test_latest_self_correction_crosses_volumes_and_independent_libraries(self):
        from rimsky.series_characters import identity
        one,first,old_generation=self.store('dev','Маришка ждала. Она говорила с Маришкой.',volume=4)
        wrong=self.report(one,old_generation,'Маришка','Ма́ришка','2026-10-23T00:00:00Z')
        old_frozen=candidate.snapshot(one,first)
        self.assertEqual('Ма́ришка',old_frozen['names']['маришка'])
        two,second,new_generation=self.store('production','Она говорила с Маришкой.',volume=5)
        self.assertEqual(identity(one,first)['id'],identity(two,second)['id'])
        self.assertNotEqual(candidate._book_key(first),candidate._book_key(second))
        corrected=self.report(two,new_generation,'Маришкой','Мари́шкой','2026-10-24T00:00:00Z')
        expected='Мари́шка Мари́шки Мари́шке Мари́шку Мари́шкой Мари́шкою маришка.'
        source='Маришка Маришки Маришке Маришку Маришкой Маришкою маришка.'
        for store,book in ((one,first),(two,second)):
            frozen=candidate.snapshot(store,book)
            self.assertEqual(expected,candidate.apply(source,frozen)[0])
            self.assertEqual(corrected['id'],frozen['family_evidence']['маришка']['report_id'])
        three,third,_=self.store('fresh-install','Маришка вошла.',volume=6)
        self.assertEqual(expected,candidate.apply(source,candidate.snapshot(three,third))[0])
        # Earlier immutable preparation remains reproducible evidence; it is
        # not confused with the current canonical human choice.
        self.assertEqual('Ма́ришкой',candidate.apply('Маришкой',old_frozen)[0])
        history={row['report']['id']:row['report'] for row in candidate.notes(two,second)}
        self.assertEqual('Ма́ришка',history[wrong['id']]['intended_stress'])
        self.assertEqual('Мари́шкой',history[corrected['id']]['intended_stress'])

    def test_ambiguous_new_volume_reuses_unique_validated_series_family(self):
        one,first,generation=self.store('first','Она говорила с Алексой. Алекса вошла.',volume=4)
        self.report(one,generation,'Алекса','Але́кса','2026-10-23T00:00:00Z')
        two,second,later=self.store('later','Алекса вошла. Она ждала Алексу.',volume=5)
        new=self.report(two,later,'Алекса','А́лекса','2026-10-24T00:00:00Z')
        # Read the ambiguous later volume first: opening the earlier volume
        # must not be needed to repair identity or share the latest choice.
        for store,book in ((two,second),(one,first)):
            frozen=candidate.snapshot(store,book)
            self.assertEqual('А́лекса',frozen['names']['алекса'])
            self.assertEqual(new['id'],frozen['family_evidence']['алекса']['report_id'])
            self.assertEqual('А́лекса А́лексой А́лексы А́лексу Александр.',candidate.apply('Алекса Алексой Алексы Алексу Александр.',frozen)[0])

    def test_conflicting_established_families_keep_new_ambiguous_note_source_bound(self):
        one,first,generation=self.store('female','Она говорила с Алексой. Алекса вошла.',volume=4)
        self.report(one,generation,'Алекса','Але́кса','2026-10-22T00:00:00Z')
        two,second,generation=self.store('male','Я видел Алекса. Алекс вошёл.',volume=5)
        male=self.report(two,generation,'Алекса','А́лекса','2026-10-23T00:00:00Z')
        # Local exclusive evidence must win over compatibility with the old
        # female family; both identities then genuinely exist in this series.
        self.assertEqual('А́лекс',candidate.snapshot(two,second)['names']['алекс'])
        three,third,generation=self.store('ambiguous','Алекса вошла.',volume=6)
        latest=self.report(three,generation,'Алекса','Алекса́','2026-10-24T00:00:00Z')
        frozen=candidate.snapshot(three,third)
        self.assertIn(latest['id'],[row['report_id'] for row in frozen['occurrences']])
        self.assertEqual('А́лекс',frozen['names']['алекс']);self.assertEqual('Але́кса',frozen['names']['алекса'])

    def test_stale_ordinary_projection_cannot_resolve_identity(self):
        one,first,generation=self.store('old','Она говорила с алексой. алекса вошла.',volume=4)
        old=self.report(one,generation,'алекса','але́кса','2026-10-23T00:00:00Z')
        # A previous resolver's invalid lowercase projection is not authority.
        with closing(sqlite3.connect(self.profile)) as db:
            with db:db.execute('UPDATE corrections SET family=?,occurrence=NULL WHERE report_id=?',(json.dumps({'алекса':'Але́кса'}),old['id']))
        two,second,generation=self.store('later','Алекса вошла.',volume=5)
        report=self.report(two,generation,'Алекса','А́лекса')
        self.assertNotEqual('updated',report['character_state']['status'])

    def test_other_exact_series_identity_cannot_resolve_matching_name(self):
        # A valid family from another exact source-author/series identity also
        # cannot authorize the fallback merely because the name matches.
        one,first,generation=self.store('other','Она говорила с Алексой. Алекса вошла.',volume=4,series='Other synthetic series')
        self.report(one,generation,'Алекса','Але́кса','2026-10-23T00:00:00Z')
        three,third,generation=self.store('third','Алекса вошла.',volume=6)
        report=self.report(three,generation,'Алекса','А́лекса')
        self.assertNotEqual('updated',report['character_state']['status'])

    def test_reimport_old_generation_cannot_resurrect_superseded_family(self):
        from rimsky.series_characters import apply_pronunciations,sync_generation
        one,first,old_generation=self.store('old-library','Она говорила с Маришкой.',[{'id':'m','name':'Маришка'}],volume=4)
        wrong=self.report(one,old_generation,'Маришкой','Ма́ришкой','2026-10-23T00:00:00Z')
        old_analysis={'names':{'m':{'name':'Маришка','stressed':'Ма́ришка','source':'series_listener'}}}
        two,second,new_generation=self.store('new-library','Маришка вошла.',[{'id':'m','name':'Маришка'}],volume=5)
        corrected=self.report(two,new_generation,'Маришка','Мари́шка','2026-10-24T00:00:00Z')
        raw_before={row['report']['id']:row['report'] for row in candidate.notes(two,second)}
        # Importing the old source/analysis into the newer library must replay
        # original timestamps and IDs, never reinterpret it as a fresh choice.
        old_source=two.import_data(fb2('Synthetic IV',4,'Она говорила с Маришкой.'),'old-again.fb2')
        imported=two.create_generation(old_source['id'],{})
        imported={**imported,'cast':[{'id':'m','name':'Маришка'}],'analysis':{'pronunciation':old_analysis}}
        sync_generation(two,old_source,imported,old_analysis)
        candidate.record(two,old_source,raw_before[wrong['id']],{'status':'updated','canonical_name':'Маришка','pronunciations':{'маришка':'Ма́ришка'}})
        for store,book in ((one,first),(two,second),(two,old_source)):
            frozen=candidate.snapshot(store,book)
            self.assertEqual('Мари́шкой',candidate.apply('Маришкой',frozen)[0])
            self.assertEqual(corrected['id'],frozen['family_evidence']['маришка']['report_id'])
        inherited=apply_pronunciations(two,old_source,imported['cast'],old_analysis)
        self.assertEqual('Мари́шка',inherited['names']['m']['stressed'])
        self.assertEqual(raw_before,{row['report']['id']:row['report'] for row in candidate.notes(two,second)})

    def test_self_correction_invalidates_every_current_family_mention_only(self):
        from rimsky.jobs import Jobs
        text='Маришка вошла.</p><p>Он говорил с Маришкой.</p><p>Он ждал Маришку.</p><p>Обычная фраза.'
        store,book,old_generation=self.store('current',text,[{'id':'m','name':'Маришка'}])
        self.report(store,old_generation,'Маришка','Ма́ришка','2026-10-23T00:00:00Z')
        # A true legacy output is distinct from the stable current ID. Give
        # it a later timestamp and saved position to exercise both old routes.
        legacy_id='1'*24
        store.execute('''INSERT INTO generations(id,book_id,name,created,updated,status,config,"cast",analysis,error,parent_id)
            SELECT ?,book_id,'Legacy history','2030-01-01',updated,'ready',config,"cast",analysis,error,NULL
            FROM generations WHERE id=?''',(legacy_id,old_generation['id']))
        from rimsky.current_audiobook import FIELDS
        columns=','.join(FIELDS)
        store.execute(f'INSERT INTO segments({columns}) SELECT ?,'+','.join(FIELDS[1:])+' FROM segments WHERE generation_id=?',
            (legacy_id,old_generation['id']))
        old_generation=store.generation(legacy_id)
        store.execute('INSERT INTO positions(book_id,value) VALUES(?,?)',
            (book['id'],json.dumps({'generation_id':legacy_id,'segment_id':old_generation['segments'][0]['id']})))
        with patch('rimsky.store.now',return_value='2026-10-25T00:00:00Z'):
            current=store.create_generation(book['id'],{})
        store.execute('UPDATE generations SET cast=? WHERE id=?',(json.dumps([{'id':'m','name':'Маришка'}]),current['id']))
        store.execute("UPDATE segments SET status='ready',audio_hash=?,duration=10 WHERE generation_id=?",('b'*64,current['id']))
        store.execute("UPDATE generations SET status='ready' WHERE id=?",(current['id'],))
        before=store.generation(current['id']);old_before=store.generation(old_generation['id'])['segments']
        report=self.report(store,current,'Маришкой','Мари́шкой','2026-10-26T00:00:00Z')
        jobs=Jobs(store,ROOT,engine_factory=lambda _pack:None)
        jobs.active=legacy_id  # A historical owner must not expand the target set.
        with patch.object(jobs,'_start_listener_queue'):
            outcome=jobs.listener_stress(report)
        after=store.generation(current['id'])
        affected=[row for row in after['segments'] if any(word in row['text'] for word in ('Маришка','Маришкой','Маришку'))]
        self.assertEqual(3,len(affected));self.assertEqual(3,outcome['targets'])
        self.assertTrue(all(row['status']=='pending' and row['audio_hash'] is None for row in affected))
        unchanged=next(row for row in after['segments'] if 'Обычная' in row['text'])
        self.assertEqual(next(row for row in before['segments'] if row['id']==unchanged['id']),unchanged)
        self.assertEqual(old_before,store.generation(old_generation['id'])['segments'])
        self.assertFalse(jobs.stop.is_set())
        self.assertEqual([current['id']],jobs.listener_queue)

if __name__=='__main__':unittest.main()
