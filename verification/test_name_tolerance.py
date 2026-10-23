import sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'runtime')]
from rimsky.name_tolerance import tolerance,proper_nouns,RULE,INTERJECTION_RULE,interjection_tolerance,leading_interjection_tolerance
from rimsky.speech_quality import transcript_check,spoken_word_tokens as tokens
from rimsky.util import file_digest,write_json


def skipped(source,heard):
    return bool(tolerance(tokens(source),tokens(heard),source)[1])


class ProperNounSlotTests(unittest.TestCase):
    def test_proper_nouns_are_capitalized_mid_sentence_or_unknown_at_the_start(self):
        found=proper_nouns('Гундахар кивнул. Он видел Ар-Хакона и Великих Подземелий. Потом косплеит. Она ушла.')
        self.assertTrue({'гундахар','ар','хакона','великих','подземелий'}<=found)
        self.assertIn('косплеит',found)      # invented/slang words: recognizers cannot spell them (iteration 3)
        self.assertNotIn('она',found)        # ordinary sentence-initial word

    def test_name_slot_is_not_spelling_checked(self):
        self.assertTrue(skipped('Это сказал Мозес вчера.','Это сказал Моисей вчера.'))
        self.assertTrue(skipped('Гундахар кивнул.','Гундакар кивнул.'))
        self.assertTrue(skipped('Ар-Хакон пояснил.','Архакон пояснил.'))

    def test_guards_keep_missing_or_replaced_names_and_other_words_flagged(self):
        self.assertFalse(skipped('Сказал Гундахар.','Сказал.'))                          # swallowed name
        self.assertFalse(skipped('Сказал Гундахар.','Сказал один большой старый друг.'))  # long replacement
        self.assertFalse(skipped('практически проорал Глас','практически прорвал глаз')) # ordinary verb differs
        self.assertTrue(skipped('Он косплеит лакея.','Он касплает лакея.'))
        self.assertTrue(skipped('игв блаженно зажмурился','Игвы блаженно зажмурился'))
        self.assertTrue(skipped('ФС-С-С!','FSSS.'))
        self.assertFalse(skipped('ФС-С-С!','Ха-ха.'))

    def test_verifier_discloses_the_skipped_name(self):
        from higgs_secondary_verification import apply_secondary
        source='Прошептал Гундахар.'
        with tempfile.TemporaryDirectory() as folder:
            wav=Path(folder)/'take.wav';wav.write_bytes(b'audio');sha=file_digest(wav)
            primary={**transcript_check(source,'Прошептал Гундакар.',sha,'primary'),'synthesis_key':'k'}
            selected={'line':{'path':str(wav),'unit':{'spoken':source},'receipt':{'audio_sha256':sha},'word_check':primary}}
            path=Path(folder)/'w'/'report.json'
            write_json(path,{'verifier':{'model':'w','language':'ru'},'checks':{'a':{**transcript_check(source,'Прошептал Гундакар.',sha,'w'),
                'unit_id':'line','synthesis_key':'k'}}})
            check=apply_secondary(selected,[path])['line']['word_check']
            self.assertEqual(check['status'],'asr_exact_match')
            self.assertEqual(check['tolerated_deviations'][0]['rule'],RULE)
            self.assertEqual(check['heard_raw'],'Прошептал Гундакар.')


    def test_interjection_lines_accept_the_same_kind_only(self):
        for source,heard in [('Угу.','Ммм.'),('Хо-хо-хо!','Ха-ха-ха!'),('У-у-у…','Оу.')]:
            self.assertTrue(skipped(source,heard),(source,heard))
        for source,heard in [('Мда.','Да.'),('Ага','А где'),('Угу, сказал он.','Ммм, сказал он.'),('Хо-хо-хо!','Ха.')]:
            self.assertFalse(skipped(source,heard),(source,heard))


    def test_leading_hesitation_of_the_same_kind_with_the_rest_exact(self):
        self.assertTrue(skipped('Хм-м-м, ну ладно.','Мм, ну ладно.'))
        self.assertTrue(skipped('М-м-м… двенадцать?','Хм, двенадцать?'))
        self.assertFalse(skipped('Эм-м, а нахрена?','м а зачем'))     # the rest differs
        self.assertFalse(skipped('Эм, привет','привет'))               # swallowed, not varied
        self.assertFalse(skipped('Ну, привет','Но, привет'))           # ну is a word, not a hesitation

    def test_gm_hum_uses_existing_disclosed_whole_line_rule(self):
        for heard in ('Мм.','Мммм.','Угу.'):
            with self.subTest(heard=heard):
                corrected,changes = tolerance(tokens('— Гм!..'),tokens(heard),'— Гм!..')
                self.assertEqual(corrected,['гм'])
                self.assertEqual(len(changes),1)
                self.assertEqual(changes[0]['rule'],INTERJECTION_RULE)
                self.assertEqual(changes[0]['source_tokens'],['гм'])
                self.assertEqual(changes[0]['heard_tokens'],tokens(heard))
                self.assertEqual(changes[0]['kind'],'interjection_of_the_same_kind')
                self.assertTrue(changes[0]['user_instructions'])

    def test_gm_leading_hum_requires_rest_to_match(self):
        result = leading_interjection_tolerance(tokens('Гм, ну ладно.'),tokens('Мм, ну ладно.'))
        self.assertIsNotNone(result)
        corrected,changes = result
        self.assertEqual(corrected,tokens('Гм, ну ладно.'))
        self.assertEqual(changes[0]['rule'],INTERJECTION_RULE)
        self.assertEqual(changes[0]['kind'],'leading_interjection_of_the_same_kind')
        for heard in ('Мм, ну поздно.','Ну ладно.','Мм, ну ладно завтра.','Ха, ну ладно.','Ррр, ну ладно.'):
            with self.subTest(heard=heard):
                self.assertIsNone(leading_interjection_tolerance(tokens('Гм, ну ладно.'),tokens(heard)))

    def test_gm_does_not_cover_empty_content_words_laugh_or_growl(self):
        for heard in ('','ладно','ха-ха','ррр','Mmm'):
            with self.subTest(heard=heard):
                self.assertIsNone(interjection_tolerance(tokens('Гм!..'),tokens(heard)))


if __name__=='__main__':unittest.main()
