import sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'runtime')]
from higgs_spoken_positions import positions
from render_higgs_palette_preview import phrasing_retry
from rimsky.speech_quality import spoken_word_tokens,transcript_check,word_tokens


class SpokenOffsetTests(unittest.TestCase):
    def test_repetitions_and_stress_keep_source_offsets_without_joining_compounds(self):
        for text in ['М-м-м… двенадцать?','У-у-у…','Призна́ться, кто-то шёл.','Ай-ай! Да-а-а.','1-1, I-I-I','Да́-а?']:
            with self.subTest(text=text):
                units=positions(text)
                self.assertEqual([row[0] for row in units],spoken_word_tokens(text))
                for word,start,end in units:self.assertEqual(spoken_word_tokens(text[start:end]),[word])
        self.assertEqual(positions('М-м-м… двенадцать?'),[('м',0,5),('двенадцать',7,17)])

    def test_missing_filler_targets_its_own_offset_and_never_changes_words(self):
        spoken='Сейчас м-м-м двенадцать человек.'
        check={**transcript_check(spoken,'Сейчас двенадцать человек.','audio','verifier',normalize_elongations=True),
            'synthesis_key':'key'}
        unit={'id':'line','text':spoken};selected={'line':{'unit':{'spoken':spoken},'word_check':check}}
        plans=phrasing_retry([unit],selected,0,None)
        self.assertEqual(len(plans),1)
        plan=plans[0]['spoken_text_plan'];self.assertEqual(plan['native_text'],'Сейчас, м-м-м двенадцать человек.')
        self.assertEqual(word_tokens(plan['native_text']),word_tokens(spoken))
        self.assertEqual(check['status'],'spoken_text_mismatch')

    def test_hyphen_joined_part_never_receives_a_comma_cue(self):
        # c0003-u0833: 'О-, оу!' crashed the worker in apply_spoken_edit.
        from rimsky.spoken_edits import apply_spoken_edit
        spoken='О-оу!'
        check={**transcript_check(spoken,'О!','audio','verifier',normalize_elongations=True),'synthesis_key':'key'}
        unit={'id':'line','text':spoken};selected={'line':{'unit':{'spoken':spoken},'word_check':check}}
        for attempt in range(6):
            for row in phrasing_retry([unit],selected,attempt,None):
                apply_spoken_edit(spoken,row['spoken_text_plan'])


if __name__=='__main__':unittest.main()
