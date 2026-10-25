"""Source coverage permits omitted whole nonlexical blocks, never omitted speech."""
import copy,sys,unittest
from pathlib import Path
ROOT=next(p for p in Path(__file__).resolve().parents if(p/'runtime/rimsky/store.py').is_file())
sys.path.insert(0,str(ROOT/'runtime'))
from rimsky.integrity import validate_coverage
from rimsky.util import AppError

class NonlexicalCoverageTests(unittest.TestCase):
    def setUp(self):
        self.book={'chapters':[{'id':'c1','blocks':[{'id':'a','text':'First word.','kind':'paragraph'},
            {'id':'pause','text':'\u2026','kind':'paragraph'},{'id':'b','text':'Last word.','kind':'paragraph'}]}]}
    def segment(self,key,start=0,end=None):
        text=next(b['text']for b in self.book['chapters'][0]['blocks']if b['id']==key)
        end=len(text)if end is None else end
        return {'source_id':key,'chapter_id':'c1','start':start,'end':end,'text':text[start:end],'spoken':text[start:end]}
    def check(self,parts):validate_coverage(self.book,['c1'],parts)
    def test_entire_nonlexical_block_can_be_absent_without_mutation(self):
        parts=[self.segment('a'),self.segment('b')]; before=copy.deepcopy((self.book,parts))
        self.check(parts);self.assertEqual(before,(self.book,parts))
    def test_rendered_legacy_punctuation_remains_valid(self):
        self.check([self.segment('a'),self.segment('pause'),self.segment('b')])
    def test_partial_nonlexical_block_remains_invalid(self):
        self.book['chapters'][0]['blocks'][1]['text']='\u2026 \u2026'
        with self.assertRaisesRegex(AppError,'every expected source'):
            self.check([self.segment('a'),self.segment('pause',end=1),self.segment('b')])
    def test_absent_mixed_or_audible_block_stays_invalid(self):
        for text in ('... word ...','123','\u0441\u043b\u043e\u0432\u043e','\u2163'):
            self.book['chapters'][0]['blocks'][1]['text']=text
            with self.subTest(text=text),self.assertRaisesRegex(AppError,'every expected source'):
                self.check([self.segment('a'),self.segment('b')])
    def test_absent_standalone_symbols_remain_invalid(self):
        for text in ('$', '+', '\U0001f514', '\u00a3', '... + ...'):
            self.book['chapters'][0]['blocks'][1]['text']=text
            with self.subTest(text=text),self.assertRaisesRegex(AppError,'every expected source'):
                self.check([self.segment('a'),self.segment('b')])
    def test_entirely_uncovered_punctuation_only_chapter_is_invalid(self):
        self.book['chapters'][0]['blocks']=[self.book['chapters'][0]['blocks'][1]]
        with self.assertRaisesRegex(AppError,'every expected source'):self.check([])
        self.check([self.segment('pause')])
    def test_missing_other_audible_passage_stays_invalid(self):
        with self.assertRaisesRegex(AppError,'every expected source'):self.check([self.segment('a')])
    def test_reorder_and_duplicate_stay_invalid(self):
        for parts in ([self.segment('b'),self.segment('a')],
                      [self.segment('a'),self.segment('a'),self.segment('b')],
                      [self.segment('a'),self.segment('b'),self.segment('pause')]):
            with self.subTest(parts=parts),self.assertRaises(AppError):self.check(parts)
    def test_changed_slice_wrong_chapter_and_unexpected_source_stay_invalid(self):
        for field,value in (('text','changed'),('start',1),('chapter_id','foreign'),('source_id','foreign')):
            parts=[self.segment('a'),self.segment('b')];parts[0][field]=value
            with self.subTest(field=field),self.assertRaises(AppError):self.check(parts)
    def test_missing_whitespace_block_and_exact_legacy_split(self):
        self.book['chapters'][0]['blocks'][1]['text']='  '
        self.check([self.segment('a'),self.segment('b')])
        # Existing nonblank punctuation slices must still cover their exact entire source.
        self.book['chapters'][0]['blocks'][1]['text']='!!'
        self.check([self.segment('a'),self.segment('pause',end=1),self.segment('pause',start=1),self.segment('b')])

if __name__=='__main__':unittest.main()
