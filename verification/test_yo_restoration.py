import sys,unittest
from unittest.mock import patch
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'runtime'))
from rimsky import yo_restoration as yo
from rimsky.auto_pronunciation import auto_pronunciation,policy_options
from rimsky.util import digest

A='́'
NAMES={'vel':'Вел (Велнари'+A+'н)','erdamon_belar':'Эрдамо'+A+'н Бела'+A+'р'}
HOMOGRAPH={'rule':'homograph-stress-adjudication-v1','answer_sha256':'h','overrides':[]}
sha=lambda t:digest(t.encode('utf8'))
block=lambda decisions=(),**kw:{'rule':yo.RULE,'answer_sha256':'y'*64,'decisions':list(decisions),**kw}
v4=lambda text,decisions=(),names=NAMES:auto_pronunciation(text,names,[],**policy_options('stress-rare-v4',{'homograph_stress':HOMOGRAPH,'yo_restoration':block(decisions)}))
decision=lambda text,word,index,surface,choice,form:{'sha256':sha(text),'word':word,'index':index,'surface':surface,'yo':form,'choice':choice,'basis':'adjudicated'}
words=lambda t:__import__('re').findall('[А-Яа-яЁё]+',t)


class YoRestorationTests(unittest.TestCase):
    def test_classes_use_the_dictionary_only(self):
        self.assertEqual(yo.classify('мародерки'),('unique_yo','мародёрки'))
        self.assertEqual(yo.classify('конфеты'),('unique_e',None))
        self.assertEqual(yo.classify('все')[0],'ambiguous')
        self.assertEqual(yo.classify('осел')[0],'ambiguous')
        self.assertEqual(yo.classify('кроколиск')[0],'unknown')   # predictor guesses never count

    def test_unique_yo_is_restored_with_case(self):
        out,rec=v4('Он нес мешок. ЕЩЕ рано. Ее нет. Мародерки прибережем обличенное.')
        self.assertEqual(out,'Он нёс мешок. ЕЩЁ рано. Её нет. Мародёрки прибережём обличённое.')
        self.assertEqual(rec['policy'],'stress-rare-v4');self.assertEqual(rec['yo_restoration']['rule'],'yo-restoration-v1')
        self.assertEqual(len(rec['yo_restoration']['restored']),6);self.assertEqual(rec['yo_restoration']['versions']['pymorphy3'][:1],'2')
        self.assertEqual(rec['yo_restoration']['answer_sha256'],'y'*64)   # part of the synthesis key

    def test_no_acute_is_forced_on_a_missed_yo(self):   # v3 wrote мароде́рки and прибереже́м
        out,_=v4('Мародерки прибережем обличенное, к тебе серьезный вопрос.')
        self.assertNotIn(A,out);self.assertEqual(out,'Мародёрки прибережём обличённое, к тебе серьёзный вопрос.')

    def test_explicit_marked_e_becomes_yo_only_for_dictionary_certain_forms(self):
        text='изощре'+A+'нное забье'+A+'тся пове'+A+'л осе'+A+'л'
        normalized,record=yo.normalize_marked_e(text)
        self.assertEqual(normalized,'изощрённое забьётся повёл осе'+A+'л')
        self.assertEqual(record['rule'],yo.MARKED_E_RULE)
        self.assertEqual([e['printed'] for e in record['edits']],
                         ['изощре'+A+'нное','забье'+A+'тся','пове'+A+'л'])
        self.assertTrue(all(e['dictionary_class']=='unique_yo' for e in record['edits']))
        out,provenance=v4(text)
        self.assertEqual(out,'изощрённое забьётся повёл осе'+A+'л')
        self.assertEqual(provenance['yo_restoration']['marked_e_normalization'],record)

    def test_builder_witnesses_see_the_same_marked_e_normalization(self):
        from rimsky.auto_pronunciation import yo_witnesses
        text='изощре'+A+'нное и осе'+A+'л'
        with patch('rimsky.auto_pronunciation._silero',side_effect=lambda value,*a,**k:value):
            _,sparse,dense=yo_witnesses(text,{},[])
        self.assertEqual(sparse,['изощрённое','и','осе'+A+'л'])
        self.assertEqual(dense,sparse)

    def test_names_and_proper_nouns_are_never_yofied(self):
        out,_=v4('Вел ушел. Потом пришел Лева, а Сережа ушел.')
        self.assertTrue(out.startswith('Вел ушёл.'));self.assertIn('пришёл Л',out)
        self.assertIn('Сережа',out);self.assertNotIn('Серёжа',out)
        self.assertEqual(yo.eligible('Вел ушел.',[(0,3,'Вел')])[0]['word'],'ушел')

    def test_unbound_ambiguous_keeps_the_source_and_is_reported(self):
        text='Все расслаблено выдохнули. Туман осел над озером.'
        out,rec=v4(text)
        self.assertTrue(out.startswith('Все '));self.assertNotIn('Всё',out)
        self.assertEqual({(u['word'],u['reason']) for u in rec['yo_restoration']['unbound']},{('Все','no decision'),('осел','no decision')})
        self.assertNotIn('осе'+A,out)                      # silero's homograph mark on an undecided е is dropped
        self.assertEqual(rec['yo_restoration']['dropped_e_marks'][0]['was'],'осе'+A+'л')

    def test_bound_decisions_apply_and_other_texts_stay_unbound(self):
        text='Туман осел над озером.'
        d=decision(text,'осел',0,'осел','yo','осёл')
        out,rec=v4(text,[d]);self.assertIn('осёл',out);self.assertEqual(rec['yo_restoration']['decisions_applied'][0]['choice'],'yo')
        out,rec=v4(text,[decision(text,'осел',0,'осел','e','осёл')])
        self.assertIn('осе'+A+'л',out)                      # a decided е keeps silero's mark
        out,rec=v4('Туман осел.',[d]);self.assertEqual(out.count('ё'),0)
        self.assertEqual([u['reason'] for u in rec['yo_restoration']['unbound']],['no decision'])
        out,rec=v4(text,[{**d,'surface':'Осел'}])
        self.assertIn('surface changed',[u['reason'] for u in rec['yo_restoration']['unbound']])

    def test_drop_held_marks_spares_adjudicated_overrides(self):
        rec={'hold_starts':[3]}
        self.assertEqual(yo.drop_held_marks('Он осел.','Он осе'+A+'л.',rec)[0],'Он осел.')
        rec={'hold_starts':[3]}
        self.assertEqual(yo.drop_held_marks('Он осел.','Он осе'+A+'л.',rec,{('осел',0)})[0],'Он осе'+A+'л.')

    def test_v4_uses_the_same_homograph_witness_as_the_builder(self):
        # silero-stress unexpectedly drops this homograph mark when put_yo is
        # false.  The builder uses the normal pass, so v4 must use it too and
        # then restore the frozen ё layer's letters.
        text='Я связался с ней вчера и продал кое-какой материал.'
        override={'sha256':sha(text),'word':'продал','index':0,'choice':'ruaccent',
                  'silero':'прода'+A+'л','stress':2}
        homograph={**HOMOGRAPH,'overrides':[override]}
        out,rec=auto_pronunciation(
            text,NAMES,[],**policy_options('stress-rare-v4',{
                'homograph_stress':homograph,'yo_restoration':block()}))
        self.assertIn('про'+A+'дал',out)
        self.assertEqual(rec['homograph_overrides']['unbound'],[])
        self.assertEqual(rec['homograph_overrides']['applied'][0]['choice'],'ruaccent')

    def test_builder_accepts_agreement_and_sends_disagreement_to_adjudication(self):
        text='Туман осел.'
        out=yo.analyze(text,[],['Туман','осел'],['Туман','осе'+A+'л'],{('осел',0):'осел'})
        self.assertEqual([(d['choice'],d['basis']) for d in out['decisions']],[('e','auto-agree')]);self.assertEqual(out['rows'],[])
        out=yo.analyze(text,[],['Туман','осёл'],['Туман','осе'+A+'л'],{('осел',0):'осел'})
        self.assertEqual(len(out['rows']),1);self.assertEqual(out['rows'][0]['yo'],'осёл');self.assertEqual(out['decisions'],[])
        out=yo.analyze(text,[],['Туман','осел'],['Туман','осе'+A+'л'],None)   # RUAccent unavailable: never auto
        self.assertEqual(len(out['rows']),1)

    def test_vse_as_vsyo_needs_a_confirming_verb(self):   # silero+RUAccent were wrong 7/435 times without one
        out=yo.analyze('Он сделал все.',[],['Он','сделал','всё'],['Он','сделал','все'],{('все',0):'всё'})
        self.assertEqual(len(out['rows']),1);self.assertEqual(out['decisions'],[])
        out=yo.analyze('Все это казалось странным.',[],['Всё','это','казалось','странным'],['Все','это','казалось','странным'],{('все',0):'Всё'})
        self.assertEqual([d['choice'] for d in out['decisions']],['yo'])

    def test_vse_with_a_plural_verb_is_adjudicated_even_when_both_models_agree(self):
        text='Все расслаблено выдохнули.'
        self.assertEqual(yo.verb_number(text,3),'plural')
        out=yo.analyze(text,[],['Всё','расслаблено','выдохнули'],['Все','расслаблено','выдохнули'],{('все',0):'Всё'})
        self.assertEqual(len(out['rows']),1);self.assertEqual(out['rows'][0]['witnesses']['grammar'],'e')
        self.assertEqual(yo.verb_number('Все, что он сделал, было плохо.',3),None)   # clause ends before any verb
        self.assertEqual(yo.verb_number('Всё это казалось странным.',3),'singular')
        # plural verb and both models saying "все": accepted
        out=yo.analyze('Все ушли.',[],['Все','ушли'],['Все','ушли'],{('все',0):'Все'})
        self.assertEqual([d['choice'] for d in out['decisions']],['e'])

    def test_unknown_word_with_a_stressed_e_goes_to_adjudication(self):
        text='Он увидел зардевшийся горизонт.'
        out=yo.analyze(text,[],['Он','увидел','зардевшийся','горизонт'],['Он','увиде'+A+'л','зардевшийся','горизонт'],{})
        self.assertEqual([r['kind'] for r in out['rows'] if r['kind']=='unknown'],[]) # dictionary words are never "unknown"
        self.assertEqual(yo.classify('кроколиск')[0],'unknown')
        out=yo.analyze('Кроколиск ушел.',[],['Кроколиск','ушел'],['Кроколи'+A+'ск','ушел'],{})
        self.assertEqual(out['rows'],[])   # sentence-initial non-dictionary word is a proper noun
        out=yo.analyze('он кроколиске.',[],['он','кроколиске'],['он','кроколи'+A+'ске'],{})
        self.assertEqual(out['rows'],[])   # no е under stress -> nothing to decide
        out=yo.analyze('он зверолюбец.',[],['он','зверолюбец'],['он','зверолю'+A+'бец'],{})
        self.assertEqual(out['rows'],[])
        out=yo.analyze('он нечелеберный.',[],['он','нечелеберный'],['он','нечеле'+A+'берный'],{})
        self.assertEqual([r['yo'] for r in out['rows']],['нечелёберный'] if yo.classify('нечелеберный')[0]=='unknown' else [])

    def test_hyphenated_dictionary_words_are_restored_as_a_whole(self):   # v4 regressed все-таки against v3 until the whole token was classified
        self.assertEqual(yo.classify('все-таки'),('unique_yo','всё-таки'))
        self.assertEqual(yo.classify('ее-то'),('unique_yo','её-то'))
        self.assertEqual(yo.classify('тек-купол')[0],'unknown')   # HyphenatedWordsAnalyzer is a guess
        self.assertEqual(v4('Он все-таки ушел.')[0],'Он всё-таки ушёл.')
        self.assertEqual(v4('Я знал ее-то и еще-то.')[0],'Я знал её-то и ещё-то.')
        self.assertEqual(v4('Все-таки он ушел.')[0],'Всё-таки он ушёл.')                  # sentence start
        self.assertEqual(v4('ВСЕ-ТАКИ так.')[0],'ВСЁ-ТАКИ так.')                           # ALL CAPS
        self.assertEqual(v4('Он все‑таки ушел.')[0],'Он всё‑таки ушёл.')          # non-breaking hyphen
        self.assertEqual(v4('Шел-то он тихо.')[0],'Шёл-то он тихо.')

    def test_unknown_compounds_keep_short_parts_but_long_parts_are_classified(self):
        self.assertEqual(v4('Он вошел в тек-купол.')[0],'Он вошёл в тек-купол.')           # тек stays (series lexicon decides it)
        out=v4('Он вошел в мародерки-купол.')[0]
        self.assertEqual(out,'Он вошёл в мародёрки-купол.')                               # a long part is still restored alone

    def test_chem_nibud_is_not_changed_without_agreement(self):
        out,rec=v4('Он говорил о чем-нибудь.');self.assertIn('о чем-нибудь',out)
        self.assertEqual([u['reason'] for u in rec['yo_restoration']['unbound']],['no decision'])
        text='Он говорил о чем-нибудь.';parts=['Он','говорил','о','чём','нибудь'];dense=['Он','говори'+A+'л','о','чем','нибудь']
        out=yo.analyze(text,[],parts,dense,{('чем',0):'чем',('нибудь',0):'нибудь'})                         # silero ё, RUAccent е -> adjudication
        self.assertEqual((len(out['decisions']),len(out['rows'])),(0,1));self.assertEqual(out['rows'][0]['yo'],'чём-нибудь')
        out=yo.analyze(text,[],parts,dense,{('чем',0):'чём',('нибудь',0):'нибудь'})                         # both ё -> auto, bound to the whole token
        self.assertEqual([(d['word'],d['choice']) for d in out['decisions']],[('чем-нибудь','yo')])
        out,_=v4(text,[decision(text,'чем-нибудь',0,'чем-нибудь','yo','чём-нибудь')]);self.assertIn('о чём-нибудь',out)

    def test_close_mark_guard(self):
        guard=lambda t,r,spans=(),prot=frozenset():yo.close_mark_guard(t,r,list(spans),prot)
        self.assertEqual(guard('к тебе серьёзный','к тебе'+A+' серьёзный')[0],'к тебе серьёзный')
        self.assertEqual(guard('к тебе серьёзный','к тебе'+A+' серьёзный')[1][0]['dropped'],'тебе'+A)
        self.assertEqual(guard('тебе, серьёзный','тебе'+A+', серьёзный')[0],'тебе, серьёзный')        # comma between
        self.assertEqual(guard('тебе. Серьёзный','тебе'+A+'. Серьёзный')[0],'тебе'+A+'. Серьёзный')  # other sentence
        self.assertEqual(guard('к тебе'+A+' серьёзный','к тебе'+A+' серьёзный')[0],'к тебе'+A+' серьёзный')  # explicit input mark
        self.assertEqual(guard('тебе кра'+A+'сный','тебе кра'+A+'сный')[1],[])
        # two automatic marks: the one on the more frequent word goes
        self.assertEqual(guard('тебе красный','тебе'+A+' кра'+A+'сный')[0],'тебе кра'+A+'сный')
        # adjudicated override and name marks survive
        self.assertEqual(guard('тебе красный','тебе'+A+' серьёзный'.replace('серьёзный','кра'+A+'сный'),prot={('тебе',0)})[0],'тебе'+A+' красный')
        self.assertEqual(guard('Белару серьёзный','Бела'+A+'ру серьёзный',[(0,6,'Бела'+A+'ру')])[0],'Бела'+A+'ру серьёзный')
        self.assertEqual(guard('ушёл ещё','ушёл ещё')[1],[])

    def test_guard_end_to_end(self):
        out,rec=v4('Скажу, что к тебе серьезный вопрос.')
        self.assertIn('тебе серьёзный',out);self.assertNotIn(A,out.split('серьёзный')[0].rsplit(' ',1)[-1])
        self.assertEqual(rec['close_mark_guard']['rule'],'close-mark-guard-v1')

    def test_answer_validation_is_strict(self):
        rows=[{'surface':'осел','yo':'осёл','sentence':'Туман осел.'}]
        req=yo.request(rows);good={'decisions':[{'id':'y0000','choice':'yo','word':'осёл','basis':'x'}]}
        yo.validate(good,req)
        for bad in ({'decisions':[{'id':'y0000','choice':'yo','word':'осел','basis':'x'}]},
                    {'decisions':[{'id':'y0000','choice':'e','word':'осёл','basis':'x'}]},
                    {'decisions':[{'id':'y0000','choice':'yo','word':'осёлк','basis':'x'}]},
                    {'decisions':[{'id':'y0001','choice':'yo','word':'осёл','basis':'x'}]},{'decisions':[]}):
            with self.assertRaises((ValueError,KeyError)):yo.validate(bad,req)
        self.assertEqual(yo.decisions_from([{**rows[0],'sha256':'s','word':'осел','index':0}],good)[0]['choice'],'yo')

    def test_v4_policy_needs_both_answers_and_v3_is_unchanged(self):
        with self.assertRaises(ValueError):policy_options('stress-rare-v4',{'homograph_stress':HOMOGRAPH})
        with self.assertRaises(ValueError):policy_options('stress-rare-v4',{'yo_restoration':block()})
        v3=lambda t:auto_pronunciation(t,NAMES,[],**policy_options('stress-rare-v3',{'homograph_stress':HOMOGRAPH}))
        # outputs recorded from the code before stress-rare-v4 existed (2026-10-21)
        self.assertEqual(v3('Мародерки прибережем обличенное, к тебе серьезный вопрос.')[0],'Мароде́рки прибережем обличе́нное, к тебе́ серьёзный вопрос.')
        self.assertEqual(v3('Все расслаблено выдохнули. Туман осел над озером.')[0],'Всё рассла́блено выдохнули. Туман осе́л над озером.')
        self.assertEqual(v3('Все кончено. Потом Белару дали замок, и Вел ушел.')[0],'Всё кончено. Пото́м Бела́ру да́ли замо́к, и Вел ушёл.')
        self.assertEqual(v3('Он нес мешок с конфетами; ей было еще рано.')[0],'Он нёс мешок с конфетами; ей было ещё рано.')
        self.assertNotIn('yo_restoration',v3('Он нес мешок.')[1])

if __name__=='__main__':unittest.main()
