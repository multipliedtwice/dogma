"""Series character state grows across volumes and survives book deletion."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'runtime'))

from rimsky.series_characters import apply_pronunciations, propagation_targets, record_listener_stress, seed_cast, sync_generation,identity,upsert_character,generation_characters,unique_name_bindings,_base_match
from rimsky.store import Store
from rimsky.util import digest


def fb2(title: str, volume: int, text: str) -> bytes:
    return f'''<?xml version="1.0" encoding="utf-8"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0"><description><title-info>
<author><first-name>Владимир</first-name><last-name>Автор</last-name></author>
<book-title>{title}</book-title><lang>ru</lang><sequence name="Элирм" number="{volume}"/>
</title-info></description><body><section><title><p>Глава 1</p></title><p>{text}</p></section></body></FictionBook>'''.encode()


class SeriesCharacterStateTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.store = Store(self.root / 'library', resources=ROOT / 'resources')

    def tearDown(self):
        self.store.close()
        self.temporary.cleanup()

    def generation(self, book, cast):
        generation = self.store.create_generation(book['id'], {})
        self.store.execute('UPDATE generations SET cast=? WHERE id=?',
                           (json.dumps(cast, ensure_ascii=False), generation['id']))
        return self.store.generation(generation['id'])

    def test_listener_name_stress_survives_deleted_volume_and_seeds_later_volume(self):
        first = self.store.import_data(fb2('Элирм IV', 4, 'Гундахар посмотрел на Влада.'), 'four.fb2')
        cast = [{'id': 'gundahar', 'name': 'Гундахар', 'aliases': [], 'voice_id': 'masculine_6'}]
        generation = self.generation(first, cast)
        report = {'id': 'r' * 24, 'book_id': first['id'], 'generation_id': generation['id'],
                  'selected_text': 'Гундахар', 'intended_stress': 'Гунда́хар',
                  'created': '2026-10-21T00:00:00Z'}
        promoted = record_listener_stress(self.store, report)
        self.assertEqual('updated', promoted['status'])
        self.assertEqual('Гунда́хар', promoted['pronunciations']['гундахар'])

        self.store.delete_book(first['id'])
        self.assertEqual(1, len(self.store.rows('SELECT * FROM series_character_state')))

        second = self.store.import_data(fb2('Элирм V', 5, 'Гундахар встретил Назу.'), 'five.fb2')
        later_cast = [*cast, {'id': 'nazu', 'name': 'Назу', 'aliases': [], 'voice_id': 'masculine_4'}]
        later = self.generation(second, later_cast)
        inherited = seed_cast(self.store, second)
        self.assertEqual(['Гундахар'], [row['name'] for row in inherited])
        self.assertTrue(inherited[0]['_series_seed'])
        _, state = sync_generation(self.store, second, later)
        self.assertEqual({'Гундахар', 'Назу'}, {row['canonical_name'] for row in state})
        seeded = apply_pronunciations(self.store, second, later_cast,
                                      {'policy': 'test', 'names': {'gundahar': {'name': 'Гундахар', 'stressed': 'Гу́ндахар', 'source': 'model'}}})
        self.assertEqual('Гунда́хар', seeded['names']['gundahar']['stressed'])
        self.assertEqual('series_listener', seeded['names']['gundahar']['source'])
        from rimsky.listener_stress import notes
        legacy=next(row['report'] for row in notes(self.store,second) if row['report'].get('original_report_id')==report['id'])
        self.assertEqual('legacy_family',legacy['kind'])
        self.assertEqual(report['selected_text'],legacy['legacy_evidence']['selected_text'])
        self.assertEqual(first['id'],legacy['book_id'])
        self.assertNotIn('text_offset',legacy);self.assertNotIn('passage_text',legacy)

    def test_unrelated_anonymous_books_do_not_share_name_corrections(self):
        import re
        def anonymous(text):
            source=fb2('',1,text).decode('utf8')
            return re.sub(r'<author>.*?</author>|<sequence[^>]*/>','',source).encode('utf8')
        first=self.store.import_data(anonymous('Гундахар сказал.'),'anonymous-a.fb2')
        second=self.store.import_data(anonymous('Гундахар ушёл.'),'anonymous-b.fb2')
        self.assertNotEqual(identity(self.store,first)['id'],identity(self.store,second)['id'])
        cast=[{'id':'gundahar','name':'Гундахар','aliases':[]}]
        generation=self.generation(first,cast)
        self.formal_report(first,generation,'Гундахар','Гунда́хар')
        from rimsky.listener_stress import snapshot,notes
        self.assertIn('Гунда́хар',snapshot(self.store,first)['names'].values())
        self.assertEqual({},snapshot(self.store,second)['names'])
        self.assertEqual([],notes(self.store,second))

    def test_case_only_binding_duplicates_do_not_erase_a_cast_name_family(self):
        book=self.store.import_data(fb2('Synthetic IV',4,'Гундахар сказал. Он увидел Гундахара.'),'name.fb2')
        generation=self.generation(book,[{'id':'gundahar','name':'Гундахар','aliases':['гундахар']}])
        self.formal_report(book,generation,'Гундахар','Гунда́хар')
        from rimsky.listener_stress import snapshot,apply
        frozen=snapshot(self.store,book)
        self.assertIn('Гунда́хар',frozen['names'].values())
        self.assertEqual('Гунда́хар Гунда́хара Гунда́харом',apply('Гундахар Гундахара Гундахаром',frozen)[0])
        choices=[('елена','Еле́на'),('елена','еле́на'),('елена','Е́лена'),('елена','Елёна'),('елена','Эле́на')]
        self.assertEqual([choices[0],*choices[2:]],unique_name_bindings(choices))

    def test_irregular_name_form_carries_only_a_retained_stressed_vowel_to_base(self):
        self.assertEqual(('павел','Па́вел'),_base_match({'canonical_name':'Павел'},'Павлом','Па́влом'))
        self.assertIsNone(_base_match({'canonical_name':'Павел'},'Павла','Павла́'))

    def test_shared_case_form_does_not_choose_identity_from_the_stress_position(self):
        book=self.store.import_data(fb2('Synthetic IV',4,'Алекса вошла.'),'ambiguous.fb2')
        generation=self.generation(book,[{'id':'male','name':'Алекс'},{'id':'female','name':'Алекса'}])
        report=self.formal_report(book,generation,'Алекса','Алекса́')
        from rimsky.listener_stress import snapshot
        frozen=snapshot(self.store,book)
        self.assertEqual({},frozen['names'])
        self.assertIn(report['id'],[row['report_id']for row in frozen['occurrences']])

    def test_external_display_names_require_exact_snapshot_binding_and_keep_live_names(self):
        book=self.store.import_data(fb2('Synthetic IV',4,'Гундахар сказал.'),'cast.fb2')
        generation=self.generation(book,[{'id':'speaker','name':'CurrentName','aliases':[]}])
        checkpoint=self.root/'analysis/c0001/cast.json';checkpoint.parent.mkdir(parents=True)
        checkpoint.write_text(json.dumps([{'id':'speaker','name':'StaleName'}]),encoding='utf8')
        generation['analysis']={'external_preview':{'generation':'old-folder'}}
        self.assertEqual('CurrentName',generation_characters(self.store,generation)[0]['name'])
        generation['cast'][0]['name']='speaker'
        snapshot={'book_id':book['id'],'source_hash':book['source_hash'],'generation_id':generation['id'],
            'characters':[{'id':'speaker','name':'BoundName','aliases':['BoundAlias']}]}
        snapshot['sha256']=digest(snapshot)
        generation['analysis']['external_preview']['cast_snapshot']=snapshot
        self.assertEqual('BoundName',generation_characters(self.store,generation)[0]['name'])
        generation['cast'][0]['name']='HumanName'
        self.assertEqual('HumanName',generation_characters(self.store,generation)[0]['name'])
        generation['cast'][0]['name']='speaker'
        for field,value in (('book_id','foreign'),('source_hash','wrong'),('generation_id','foreign')):
            with self.subTest(field=field):
                changed={**snapshot,field:value};changed['sha256']=digest({k:v for k,v in changed.items()if k!='sha256'})
                generation['analysis']['external_preview']['cast_snapshot']=changed
                self.assertEqual('speaker',generation_characters(self.store,generation)[0]['name'])

    def formal_report(self,book,generation,selected,intended):
        self.store.execute("UPDATE segments SET status='ready',audio_hash=?,duration=10 WHERE generation_id=?",('a'*64,generation['id']))
        segment=next(row for row in self.store.generation(generation['id'])['segments'] if selected in row['text'])
        return self.store.report_stress(generation['id'],segment['id'],segment['source_id'],selected,
            segment['start']+segment['text'].index(selected),intended_stress=intended)

    def stale_projection(self,book,character,report,stressed):
        evidence={key:report[key] for key in ('book_id','generation_id','selected_text','intended_stress','created')}
        evidence.update(report_id=report['id'],source='listener')
        upsert_character(self.store,identity(self.store,book),character,state={'pronunciation_source':'listener',
            'pronunciations':{character['name'].casefold():stressed},'pronunciation_evidence':[evidence]})
        return {'names':{character['id']:{'name':character['name'],'stressed':stressed,'source':'series_listener'}}}

    def test_lowercase_note_downgrade_removes_saved_legacy_projection(self):
        book=self.store.import_data(fb2('Synthetic IV',4,'Он не видел мира.'),'four.fb2')
        character={'id':'mira','name':'Мира'};generation=self.generation(book,[character])
        report=self.formal_report(book,generation,'мира','мира́')
        saved=self.stale_projection(book,character,report,'Мира́')
        result=apply_pronunciations(self.store,book,[character],saved)
        self.assertNotIn('mira',result['names']);self.assertTrue(result['listener_projection_holds'])
        from rimsky.listener_stress import snapshot
        frozen=snapshot(self.store,book)
        self.assertEqual({},frozen['names']);self.assertEqual('мира́',frozen['occurrences'][0]['intended_stress'])

    def test_sentence_initial_ordinary_note_does_not_survive_as_family_projection(self):
        book=self.store.import_data(fb2('Synthetic IV',4,'Стоит человек.'),'four.fb2')
        character={'id':'stoit','name':'Стоит'};generation=self.generation(book,[character])
        report=self.formal_report(book,generation,'Стоит','Стои́т')
        saved=self.stale_projection(book,character,report,'Сто́ит')
        result=apply_pronunciations(self.store,book,[character],saved)
        self.assertNotIn('stoit',result['names'])

    def test_explicit_saved_canonical_family_overrides_automatic_and_old_projection(self):
        book=self.store.import_data(fb2('Synthetic IV',4,'Он говорил с Гундахаром.'),'four.fb2')
        character={'id':'gundahar','name':'Гундахар'};generation=self.generation(book,[character])
        report=self.formal_report(book,generation,'Гундахаром','Гунда́харом')
        result=apply_pronunciations(self.store,book,[character],{'names':{'gundahar':{'name':'Гундахар','stressed':'Гу́ндахар','source':'model'}}})
        self.assertEqual('Гунда́хар',result['names']['gundahar']['stressed'])
        from rimsky.listener_stress import notes
        rows=notes(self.store,book)
        self.assertEqual(1,len(rows));self.assertEqual(report['id'],rows[0]['report']['id'])
        self.assertEqual('stress',rows[0]['report']['kind'])

    def test_legacy_family_migration_uses_own_retained_marks_not_latest_projection(self):
        book=self.store.import_data(fb2('Synthetic IV',4,'Он говорил с Гундахаром.'),'four.fb2')
        character={'id':'gundahar','name':'Гундахар'}
        evidence=[{'report_id':'old','book_id':'deleted','generation_id':'old-generation','selected_text':'Гундахар',
            'intended_stress':'Гу́ндахар','created':'2026-10-20','source':'listener'},
            {'report_id':'new','book_id':'deleted','generation_id':'old-generation','selected_text':'Гундахар',
            'intended_stress':'Гунда́хар','created':'2026-10-21','source':'listener'}]
        upsert_character(self.store,identity(self.store,book),character,state={'pronunciation_source':'listener',
            'pronunciations':{'гундахар':'Гу́ндахар'},'pronunciation_evidence':evidence})
        result=apply_pronunciations(self.store,book,[character],{})
        self.assertEqual('Гунда́хар',result['names']['gundahar']['stressed'])
        from rimsky.listener_stress import notes
        rows=notes(self.store,book)
        self.assertEqual(['Гу́ндахар','Гунда́хар'],[r['family']['гундахар'] for r in rows])
        self.assertEqual(evidence,[r['report']['legacy_evidence'] for r in rows])

    def test_lowercase_context_note_cannot_become_a_cast_name_rule(self):
        book = self.store.import_data(fb2('Synthetic IV', 4, 'Он не видел мира.'), 'context.fb2')
        generation = self.generation(book, [{'id': 'mira', 'name': 'Мира'}])
        report = {'id': 'ordinary', 'book_id': book['id'], 'generation_id': generation['id'],
                  'selected_text': 'мира', 'intended_stress': 'ми́ра', 'created': '2026-10-24T00:00:00Z'}
        self.assertEqual('not_character', record_listener_stress(self.store, report)['status'])
        self.assertFalse(any(json.loads(r['state'] or '{}').get('pronunciations')
                             for r in self.store.rows('SELECT state FROM series_character_state')))

    def test_sentence_initial_common_word_cannot_become_a_cast_name_rule(self):
        text = 'Стоит человек.'
        book = self.store.import_data(fb2('Synthetic IV', 4, text), 'context.fb2')
        generation = self.generation(book, [{'id': 'stoit', 'name': 'Стоит'}])
        report = {'id': 'ordinary', 'book_id': book['id'], 'generation_id': generation['id'],
                  'selected_text': 'Стоит', 'intended_stress': 'Стои́т', 'text_offset': 0,
                  'passage_text': text, 'created': '2026-10-24T00:00:00Z'}
        self.assertEqual('not_character', record_listener_stress(self.store, report)['status'])

    def test_propagation_includes_inflected_forms_and_groups_one_retake_per_segment(self):
        state = {'status': 'updated', 'pronunciations': {'гундахар': 'Гунда́хар'}}
        generation = {'segments': [
            {'id': 'one', 'chapter_id': 'c1', 'source_id': 'p1', 'start': 10, 'ordinal': 1,
             'text': 'Гундахар позвал Гундахара.', 'spoken': 'Гундахар позвал Гундахара.',
             'status': 'ready', 'audio_hash': 'a' * 64},
            {'id': 'two', 'chapter_id': 'c1', 'source_id': 'p2', 'start': 40, 'ordinal': 2,
             'text': 'Обычное слово.', 'spoken': 'Обычное слово.', 'status': 'ready', 'audio_hash': 'b' * 64},
        ]}
        targets = propagation_targets(generation, state)
        self.assertEqual(1, len(targets))
        self.assertEqual(['Гунда́хар', 'Гунда́хара'],
                         [row['intended_stress'] for row in targets[0]['corrections']])

    def test_compound_character_component_binds_an_inflected_ui_selection(self):
        book = self.store.import_data(fb2('Элирм IV', 4, 'Генерал посмотрел Ар-Хакону в глаза.'), 'four.fb2')
        generation = self.generation(book, [
            {'id': 'ar_hakon', 'name': 'Ар-Хакон', 'aliases': [], 'voice_id': 'masculine_4'},
        ])
        promoted = record_listener_stress(self.store, {
            'id': 'h' * 24, 'book_id': book['id'], 'generation_id': generation['id'],
            'selected_text': 'Хакону', 'intended_stress': 'Хако́ну',
            'created': '2026-10-21T00:00:00Z',
        })
        self.assertEqual('updated', promoted['status'])
        self.assertEqual('Ар-Хакон', promoted['canonical_name'])
        self.assertEqual('Хако́н', promoted['pronunciations']['хакон'])


if __name__ == '__main__':
    unittest.main()
