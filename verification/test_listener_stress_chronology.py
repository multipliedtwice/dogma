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
    def store(self,name,text='Она гуляла с Маришкой.',cast=()):
        store=Store(self.root/name,correction_profile=self.profile);self.stores.append(store)
        book=store.import_data(fb2('Synthetic IV',4,text),'fixture.fb2')
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
    def test_newer_source_occurrence_wins_older_family(self):
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

if __name__=='__main__':unittest.main()
