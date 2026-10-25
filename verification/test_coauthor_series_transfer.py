"""Ordered legacy coauthor keys remain readable without rewriting human data."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'runtime'),str(ROOT/'verification')]
from rimsky.store import Store
from rimsky.util import digest
from rimsky.series_characters import identity,normalized,series_ids,series_state,upsert_character
from rimsky.listener_stress import _journal,_book_key,notes,snapshot
from test_series_character_state import fb2


class CoauthorSeriesTransferTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.store=Store(self.root/'library',correction_profile=self.root/'journal.sqlite3')
    def tearDown(self):
        self.store.close();self.temp.cleanup()
    def book(self,authors=('Борис Автор','Анна Автор'),series='Общий цикл',volume=2):
        text=fb2(f'{series} {volume}',volume,'Он увидел Гундахара.').decode()
        old='<author><first-name>Владимир</first-name><last-name>Автор</last-name></author>'
        text=text.replace(old,''.join(f'<author><nickname>{author}</nickname></author>'for author in authors))
        text=text.replace('name="Элирм"',f'name="{series}"')
        return self.store.import_data(text.encode(),f'{volume}.fb2')
    def legacy(self,book,authors=('Анна Автор','Борис Автор'),series='Общий цикл',legacy_id=None):
        author=', '.join(authors)
        old=legacy_id or digest({'author':normalized(author),'series':normalized(series)})[:32]
        group={**identity(self.store,book),'id':old,'name':series,'author':author}
        upsert_character(self.store,group,{'name':'Гундахар'},state={
            'pronunciations':{'гундахар':'Гу́ндахар'},'pronunciation_source':'model'})
        passage='Он встретил Гундахара.';selected='Гундахара'
        raw={'id':'legacy-coauthor-note','book_id':'previous-volume','generation_id':'historical',
             'kind':'stress','selected_text':selected,'intended_stress':'Гундаха́ра',
             'source_id':'old-p1','passage_text':passage,'text_offset':passage.index(selected),
             'created':'2026-10-20T00:00:00Z'}
        encoded=json.dumps(raw,ensure_ascii=False,sort_keys=True)
        with _journal(self.store)as db:
            db.execute('INSERT INTO corrections VALUES(?,?,?,?,?,?,?)',(raw['id'],'old-source',old,
                raw['created'],encoded,json.dumps({'гундахар':'Гундаха́р'},ensure_ascii=False),None))
        return old,raw
    def journal_row(self):
        with _journal(self.store)as db:
            return dict(db.execute('SELECT rowid,* FROM corrections WHERE report_id=?',('legacy-coauthor-note',)).fetchone())

    def test_reversed_coauthors_share_identity_preserve_duplicates_and_display_order(self):
        a=self.book(('Анна Автор','Борис Автор'),volume=1)
        b=self.book(('Борис Автор','Анна Автор'),volume=2)
        self.assertEqual(identity(self.store,a)['id'],identity(self.store,b)['id'])
        self.assertNotEqual(identity(self.store,a)['author'],identity(self.store,b)['author'])
        duplicate=self.book(('Анна Автор','Борис Автор','Анна Автор'),volume=3)
        self.assertNotEqual(identity(self.store,a)['id'],identity(self.store,duplicate)['id'])

    def test_later_volume_read_first_inherits_legacy_note_without_rewriting_raw_or_state(self):
        later=self.book();old,raw=self.legacy(later)
        original=self.journal_row()
        old_state=self.store.rows('SELECT * FROM series_character_state WHERE series_id=?',(old,))[0]
        self.assertIn(old,series_ids(self.store,identity(self.store,later)))
        frozen=snapshot(self.store,later)
        self.assertEqual('Гундаха́р',frozen['names']['гундахар'])
        self.assertEqual(raw['id'],frozen['family_evidence']['гундахар']['report_id'])
        final=self.journal_row()
        for field in ('rowid','report_id','raw','book_key','series_id','created'):
            self.assertEqual(original[field],final[field],field)
        self.assertEqual(old_state,self.store.rows('SELECT * FROM series_character_state WHERE series_id=?',(old,))[0])
        self.assertEqual(raw,next(row['report']for row in notes(self.store,later)if row['report']['id']==raw['id']))

    def test_canonical_state_wins_duplicate_identity_but_journal_chronology_wins_stress(self):
        book=self.book();self.legacy(book)
        group=identity(self.store,book)
        upsert_character(self.store,group,{'name':'Гундахар'},state={
            'pronunciations':{'гундахар':'Гунда́хар'},'pronunciation_source':'model'})
        older={**self.journal_row(),'report_id':'older-canonical'}
        raw=json.loads(older['raw']);raw.update(id='older-canonical',intended_stress='Гунда́хара',created='2026-10-19T00:00:00Z')
        with _journal(self.store)as db:
            db.execute('INSERT INTO corrections VALUES(?,?,?,?,?,?,?)',(raw['id'],'old-source',group['id'],
                raw['created'],json.dumps(raw,ensure_ascii=False),json.dumps({'гундахар':'Гунда́хар'},ensure_ascii=False),None))
        self.assertEqual('Гунда́хар',series_state(self.store,book)[1][0]['state']['pronunciations']['гундахар'])
        self.assertEqual('Гундаха́р',snapshot(self.store,book)['names']['гундахар'])

    def test_author_series_unknown_and_forged_identifier_do_not_alias(self):
        cases=[(('Другой Автор','Борис Автор'),'Общий цикл',None),
               (('Анна Автор','Борис Автор'),'Другой цикл',None),
               (('Анна Автор','Борис Автор'),'Общий цикл','forged-legacy-id')]
        for authors,series,old_id in cases:
            with self.subTest(authors=authors,series=series,old_id=old_id):
                # Independent local libraries prevent intentional state overlap
                # in one case from becoming another case's alias evidence.
                self.store.close();self.temp.cleanup();self.setUp()
                book=self.book();old,_=self.legacy(book,authors,series,old_id)
                self.assertNotIn(old,series_ids(self.store,identity(self.store,book)))
                self.assertEqual([],notes(self.store,book))
        unknown=self.book(('Unknown author','Unknown author'),volume=5)
        group=identity(self.store,unknown)
        self.assertIn('source',group['key'])
        self.assertEqual((group['id'],),series_ids(self.store,group))

    def test_current_display_legacy_order_is_readable_without_state_and_single_author_is_unchanged(self):
        book=self.book();group=identity(self.store,book)
        old=digest({'author':normalized(group['author']),'series':normalized(group['name'])})[:32]
        self.assertIn(old,series_ids(self.store,group))
        single=self.book(('Анна Автор',),volume=6);one=identity(self.store,single)
        self.assertEqual(digest({'author':normalized(one['author']),'series':normalized(one['name'])})[:32],one['id'])
        self.assertEqual((one['id'],),series_ids(self.store,one))


if __name__=='__main__':unittest.main()
