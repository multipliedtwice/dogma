import json,sys,tempfile,unittest
from unittest.mock import patch
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'runtime'),str(ROOT/'scripts')]
from rimsky import homograph_stress as hs
from rimsky.auto_pronunciation import auto_pronunciation,policy_options
from rimsky.proper_noun_stress import proper_noun_lexicon

A='́'
TEXT='Запах гнилой плоти бил в нос.'
SILERO='плоти'.replace('и','и'+A)   # плоти́ (silero's wrong mark)
RUACCENT='пло'+A+'ти'
fake=lambda stresses:(lambda text:stresses)  # RUAccent stand-in: {(key,occurrence):letter index|None}


def rows(text=TEXT,stresses=None,**kw):
    return hs.disagreements([{'text':text,**kw}],ruaccent=fake({('плоти',0):2} if stresses is None else stresses))


class HomographStressTests(unittest.TestCase):
    def test_builder_uses_deepest_preparation_wrapper(self):
        import build_homograph_stress as builder
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);chunk=root/'generation/c0001/chunk-000'
            shallow={'units':[{'id':'u1','text':'Старый текст.'}]}
            final={'units':[{'id':'u1','text':'Финальный текст.'}]}
            chunk.mkdir(parents=True);(chunk/'assignment.json').write_text(json.dumps(shallow),encoding='utf8')
            deep=chunk/'presentation-x/number-groups-v2';deep.mkdir(parents=True)
            (deep/'assignment.json').write_text(json.dumps(final),encoding='utf8')
            with patch.object(builder,'FOLDER',root):
                texts=builder.native_texts(['generation'],['c0001'])
            self.assertEqual(texts,{'Финальный текст.':'c0001'})

    def test_disagreement_is_found_with_both_forms(self):
        r=rows()
        self.assertEqual([(x['word'],x['index'],x['silero'],x['ruaccent']) for x in r],[('плоти',0,SILERO,RUACCENT)])

    def test_agreement_and_no_opinion(self):
        self.assertEqual(rows(stresses={('плоти',0):4}),[])        # same stress as silero
        self.assertEqual(rows(stresses={})[0]['ruaccent'],None)    # RUAccent silent is still a disagreement

    def test_proper_nouns_are_not_adjudicated(self):
        self.assertEqual(rows('Он видел Эо.',stresses={}),[])

    def test_apply_replace_remove_leave(self):
        marked='Запах гнилой '+SILERO+' бил в нос.'
        base=dict(sha256=hs.digest(TEXT.encode()),word='плоти',index=0,silero=SILERO)
        out,applied,unbound=hs.apply(marked,[{**base,'choice':'ruaccent','stress':2}],TEXT)
        self.assertIn(RUACCENT,out);self.assertEqual(len(applied),1);self.assertEqual(unbound,[])
        out,_,_=hs.apply(marked,[{**base,'choice':'neither','stress':None}],TEXT)
        self.assertEqual(out,TEXT)
        self.assertEqual(hs.apply(marked,[],TEXT),(marked,[],[]))
        self.assertEqual(hs.apply(marked,[{**base,'sha256':'0'*64,'choice':'neither','stress':None}],TEXT),(marked,[],[]))  # other text: unbound by design
        out,applied,unbound=hs.apply(marked,[{**base,'silero':'друго'+'й','choice':'neither','stress':None}],TEXT)
        self.assertEqual((out,len(unbound)),(marked,1))            # silero's mark drifted: ignored and reported

    def test_second_occurrence_binds_by_index(self):
        text='Ноги идут. Под ноги.'
        override={'sha256':hs.digest(text.encode()),'word':'ноги','index':1,'choice':'neither','silero':'но'+A+'ги','stress':None}
        out,applied,_=hs.apply('Ноги идут. Под но'+A+'ги.',[override],text)
        self.assertEqual(out,text);self.assertEqual(applied[0]['index'],1)

    def test_validator_rejects_letter_changes_and_mismatched_choices(self):
        req=hs.request(rows())
        hs.validate({'decisions':[{'id':'h0000','choice':'ruaccent','stressed':RUACCENT,'basis':'x'}]},req)
        for bad in ({'choice':'ruaccent','stressed':'плате'},               # letters changed
                    {'choice':'silero','stressed':RUACCENT},                 # echo does not match the choice
                    {'choice':'neither','stressed':SILERO},
                    {'choice':'maybe','stressed':'плоти'}):
            with self.assertRaises(ValueError):hs.validate({'decisions':[{'id':'h0000','basis':'x',**bad}]},req)
        with self.assertRaises(ValueError):hs.validate({'decisions':[]},req)
        with self.assertRaises(ValueError):   # no RUAccent candidate to choose
            hs.validate({'decisions':[{'id':'h0000','choice':'ruaccent','stressed':'плоти','basis':'x'}]},hs.request(rows(stresses={})))

    def test_overrides_skip_silero_choices(self):
        r=rows();ans=lambda c,s:{'decisions':[{'id':'h0000','choice':c,'stressed':s,'basis':''}]}
        self.assertEqual(hs.overrides(r,ans('silero',SILERO)),[])
        o=hs.overrides(r,ans('ruaccent',RUACCENT));self.assertEqual((o[0]['choice'],o[0]['stress']),('ruaccent',2))

    def _spec(self,choice,stress,overrides=True):
        return {'rule':hs.RULE,'answer_sha256':'a'*64,'overrides':[{'sha256':hs.digest(TEXT.encode()),'word':'плоти','index':0,
                'choice':choice,'silero':SILERO,'stress':stress}] if overrides else []}

    def test_v2_is_unchanged_and_v3_applies_the_override(self):
        v2=auto_pronunciation(TEXT,{},[],**policy_options('stress-rare-v2'))
        self.assertIn(SILERO,v2[0]);self.assertNotIn('homograph_overrides',v2[1])
        out,rec=auto_pronunciation(TEXT,{},[],**policy_options('stress-rare-v3',{'homograph_stress':self._spec('ruaccent',2)}))
        self.assertIn(RUACCENT,out);self.assertEqual(rec['policy'],'stress-rare-v3')
        self.assertEqual(rec['homograph_overrides']['applied'][0]['choice'],'ruaccent')
        self.assertEqual(rec['homograph_overrides']['answer_sha256'],'a'*64)
        out,_=auto_pronunciation(TEXT,{},[],**policy_options('stress-rare-v3',{'homograph_stress':self._spec('neither',None)}))
        self.assertEqual(out,TEXT)                                            # removed mark
        out,_=auto_pronunciation(TEXT,{},[],**policy_options('stress-rare-v3',{'homograph_stress':self._spec('x',None,False)}))
        self.assertEqual(out,v2[0])                                           # no override: same text as v2
        with self.assertRaises(ValueError):policy_options('stress-rare-v3',{})

    def test_sentence_initial_base_and_oblique_only_family(self):
        lex=proper_noun_lexicon(['Он жил возле Нимраса и Укумрии. Нимрас, Ярма, Сальпетриер.'],{},True)
        self.assertEqual(lex['pn:Нимрас'],'Нимра'+A+'с')
        self.assertNotIn('pn:Нимраса',lex)
        out=auto_pronunciation('Мы шли к Нимрасу и Нимраса.',lex,[],**policy_options('stress-rare-v2'))[0]
        self.assertIn('Нимра'+A+'су',out);self.assertIn('Нимра'+A+'са',out)
        only=proper_noun_lexicon(['Он жил возле Нимраса.'],{},True).get('pn:Нимраса')   # oblique-only: derived from Нимрас
        self.assertEqual(only,'Нимра'+A+'са')

    def test_v2_lexicon_is_unchanged(self):
        self.assertNotIn('pn:Нимрас',proper_noun_lexicon(['Нимрас, Ярма. Возле Нимраса.'],{}))

    def test_unlisted_proper_noun_is_not_marked_by_the_rare_word_pass_in_v3(self):
        text='Он шёл возле Зумбралфа и Укумрии.'
        out=auto_pronunciation(text,{},[],**policy_options('stress-rare-v3',{'homograph_stress':self._spec('x',None,False)}))[0]
        self.assertNotIn(A,out.split('возле')[1])


if __name__=='__main__':unittest.main()
