import copy
from pathlib import Path
import sys
import tempfile
import unittest
import numpy as np
import soundfile as sf
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'runtime'));sys.path.insert(0,str(ROOT/'scripts'))
from higgs_reviewed_take_overrides import validate_punctuation_take,apply_overrides
from rimsky.util import digest,file_digest,write_json
from rimsky.speech_quality import transcript_check


class ReviewedPunctuationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        wav=Path(self.temp.name)/'take.wav'
        audio=np.concatenate([np.zeros(1200),.05*np.sin(np.arange(2400)*.1),np.zeros(1200)])
        sf.write(wav,audio,24000,subtype='PCM_16')
        old={'text':'one, two.','prompt':'one, two.','effects_policy':'disabled','reference':'same','sampling':{'seed':7}}
        unit={'id':'sentence','chapter_id':'chapter','source_id':'paragraph','speaker':'actor',
              'voice':'family','render_voice':'family','mood':'calm','text':'one, two.','synthesis':old}
        self.before={'unit':unit};self.after={'unit':copy.deepcopy(unit),'path':str(wav)}
        self.after['unit']['synthesis'].update(text='one two.',prompt='one two.')
        self.after['unit']['spoken']='one two.'
        key=digest(self.after['unit']['synthesis']);hashed=file_digest(wav)
        self.after['receipt']={'key':key,'synthesis_sha256':key,'audio_sha256':hashed}
        self.after['word_check']={**transcript_check('one two.','one two.',hashed,'verifier'),'synthesis_key':key}

    def test_comma_changes_spoken_text_without_rewriting_source(self):
        result=validate_punctuation_take(self.before,self.after)
        self.assertFalse(result['unsafe_start']);self.assertFalse(result['unsafe_end'])
        self.assertEqual(self.after['unit']['text'],'one, two.')

    def test_other_sound_inputs_and_identity_cannot_change(self):
        for field,value in [('speaker','someone'),('mood','excited'),('text','edited source')]:
            altered=copy.deepcopy(self.after);altered['unit'][field]=value
            with self.assertRaisesRegex(ValueError,'identity/source'):validate_punctuation_take(self.before,altered)
        altered=copy.deepcopy(self.after);altered['unit']['synthesis']['sampling']['seed']=8
        with self.assertRaisesRegex(ValueError,'sound inputs'):validate_punctuation_take(self.before,altered)

    def test_words_and_audit_hash_cannot_change(self):
        altered=copy.deepcopy(self.after);altered['unit']['synthesis'].update(text='one three.',prompt='one three.')
        with self.assertRaisesRegex(ValueError,'comma removal'):validate_punctuation_take(self.before,altered)
        altered=copy.deepcopy(self.after);altered['word_check']['audio_sha256']='stale'
        with self.assertRaisesRegex(ValueError,'binding'):validate_punctuation_take(self.before,altered)

    def test_completion_period_preserves_fragment_words_and_original_source(self):
        before=copy.deepcopy(self.before);before['unit']['synthesis'].update(text='one, two',prompt='one, two')
        after=copy.deepcopy(self.after);after['unit']['synthesis'].update(text='one, two.',prompt='one, two.')
        after['unit']['spoken']='one, two.';key=digest(after['unit']['synthesis'])
        after['receipt'].update(key=key,synthesis_sha256=key)
        after['word_check']={**transcript_check(after['unit']['spoken'],'one two.',after['receipt']['audio_sha256'],'verifier'),'synthesis_key':key}
        validate_punctuation_take(before,after)

    def test_listener_stress_approval_retains_a_separate_asr_flag(self):
        before=copy.deepcopy(self.before);after=copy.deepcopy(self.after)
        for unit in [before['unit'],after['unit']]:unit['text']='потом и немытыми.'
        before['unit']['synthesis'].update(text='потом и немытыми.',prompt='потом и немытыми.')
        after['unit']['synthesis'].update(text='по́том и немытыми.',prompt='по́том и немытыми.')
        after['unit']['spoken']='потом и немытыми.';key=digest(after['unit']['synthesis'])
        after['receipt'].update(key=key,synthesis_sha256=key)
        after['word_check']={**transcript_check(after['unit']['spoken'],'потом и не мытыми.',after['receipt']['audio_sha256'],'verifier'),'synthesis_key':key}
        with self.assertRaisesRegex(ValueError,'binding'):validate_punctuation_take(before,after)
        validate_punctuation_take(before,after,listener_stress_approved=True)
        self.assertEqual(after['word_check']['status'],'spoken_text_mismatch')
        after['word_check']={**transcript_check(after['unit']['spoken'],'потом.',after['receipt']['audio_sha256'],'verifier'),'synthesis_key':key}
        with self.assertRaisesRegex(ValueError,'binding'):validate_punctuation_take(before,after,listener_stress_approved=True)

    def test_resume_override_is_bound_to_listener_answer_and_previous_take(self):
        folder=Path(self.temp.name)/'generation';folder.mkdir()
        answer=folder.parent/'answer.json'
        write_json(answer,{'source':'explicit_listener','verdict':'approved','unit_id':'sentence',
                           'audio_sha256':self.after['receipt']['audio_sha256']})
        write_json(folder/'manifest.json',{'source_sha256':'source'})
        self.before['receipt']={'audio_sha256':'previous','key':'previous-key'}
        record={'generation':'generation','source_sha256':'source','units':{'sentence':{
            'listener_answer':str(answer),'listener_answer_sha256':file_digest(answer),
            'previous_audio_sha256':'previous','previous_synthesis_key':'previous-key','replacement':self.after}}}
        write_json(folder/'reviewed-take-overrides.json',record)
        result=apply_overrides(folder,'chapter',[self.before])
        self.assertEqual(result[0]['unit']['spoken'],'one two.')
        self.assertEqual(self.before['unit']['text'],'one, two.')
        self.before['receipt']['audio_sha256']='different-take'
        with self.assertRaisesRegex(ValueError,'Original take changed'):apply_overrides(folder,'chapter',[self.before])
        self.before['receipt']['audio_sha256']='previous';answer.write_text('{}')
        with self.assertRaisesRegex(ValueError,'Listener answer changed'):apply_overrides(folder,'chapter',[self.before])

    def test_automatic_word_recovery_is_not_a_human_listening_verdict(self):
        folder=Path(self.temp.name)/'generation';folder.mkdir();answer=folder.parent/'recovery.json'
        write_json(answer,{'source':'automatic_word_recovery','verdict':'word_verified','unit_id':'sentence',
                           'audio_sha256':self.after['receipt']['audio_sha256']})
        write_json(folder/'manifest.json',{'source_sha256':'source'})
        self.before['receipt']={'audio_sha256':'previous','key':'previous-key'}
        self.before['word_check']={'status':'spoken_text_mismatch','expected_words':['one','two'],'heard_words':['one']}
        write_json(folder/'reviewed-take-overrides.json',{'generation':'generation','source_sha256':'source','units':{
            'sentence':{'listener_answer':str(answer),'listener_answer_sha256':file_digest(answer),
             'previous_audio_sha256':'previous','previous_synthesis_key':'previous-key','replacement':self.after}}})
        self.assertEqual(apply_overrides(folder,'chapter',[self.before])[0]['word_check']['status'],'asr_exact_match')
        self.before['word_check']['heard_words']=['one','two']
        with self.assertRaisesRegex(ValueError,'requires a bound original omission'):apply_overrides(folder,'chapter',[self.before])


if __name__=='__main__':unittest.main()
