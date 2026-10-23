import copy,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'runtime'))
from rimsky.speech_quality import transcript_check
from rimsky.word_verification import reconcile,verified


class WordVerificationTests(unittest.TestCase):
    def test_soft_final_devoicing_does_not_accept_missing_soft_sign_or_other_changes(self):
        for source,heard in [('рухлядь','рухлять'),('лошадь','лошать'),('грудь','груть')]:
            raw=transcript_check(source,heard,'a','v');audit=reconcile(source,raw,'Russian')
            self.assertTrue(verified(source,audit,'a','v','Russian'))
            self.assertEqual(audit['phonological_equivalences'][0]['rule'],
                'russian-word-final-soft-paired-consonant-devoicing-v1')
        for source,heard in [('дать','даль'),('рухлядь','рухлясь'),('рухлядь','рухлят'),
            ('рухлядь','рухляди'),('Лошадь ушла','Лошать ушло'),('Убрали рухлядь сегодня','Убрали рухлять'),
            ('Убрали рухлядь','Убрали новую рухлять')]:
            raw=transcript_check(source,heard,'a','v');audit=reconcile(source,raw,'Russian')
            self.assertFalse(verified(source,audit,'a','v','Russian'))

    def test_proper_name_spelling_boundary_has_recomputable_source_and_raw_proof(self):
        source='От кратера Аз-Лаар.';raw=transcript_check(source,'От кратера Азлаар.','a','v')
        audit=reconcile(source,raw,'Russian')
        self.assertTrue(verified(source,audit,'a','v','Russian'))
        self.assertEqual('Аз-Лаар',audit['proper_name_boundaries'][0]['source_name'])
        forged=copy.deepcopy(audit);forged['raw_verification']['heard']='От кратера Азлар.'
        self.assertFalse(verified(source,forged,'a','v','Russian'))

    def test_equivalent_spoken_form_has_recomputable_raw_proof(self):
        expected='Отправитель: Глас Эстир'
        raw=transcript_check(expected,'Отправитель, глаз Эстир.','audio','verifier')
        audit=reconcile(expected,raw,'Russian')
        self.assertTrue(verified(expected,audit,'audio','verifier','Russian'))
        self.assertEqual(audit['raw_verification'],raw);self.assertFalse(audit['human_approval'])
        self.assertFalse(verified(expected,audit,'other audio','verifier','Russian'))
        self.assertFalse(verified(expected,audit,'audio','other verifier','Russian'))
        self.assertFalse(verified(expected,audit,'audio','verifier','English'))
        forged=copy.deepcopy(audit);forged['raw_verification']['heard']='Отправитель.'
        self.assertFalse(verified(expected,forged,'audio','verifier','Russian'))

    def test_missing_words_and_vowels_never_become_verified(self):
        expected='Отправитель: Глас Эстир'
        for heard in ['Отправитель.','Отправитель Глас Эстер','Отправитель Глас и Стир','Неверные слова']:
            raw=transcript_check(expected,heard,'a','v');audit=reconcile(expected,raw,'Russian')
            self.assertFalse(verified(expected,audit,'a','v','Russian'))
            forged={**raw,'status':'asr_exact_match'}
            self.assertIsNone(reconcile(expected,forged,'Russian'))

    def test_original_exact_receipts_remain_valid_without_language_rewrite(self):
        raw=transcript_check('one two','one two','audio','verifier')
        self.assertTrue(verified('one two',raw,'audio','verifier',None))
        self.assertFalse(verified('one two',{**raw,'ignored_extra':'field'},'audio','verifier',None))


if __name__=='__main__':unittest.main()
