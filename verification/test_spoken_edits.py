import sys
import unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'runtime'))
from rimsky.spoken_edits import apply_spoken_edit


class SpokenEditTests(unittest.TestCase):
    def test_clipped_fragment_recipe_only_adds_terminal_punctuation(self):
        spoken='Гундахар пересек рубку, распихав мешающих пройти'
        plan={'expected_spoken':spoken,'native_text':spoken+',','recovery_kind':'edge_fragment',
            'evidence':{'source':'unsafe_generated_audio_edge','takes':[{'audio_sha256':'bound'}]}}
        self.assertEqual(apply_spoken_edit(spoken,plan),(spoken+',',spoken+','))
        for changed in (spoken+' быстро,',spoken.replace('рубку','комнату')+','):
            with self.assertRaises(ValueError):apply_spoken_edit(spoken,{**plan,'native_text':changed})
        with self.assertRaises(ValueError):apply_spoken_edit(spoken,{**plan,'evidence':{'source':'guess'}})

    def test_source_digits_preserve_values_and_all_other_words(self):
        from rimsky.spoken_numbers import expand
        from rimsky.util import digest
        native='Человек 162-го уровня. Версия 21А'
        spoken=expand(native)
        plan={'expected_spoken':spoken,'native_text':native,'recovery_kind':'source_number_surface',
              'evidence':{'native_surface_sha256':digest(native.encode('utf8'))}}
        self.assertEqual(apply_spoken_edit(spoken,plan),(native,spoken))
        for changed in ('Человек 163-го уровня. Версия 21А','Человек 162-го уровня. Другая версия 21А'):
            with self.assertRaisesRegex(ValueError,'source value'):
                apply_spoken_edit(spoken,{**plan,'native_text':changed,
                    'evidence':{'native_surface_sha256':digest(changed.encode('utf8'))}})
        with self.assertRaisesRegex(ValueError,'source value'):
            apply_spoken_edit(spoken,{**plan,'evidence':{'native_surface_sha256':'stale'}})

    def test_approved_reading_recipe_survives_new_take_without_altering_expected_words(self):
        for before,native,expected in [
            ('Сил моих больше нет, это терпеть.','Сил моих больше нет это терпеть.','Сил моих больше нет это терпеть.'),
            ('Кровью, потом.','Кровью, по́том.','Кровью, потом.'),
            ('Ай, всё','Ай, всё.','Ай, всё.')]:
            plan={'expected_spoken':before,'native_text':native,'evidence':{'source':'bound correction'}}
            self.assertEqual(apply_spoken_edit(before,plan),(native,expected))

    def test_stale_source_and_word_changes_are_refused(self):
        plan={'expected_spoken':'Ай, всё','native_text':'Ай, всё.','evidence':{'source':'bound correction'}}
        with self.assertRaisesRegex(ValueError,'different source'):apply_spoken_edit('Ой, всё',plan)
        for text in ['Ай.', 'Ой, всё.', 'Ай, уже всё.']:
            with self.assertRaisesRegex(ValueError,'Unsupported'):
                apply_spoken_edit('Ай, всё',{**plan,'native_text':text})
        with self.assertRaisesRegex(ValueError,'unbound'):
            apply_spoken_edit('Ай, всё',{**plan,'evidence':None})

    def test_listener_stress_preserves_prior_native_yo_marks_and_fragment_punctuation(self):
        spoken='Гундахар пересек рубку, распихав мешающих пройти'
        previous='Гу́ндахар пересёк рубку, распиха́в мешающих пройти.'
        native='Гундаха́р пересёк рубку, распиха́в мешающих пройти.'
        plan={'expected_spoken':spoken,'native_text':native,
              'previous_native_text':previous,
              'recovery_kind':'listener_pronunciation_from_synthesis',
              'evidence':{'source':'explicit_listener_instruction'}}
        self.assertEqual((native,spoken),apply_spoken_edit(spoken,plan))
        with self.assertRaisesRegex(ValueError,'prior native wording'):
            apply_spoken_edit(spoken,{**plan,'native_text':native.replace('рубку','каюту')})


if __name__=='__main__':unittest.main()


class DelegatedEditTests(unittest.TestCase):
    EVIDENCE={'source':'delegated_editorial_decision','decided_by':'claude-opus-5-5',
              'user_instruction':'Use your own judgement','rationale':'test'}

    def plan(self,spoken,native,kind='delegated_respelling',**extra):
        return {'expected_spoken':spoken,'native_text':native,'recovery_kind':kind,'evidence':dict(self.EVIDENCE),**extra}

    def test_respelling_changes_only_what_higgs_reads(self):
        from rimsky.spoken_edits import apply_spoken_edit
        spoken='Надо было передать Гласу список деталей.'
        native='Надо было передать Гла́ссу список деталей.'
        self.assertEqual(apply_spoken_edit(spoken,self.plan(spoken,native)),(native,spoken))

    def test_source_correction_is_verified_against_its_declared_target(self):
        from rimsky.spoken_edits import apply_spoken_edit
        spoken='дополнительные адамантивые пластины';target='дополнительные адамантиевые пластины'
        self.assertEqual(apply_spoken_edit(spoken,self.plan(spoken,target,'delegated_source_correction',target_spoken=target)),(target,target))

    def test_delegated_edits_need_evidence_and_stay_small(self):
        from rimsky.spoken_edits import apply_spoken_edit
        spoken='Надо было передать Гласу список деталей.'
        for native,evidence in [('Надо было передать Глассу список деталей.',{}),
                                ('Надо было отдать Петру список деталей.',self.EVIDENCE),
                                ('Надо передать Гласу список деталей.',self.EVIDENCE)]:
            with self.subTest(native=native),self.assertRaises(ValueError):
                apply_spoken_edit(spoken,{**self.plan(spoken,native),'evidence':evidence})
