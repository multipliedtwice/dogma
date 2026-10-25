"""Full chapter gates must prove source ranges, not an inherited unit count."""
import copy,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'runtime')]
import debug_higgs_one_shot as d
import repair_debug_listener_stress as repair


class SourceCoverage(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)

    def fixture(self):
        blocks=[{'id':'p1','kind':'paragraph','text':'First sentence.','html':'<p>First sentence.</p>'},
            {'id':'scene','kind':'separator','text':'* * *','html':'<h3>* * *</h3>'},
            {'id':'pause','kind':'paragraph','text':'…','html':'<p>…</p>'},
            {'id':'p2','kind':'paragraph','text':'Last sentence.','html':'<p>Last sentence.</p>'}]
        book={'chapters':[{'id':'c','blocks':blocks}]}
        units=[{'id':'u1','chapter_id':'c','source_id':'p1','start':0,'end':15,'text':'First sentence.'},
            {'id':'u2','chapter_id':'c','source_id':'p2','start':0,'end':14,'text':'Last sentence.'}]
        return book,units

    def test_existing_incomplete_chunk_fails(self):
        (self.root/'c/chunk-000').mkdir(parents=True);d.write(self.root/'c/chunk-000/assignment.json',{'units':[]})
        (self.root/'c/chunk-001/direction').mkdir(parents=True)
        with self.assertRaisesRegex(ValueError,'incomplete chunk'):d.canonical_assignments(self.root,'c')

    def test_trailing_paragraph_cannot_be_complete(self):
        book,units=self.fixture()
        with self.assertRaisesRegex(ValueError,'p2'):d.source_coverage(book,units[:1],['c'])

    def test_old_ready_fast_return_cannot_bypass_missing_tail(self):
        book,units=self.fixture();source=self.root/'original';out=self.root/'prepared'
        d.write(self.root/'source/book.json',book);d.write(out/'chunk/render-plan.json',{'units':units[:1]})
        d.write(out/'ready.json',{'source':str(source),'chapters':['c'],
            'source_sha256':d.file_digest(self.root/'source/book.json'),
            'plans':[{'plan':'chunk/render-plan.json','plan_sha256':d.file_digest(out/'chunk/render-plan.json')}]})
        with patch.object(d,'native_batch')as native,patch.object(d.subprocess,'run')as provider:
            with self.assertRaisesRegex(ValueError,'p2'):d.prepare(source,out,['c'])
            native.assert_not_called();provider.assert_not_called()

    def test_structural_nonlexical_exclusions_have_source_evidence(self):
        book,units=self.fixture();result=d.source_coverage(book,units,['c'])
        self.assertTrue(result['complete']);self.assertEqual(len(result['excluded_ranges']),2)
        for row in result['excluded_ranges']:
            self.assertIn(row['rule'],('canonical-nonnarratable-source-v1','source-nonlexical-punctuation-v1'))
            self.assertTrue(row['source_passage_sha256']);self.assertTrue(row['source_markup_sha256'])

    def test_progressive_prefix_is_explicitly_incomplete(self):
        book,units=self.fixture();result=d.source_coverage(book,units[:1],['c'],allow_partial=True)
        self.assertFalse(result['complete']);self.assertEqual(result['missing_ranges'][0]['source_id'],'p2')

    def test_mid_paragraph_omission_fails_even_when_block_has_unit(self):
        book,units=self.fixture();units[0].update(end=5,text='First')
        with self.assertRaisesRegex(ValueError,'p1'):d.source_coverage(book,units,['c'])

    def test_source_word_in_narratable_paragraph_cannot_be_hidden(self):
        book,units=self.fixture();book['chapters'][0]['blocks'][1].update(kind='paragraph',text='Missing speech')
        with self.assertRaisesRegex(ValueError,'scene'):d.source_coverage(book,units,['c'])

    def test_canonical_note_figure_separator_and_explicit_exclusion(self):
        book,units=self.fixture()
        for kind in ('note','figure','separator'):
            book['chapters'][0]['blocks'][1].update(kind=kind,text='Explicit parser-excluded prose')
            result=d.source_coverage(book,units,['c']);self.assertTrue(result['complete'])
            self.assertEqual(result['excluded_ranges'][0]['rule'],'canonical-nonnarratable-source-v1')
            self.assertEqual(result['excluded_ranges'][0]['source_kind'],kind)
        book['chapters'][0]['blocks'][1].update(kind='paragraph',exclude_from_narration=True)
        result=d.source_coverage(book,units,['c']);self.assertTrue(result['complete'])
        self.assertTrue(result['excluded_ranges'][0]['explicit_exclusion'])

    def test_source_symbol_requires_explicit_spoken_decision(self):
        book,units=self.fixture();book['chapters'][0]['blocks'][2].update(text='+')
        with self.assertRaisesRegex(ValueError,'pause'):d.source_coverage(book,units,['c'])

    def test_overlapping_spoken_source_rejected(self):
        book,units=self.fixture();units.append({**units[0],'id':'duplicate'})
        with self.assertRaisesRegex(ValueError,'Overlapping'):d.source_coverage(book,units,['c'])

    def test_derived_materialization_carries_pinned_source_coverage(self):
        base=self.root/'base';out=self.root/'derived';book,units=self.fixture()
        coverage=d.source_coverage(book,units,['c']);d.write(base/'source-coverage.json',coverage)
        source_sha=d.file_digest(base/'source-coverage.json')
        # This companion helper verifies a pinned optional file before copying;
        # a corrupted source receipt must not reach a derived generation.
        repair.copy_source_coverage(base,out,{'source_coverage_sha256':source_sha})
        self.assertEqual((out/'source-coverage.json').read_bytes(),(base/'source-coverage.json').read_bytes())
        d.write(base/'source-coverage.json',{'corrupt':True})
        with self.assertRaises(Exception):repair.copy_source_coverage(base,self.root/'other',{'source_coverage_sha256':source_sha})

if __name__=='__main__':unittest.main()
