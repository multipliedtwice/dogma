import sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'runtime'))
from rimsky.speech_quality import word_tokens
from rimsky.spoken_homophones import homophone_equivalence,homophone_span


def resolves(source,heard):
    corrected,changes=homophone_equivalence(word_tokens(source),word_tokens(heard))
    return corrected==word_tokens(source),changes


class RecognizerOrthographyTests(unittest.TestCase):
    def test_speech_identical_spellings_resolve_with_their_rule(self):
        for source,heard in [('не правда','неправда'),('боевые цеппелины','боевые цепелины'),
                             ('ямарайаху','ямара яху'),('рухлядь, что пугала','рухлять что пугало'),
                             ('стареешь ни по дням','стареешь не по дням'),('я в озеро ни ногой','я в озера не ногой'),
                             ('ответил Готэн Либен','ответил Готтенлибен'),('интерфейс NS-Eye','интерфейс NSI'),
                             ('Мэнелия Налима','Менелия Налима'),('Условие выполнения','условия выполнения'),
                             ('визит в хранилища Райза','визит в хранилище Райза')]:
            with self.subTest(source=source):
                ok,changes=resolves(source,heard)
                self.assertTrue(ok);self.assertTrue(changes)
                self.assertTrue(all(c['rule']=='russian-reduction-homophone-v1' for c in changes))

    def test_audible_differences_stay_flagged(self):
        for source,heard in [('было','была'),('могла','могло'),('слова','слово'),('его не было','его ни было'),
                             ('ваш акт агрессии','ваша агрессия'),('остановлюсь','останавливаюсь'),
                             ('Глас Эстир','Глаз Эстер'),('мэр','мер'),('в NS-Eye','в нси'),('статья','статье'),('моя','мое'),
                             ('премного благодарен','привет много благодарю'),('О-оу','О'),('у','')]:
            with self.subTest(source=source):
                self.assertFalse(resolves(source,heard)[0])

    def test_unchanged_words_are_not_reported_as_changes(self):
        self.assertEqual(resolves('Тихо стало.','тихо стало'),(True,[]))


    def test_explicit_synthesis_stress_replaces_espeak_guess_for_unknown_words(self):
        from rimsky.spoken_homophones import synthesis_stress
        stress=synthesis_stress('в Затоли́се, о́строва')
        self.assertEqual(stress,{'затолисе':2,'острова':0})
        self.assertIsNone(homophone_span(['затолисе'],['заталисе']))           # espeak guesses stress on о
        self.assertEqual(homophone_span(['затолисе'],['заталисе'],stress=stress)['kind'],'unstressed_reduction_marked_stress')
        self.assertIsNone(homophone_span(['затолисе'],['затолесе'],stress=stress))  # the marked vowel must stay
        self.assertIsNone(homophone_span(['острова'],['астрова'],stress=stress))    # dictionary words keep their rules

    def test_post_tonic_inflection_needs_same_stress_and_one_reduction_class(self):
        for source,heard in [('уходила','уходило'),('дороги','дороге'),('сказала','сказало')]:
            self.assertEqual(homophone_span([source],[heard])['kind'],'post_tonic_reduction',(source,heard))
        for source,heard in [('было','была'),        # stress moves (бы́ло / была́)
                             ('городом','городам'),  # stress moves (го́родом / города́м)
                             ('пребывает','прибывает'),  # differs before the stress
                             ('уходила','уходили')]:     # а and и are different reduced vowels
            self.assertIsNone(homophone_span([source],[heard]),(source,heard))

    def test_last_syllable_vowels_follow_their_real_reduction(self):
        for source,heard in [('изготовленная','изготовленное'),('новая','новое'),('ведущий','ведущей')]:
            self.assertEqual(homophone_span([source],[heard])['kind'],'post_tonic_reduction',(source,heard))
        for source,heard in [('шея','шеи'),('капля','капле'),('давит','давят'),('решением','решениям'),('счастье','счастья')]:
            self.assertIsNone(homophone_span([source],[heard]),(source,heard))

if __name__=='__main__':unittest.main()


class ElongationSpanTests(unittest.TestCase):
    def test_merged_or_split_stylized_sounds_compare_as_a_whole(self):
        from rimsky.speech_quality import spoken_word_tokens as t
        from rimsky.spoken_homophones import elongation_equivalence as el
        for source,heard,ok in [('О-оу!','Оу.',True),('О-оу!','О.',False),('Не-е-ет!','нет',True),
                                ('Ар-Хакон пришёл','архакон пришел',False),('Кто-то шёл','ктото шел',False)]:
            with self.subTest(source=source,heard=heard):
                self.assertEqual(el(source,t(source),t(heard))[0]==t(source),ok)
