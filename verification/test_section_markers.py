import sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'runtime'),str(ROOT/'scripts')]
from rimsky import section_markers as sm
from rimsky.narration_transitions import structure,gap_seconds,lead_seconds,source_boundaries,windows

BLOCKS=[{'id':'h','kind':'heading','text':'Глава 3'},
        {'id':'p1','kind':'paragraph','text':'Скрип колес.'},
        {'id':'m','kind':'paragraph','text':'За шесть часов до этого…','formatting':{'full':['strong']}},
        {'id':'ui','kind':'paragraph','text':'Получено: 15 000 золотых','formatting':{'full':['strong']}},
        {'id':'d','kind':'paragraph','text':'— Стой!','formatting':{'full':['strong']}},
        {'id':'p2','kind':'paragraph','text':'Дальше.'}]
EDGE={'trailing_padding':.05,'leading_padding':.05}


class SectionMarkerTests(unittest.TestCase):
    def test_candidates_come_from_markup_but_only_consensus_makes_a_section(self):
        self.assertEqual([r['id'] for r in sm.candidates(BLOCKS)],['m','ui'])  # dialogue and the title are not candidates
        self.assertEqual(structure(BLOCKS),{'h':'title'})
        self.assertEqual(structure(BLOCKS,['m']),{'h':'title','m':'section'})
        self.assertEqual(sm.consensus({'m':'scene_transition','ui':'scene_transition'},{'m':'scene_transition','ui':'not_transition'}),['m'])

    def test_structural_gaps_and_title_lead(self):
        row=lambda i,s=None:{'source_id':i,'structure':s}
        self.assertAlmostEqual(gap_seconds(row('p1'),row('m','section'),EDGE,EDGE),2.4)
        self.assertAlmostEqual(gap_seconds(row('m','section'),row('p2'),EDGE,EDGE),1.4)
        self.assertAlmostEqual(gap_seconds(row('h','title'),row('p1'),EDGE,EDGE),2.9)
        self.assertEqual(lead_seconds(row('h','title')),1.5)
        self.assertEqual(lead_seconds(row('p1')),0.)

    def test_music_windows_cover_the_title_lead_and_both_sides_of_a_section(self):
        rows=[{'id':'u0','source_id':'h','start':1.5,'end':2.5},{'id':'u1','source_id':'p1','start':5.5,'end':7},
              {'id':'u2','source_id':'m','start':9.5,'end':11},{'id':'u3','source_id':'p2','start':12.5,'end':14}]
        kinds=[(w['kind'],w['start'],w['end']) for w in windows(rows,source_boundaries(rows,BLOCKS,['m']),14)]
        self.assertEqual(kinds,[('title',0.,1.5),('section',2.5,5.5),('section',7,9.5),('section',11,12.5)])

    def test_validator_requires_full_coverage(self):
        req=sm.request(sm.candidates(BLOCKS))
        with self.assertRaises(ValueError):sm.validate({'markers':[{'block_id':'m','role':'scene_transition','basis':''}]},req)


if __name__=='__main__':unittest.main()
