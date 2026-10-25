"""Private shared human preparation feedback, without providers or synthesis."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'runtime')]
import public_demo_listener_feedback as feedback
import freeze_public_audiobook_demo as freeze
from rimsky import russian_pronunciation as phonetics


class PublicFeedbackTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.profile=self.root/'shared.sqlite3'

    def source(self,text,source_hash='b'*64):
        book={'id':source_hash[:32],'source_hash':source_hash,'title':'Synthetic source','author':'Fixture author',
            'chapters':[{'id':'c','blocks':[{'id':'p','text':text}]}]}
        unit={'id':'u1','chapter_id':'c','source_id':'p','start':0,'end':len(text),'text':text,'spoken':text}
        return book,unit

    def write(self,name,data):
        path=self.root/name;path.write_text(json.dumps(data,ensure_ascii=False),encoding='utf8');return path

    def stress(self,unit,word,marked):
        return self.write('notes.json',{'schema':1,'created':'2026-10-24T00:00:00Z','created_basis':'explicit_fixture',
            'audio_approval':False,'notes':[{'unit':unit['id'],'source_text':unit['text'],
                'selected_text':word,'intended_stress':marked,'instruction':'Explicit human fixture instruction'}]})

    def direct(self,store,book,unit,word,marked,identifier='direct',created='2026-10-24T00:00:00Z'):
        raw={'id':identifier,'book_id':book['id'],'generation_id':'fixture','segment_id':unit['id'],
             'chapter_id':unit['chapter_id'],'source_id':unit['source_id'],'kind':'stress',
             'selected_text':word,'intended_stress':marked,'passage_text':unit['text'],
             'text_offset':unit['text'].index(word),'created':created,'comment':'Direct human fixture'}
        feedback.listener_stress.record(store,book,raw,_project=False)
        return raw

    def test_copied_legacy_export_cannot_override_direct_occurrence_or_later_direct_choice(self):
        book,unit=self.source('На площади ни души.')
        path=self.write('copied.json',{'schema':1,'human_approval':False,'changes':[{'id':'u1',
            'source_text':unit['text'],'before':unit['text'],'after':unit['text'].replace('души','ду́ши'),
            'basis':'Historical instruction'}]})
        os.utime(path,(2051222400,2051222400)) # copying in 2035 is not a 2035 human choice
        with feedback.source_store(self.root/'library',self.profile)as store:
            self.direct(store,book,unit,'души','души́')
            ids=feedback.import_overrides(store,book,{'units':[unit]},path)
            frozen=feedback.freeze(store,book)
            actual,_=feedback.apply(unit['text'],frozen,source_id='p',source_text=unit['text'],source_start=0,source_end=unit['end'])
            self.assertEqual('На площади ни души́.',actual)
            status=next(row for row in frozen['stress']['note_statuses']if row['report_id']==ids[0])
            self.assertEqual(('superseded','direct'),(status['status'],status['superseded_by_report_id']))
            original=next(row['report']for row in feedback.listener_stress.notes(store,book)if row['report']['id']==ids[0])
            os.utime(path,(2082758400,2082758400))
            self.assertEqual(ids,feedback.import_overrides(store,book,{'units':[unit]},path))
            self.assertEqual(original,next(row['report']for row in feedback.listener_stress.notes(store,book)if row['report']['id']==ids[0]))
            self.direct(store,book,unit,'души','ду́ши','later','2026-10-25T00:00:00Z')
            later=feedback.freeze(store,book)
            self.assertEqual('later',later['stress']['occurrences'][0]['report_id'])

    def test_legacy_mtime_raw_family_stays_immutable_and_projection_uses_direct_authority(self):
        book,unit=self.source('Она гуляла с Маришкой.')
        path=self.write('legacy.json',{'schema':1,'human_approval':False,'changes':[{'id':'u1',
            'source_text':unit['text'],'before':unit['text'],'after':unit['text'].replace('Маришкой','Ма́ришкой'),
            'basis':'Historical name choice'}]})
        with feedback.source_store(self.root/'library',self.profile)as store:
            ids=feedback.import_overrides(store,book,{'units':[unit]},path)
            # Reproduce receipts imported by the old released mtime importer.
            with feedback.listener_stress._journal(store)as db:
                row=db.execute('SELECT * FROM corrections WHERE report_id=?',(ids[0],)).fetchone()
                raw=json.loads(row['raw']);raw['created']='2035-01-01T00:00:00+00:00'
                raw['preparation_origin'].update(created=raw['created'],created_basis='original_preparation_export_mtime')
                db.execute('UPDATE corrections SET created=?,raw=? WHERE report_id=?',
                    (raw['created'],json.dumps(raw,ensure_ascii=False,sort_keys=True),ids[0]))
            self.direct(store,book,unit,'Маришкой','Мари́шкой')
            with feedback.listener_stress._journal(store)as db:
                before=dict(db.execute('SELECT rowid,* FROM corrections WHERE report_id=?',(ids[0],)).fetchone())
            frozen=feedback.freeze(store,book)
            self.assertEqual('Мари́шка',frozen['stress']['names']['маришка'])
            from rimsky.series_characters import series_state
            state=series_state(store,book)[1][0]['state']
            self.assertEqual('Мари́шка',state['pronunciations']['маришка'])
            self.assertEqual('direct',state['listener_family_projection']['маришка']['report_id'])
            self.direct(store,book,unit,'Маришкой','Ма́ришкой','later','2026-10-25T00:00:00Z')
            self.assertEqual('later',feedback.freeze(store,book)['stress']['family_evidence']['маришка']['report_id'])
            with feedback.listener_stress._journal(store)as db:
                after=dict(db.execute('SELECT rowid,* FROM corrections WHERE report_id=?',(ids[0],)).fetchone())
            for field in ('rowid','raw','created','series_id','book_key'):
                self.assertEqual(before[field],after[field],field)

    def test_imported_token_substitution_cannot_reapply_after_direct_choice(self):
        book,unit=self.source('Он вышел из дровяного склада.')
        path=self.write('phonetic.json',{'schema':1,'human_approval':False,'changes':[{'id':'u1',
            'source_text':unit['text'],'before':unit['text'],'after':unit['text'].replace('дровяного','дровяново'),
            'basis':'Historical phonetic spelling'}]})
        with feedback.source_store(self.root/'library',self.profile)as store:
            ids=feedback.import_overrides(store,book,{'units':[unit]},path)
            self.direct(store,book,unit,'дровяного','дровяно́го')
            frozen=feedback.freeze(store,book)
        self.assertEqual([],frozen['token_edits'])
        self.assertEqual('superseded',next(row['status']for row in frozen['note_statuses']if row['report_id']==ids[0]))
        actual,_=feedback.apply(unit['text'],frozen,source_id='p',source_text=unit['text'],source_start=0,source_end=unit['end'])
        self.assertEqual('Он вышел из дровяно́го склада.',actual)

    def test_source_note_rejects_timestamp_without_original_timezone(self):
        book,unit=self.source('На площади ни души.')
        path=self.stress(unit,'души','души́');doc=json.loads(path.read_text('utf8'))
        doc['created']='2026-10-24';path.write_text(json.dumps(doc),encoding='utf8')
        with feedback.source_store(self.root/'library',self.profile)as store:
            with self.assertRaisesRegex(ValueError,'timestamp'):feedback.import_source_notes(store,book,{'units':[unit]},path)

    def test_valid_source_timestamp_is_utc_and_later_direct_note_wins_actual_time(self):
        book,unit=self.source('На площади ни души.')
        path=self.stress(unit,'души','ду́ши');doc=json.loads(path.read_text('utf8'))
        doc['created']='2026-10-24T23:00:00+07:00';path.write_text(json.dumps(doc),encoding='utf8')
        with feedback.source_store(self.root/'library',self.profile)as store:
            self.direct(store,book,unit,'души','души́',created='2026-10-24T17:00:00Z')
            ids=feedback.import_source_notes(store,book,{'units':[unit]},path)
            frozen=feedback.freeze(store,book)
            self.assertEqual('direct',frozen['stress']['occurrences'][0]['report_id'])
            raw=next(row['report']for row in feedback.listener_stress.notes(store,book)if row['report']['id']==ids[0])
            self.assertEqual('2026-10-24T16:00:00Z',raw['created'])
            self.assertEqual(doc['created'],raw['preparation_origin']['original_created'])

    def test_new_direct_family_also_supersedes_legacy_token_edit_on_other_inflection(self):
        book,unit=self.source('Она гуляла с Маришкой. Маришка ждала.')
        path=self.write('phonetic-name.json',{'schema':1,'human_approval':False,'changes':[{'id':'u1',
            'source_text':unit['text'],'before':unit['text'],'after':unit['text'].replace('Маришкой','Маришкай'),
            'basis':'Historical spelling instruction'}]})
        with feedback.source_store(self.root/'library',self.profile)as store:
            feedback.import_overrides(store,book,{'units':[unit]},path)
            self.direct(store,book,unit,'Маришка','Мари́шка')
            frozen=feedback.freeze(store,book)
            self.assertEqual([],frozen['token_edits'])
            actual,_=feedback.apply(unit['text'],frozen,source_id='p',source_text=unit['text'],source_start=0,source_end=unit['end'])
            self.assertEqual('Она гуляла с Мари́шкой. Мари́шка ждала.',actual)

    def test_noncast_human_family_overrides_automatic_and_survives_independent_preparation(self):
        book,unit=self.source('Она гуляла с Маришкой. Маришка ждала Маришку.')
        note=self.stress(unit,'Маришкой','Мари́шкой')
        with feedback.source_store(self.root/'first',self.profile) as store:
            feedback.import_source_notes(store,book,{'units':[unit]},note)
            self.assertEqual([],store.rows('SELECT id FROM books'))
            frozen=feedback.freeze(store,book)
        with feedback.source_store(self.root/'later',self.profile) as store:
            later=feedback.freeze(store,book)
            foreign=copy.deepcopy(book);foreign['author']='Unrelated author';foreign['title']='Unrelated book'
            foreign['source_hash']='f'*64;foreign['id']='f'*32
            self.assertEqual({},feedback.freeze(store,foreign)['stress']['names'])
        actual,receipt=feedback.apply('Она гуляла с Ма́ришкой. Ма́ришка ждала Ма́ришку.',later,
            source_id='p',source_text=unit['text'],source_start=0,source_end=unit['end'])
        self.assertEqual('Она гуляла с Мари́шкой. Мари́шка ждала Мари́шку.',actual)
        self.assertEqual(frozen['stress']['names'],later['stress']['names'])
        feedback.validate(receipt,later)

    def test_ordinary_stress_is_source_bound_and_different_book_rules_do_not_leak(self):
        book,unit=self.source('На площади ни души. А дальше души поют.')
        path=self.stress(unit,'души','души́')
        # Repeated source words require an explicit bound occurrence, not a guess.
        with feedback.source_store(self.root/'library',self.profile) as store:
            with self.assertRaisesRegex(ValueError,'unique source occurrence'):
                feedback.import_source_notes(store,book,{'units':[unit]},path)
        book,unit=self.source('На площади ни души.')
        path=self.stress(unit,'души','души́')
        with feedback.source_store(self.root/'one',self.profile) as store:
            feedback.import_source_notes(store,book,{'units':[unit]},path)
            frozen=feedback.freeze(store,book)
            other,_=self.source('На площади ни души.','c'*64)
            unrelated=feedback.freeze(store,other)
        actual,_=feedback.apply('На площади ни ду́ши.',frozen,source_id='p',source_text=unit['text'],source_start=0,source_end=unit['end'])
        self.assertEqual('На площади ни души́.',actual)
        self.assertEqual([],unrelated['stress']['occurrences'])
        self.assertEqual({},unrelated['stress']['names'])

    def test_exact_phonetic_edit_keeps_new_automatic_stress_and_no_heard_evidence_is_invented(self):
        book,unit=self.source('Он вышел из дровяного склада.')
        path=self.write('old.json',{'schema':1,'human_approval':False,'changes':[{'id':'u1','source_text':unit['text'],
            'before':'Он вы́шел из дровяно́го склада.','after':'Он вы́шел из дровяно́во склада.',
            'basis':'Explicit human pronunciation instruction'}]})
        with feedback.source_store(self.root/'library',self.profile) as store:
            ids=feedback.import_overrides(store,book,{'units':[unit]},path)
            frozen=feedback.freeze(store,book)
            original=feedback.listener_stress.notes(store,book)[0]['report']
            moved=self.root/'moved.json';moved.write_bytes(path.read_bytes())
            self.assertEqual(ids,feedback.import_overrides(store,book,{'units':[unit]},moved))
            self.assertEqual(original,feedback.listener_stress.notes(store,book)[0]['report'])
        before='Он вы́шел из дровя́ного склада.'
        after,receipt=feedback.apply(before,frozen,source_id='p',source_text=unit['text'],source_start=0,source_end=unit['end'])
        self.assertEqual('Он вы́шел из дровя́ново склада.',after)
        self.assertIsNone(original['audio_hash']);self.assertEqual('',original['generation_id'])
        self.assertFalse(original['preparation_origin']['audio_approval'])
        feedback.validate(receipt,frozen)

    def test_final_freeze_replays_human_then_pronoun_phonetics_and_rejects_tampering(self):
        book,unit=self.source('Он никого не видел около дровяного склада.')
        path=self.write('old.json',{'schema':1,'human_approval':False,'changes':[{'id':'u1','source_text':unit['text'],
            'before':unit['text'],'after':unit['text'].replace('дровяного','дровяново'),'basis':'Explicit phonetic instruction'}]})
        with feedback.source_store(self.root/'library',self.profile) as store:
            feedback.import_overrides(store,book,{'units':[unit]},path);frozen=feedback.freeze(store,book)
        human,receipt=feedback.apply(unit['text'],frozen,source_id='p',source_text=unit['text'],source_start=0,source_end=unit['end'])
        final,records=phonetics.apply(human)
        phonetic={'rule':phonetics.RULE,'prephonetic_source':human,'records':records,
            'module_sha256':feedback.file_digest(Path(phonetics.__file__))}
        self.assertIn('никаво',final)
        freeze.validate_synthesis_pronunciation(unit['text'],final,phonetic,listener=receipt,listener_context=frozen)
        with self.assertRaises(ValueError):freeze.validate_synthesis_pronunciation(unit['text'],final,phonetic)
        tampered=copy.deepcopy(receipt);tampered['after']=tampered['after'].replace('склада','дома')
        with self.assertRaises(ValueError):feedback.validate(tampered,frozen)

    def test_source_changes_cannot_reuse_imported_phonetic_edit(self):
        book,unit=self.source('Он вышел из дровяного склада.')
        path=self.write('old.json',{'schema':1,'human_approval':False,'changes':[{'id':'u1','source_text':unit['text'],
            'before':unit['text'],'after':unit['text'].replace('дровяного','дровяново'),'basis':'Explicit human instruction'}]})
        with feedback.source_store(self.root/'library',self.profile) as store:
            feedback.import_overrides(store,book,{'units':[unit]},path);frozen=feedback.freeze(store,book)
        changed=unit['text'].replace('вышел','ушёл')
        actual,receipt=feedback.apply(changed,frozen,source_id='p',source_text=changed,source_start=0,source_end=len(changed))
        self.assertEqual(changed,actual);self.assertEqual([],receipt['token_edits'])


if __name__=='__main__':unittest.main()
