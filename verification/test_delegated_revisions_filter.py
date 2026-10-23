import json,sys,tempfile,types,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'runtime'),str(ROOT/'scripts')]
import higgs_delegated_revisions as revisions
from rimsky.util import write_json

def row(kind='delegated_respelling',**extra):
    return {'kind':kind,'native_variants':['a','b'],'rationale':'r',**extra}

class DelegatedRevisionFilterTests(unittest.TestCase):
    def run_round(self,units,progress=None,bad=()):
        """Runs one round with stubbed rendering; returns (assignments written, jobs, output)."""
        with tempfile.TemporaryDirectory() as tmp:
            generation=Path(tmp)
            write_json(generation/'delegated-revisions.json',{'evidence':{'source':'t'},'units':units})
            if progress is not None:write_json(generation/'progress.json',progress)
            rendered=[]
            def render_candidates(jobs,*args):rendered.extend(jobs)
            def apply(spoken,plan):
                if plan['native_text'] in bad:raise ValueError('Unsupported or unbound spoken edit')
                return plan['native_text'],spoken
            mods={'render_higgs_palette_preview':types.SimpleNamespace(
                      retry_units=lambda us,attempt,groups:[dict(u) for u in us],spoken_text=lambda text,policy:text.upper()),
                  'higgs_exhausted_word_recovery':types.SimpleNamespace(render_candidates=render_candidates),
                  'render_higgs_book_trial':types.SimpleNamespace(render_many=None),
                  'regenerate_higgs_book_trial':types.SimpleNamespace(primary_audit_command=None,cached_audit=lambda t:{})}
            with patch.dict(sys.modules,mods),\
                 patch.object(revisions,'assignment_units',side_effect=lambda g,uid:({'frozen':1},{'id':uid,'text':'word'})),\
                 patch('rimsky.spoken_edits.apply_spoken_edit',side_effect=apply):
                base=revisions.run_round(generation,['c1','c2'],{},None,lambda:None,generation)
            written={}
            for path,take,_ in rendered:written.setdefault(take.name,[]).extend(u['id'] for u in json.loads(path.read_text('utf-8'))['units'])
            return base,written

    def test_complete_chapter_skipped(self):
        units={'c1-1':row(),'c2-1':row()}
        base,written=self.run_round(units,progress={'chapters':{'c1':{'complete':True}}})
        self.assertEqual(set(sum(written.values(),[])),{'c2-1'})
        base,written=self.run_round(units,progress={'chapters':{'c1':{'complete':True},'c2':{'complete':True}}})
        self.assertIsNone(base)
        base,written=self.run_round(units)
        self.assertEqual(set(sum(written.values(),[])),{'c1-1','c2-1'})

    def test_obsolete_source_correction_skipped(self):
        units={'c1-1':row('delegated_source_correction',target_spoken='WORD'),'c2-1':row()}
        _,written=self.run_round(units)
        self.assertEqual(set(sum(written.values(),[])),{'c2-1'})
        units['c1-1']['target_spoken']='OTHER'
        _,written=self.run_round(units)
        self.assertEqual(set(sum(written.values(),[])),{'c1-1','c2-1'})

    def test_invalid_variant_drops_only_that_variant(self):
        units={'c1-1':row(),'c2-1':row()}
        with patch.object(revisions,'print',create=True):
            _,written=self.run_round(units,bad=('b',))
        self.assertEqual(sorted(written),['v0-s0','v0-s1'])
        self.assertEqual(written['v0-s0'],['c1-1','c2-1'])

if __name__=='__main__':unittest.main()
