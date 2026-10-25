"""Source-bound lexical heading stress; all tagger/provider outputs are synthetic."""
import copy,sys,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'runtime'))
from rimsky.util import digest
from rimsky import russian_preparation as recipe,listener_stress,heading_stress as heading

class HeadingStressTests(unittest.TestCase):
    def witness(self,text='Хамелеон',dense='Хамелео́н',positions=None,kind='heading',names=None,known=lambda word:True):
        source={'source_id':'title','source_text':text,'source_start':0,'source_end':len(text)}
        return heading.analyze(text,source,kind=kind,names=names,silero=lambda s:dense,
            ruaccent=lambda s:positions if positions is not None else{('хамелеон',0):6},known=known)

    def test_dictionary_heading_agreement_fills_stress(self):
        witness=self.witness();text,receipt=heading.apply('Хамелеон',witness)
        self.assertEqual(text,'Хамелео́н');self.assertEqual(len(receipt['edits']),1)
        self.assertEqual(receipt['edits'][0]['authority'],'silero_ruaccent_agreement')
        self.assertEqual(witness['disagreements'],[])

    def test_generic_other_dictionary_heading_and_case(self):
        witness=self.witness('КОЛОКОЛ','КО́ЛОКОЛ',{('колокол',0):1},kind='title')
        self.assertEqual(heading.apply('КОЛОКОЛ',witness)[0],'КО́ЛОКОЛ')

    def test_nonheading_never_runs_taggers(self):
        with patch.object(heading.hs,'ruaccent_stresses',side_effect=AssertionError('No taggers')):
            witness=heading.analyze('Хамелеон',{'source_id':'p','source_text':'Хамелеон',
                'source_start':0,'source_end':8},kind='paragraph')
        self.assertEqual(heading.apply('Хамелеон',witness)[0],'Хамелеон')

    def test_source_hs_and_yo_marks_are_not_overwritten(self):
        for text in ('Хаме́леон','Хамелео́н','Ёлочки'):
            witness=self.witness(text,text,positions={})
            self.assertEqual(heading.apply(text,witness)[0],text)
            self.assertEqual(witness['rows'],[])

    def test_mixed_marked_name_and_dictionary_word_use_plain_tagger_inputs(self):
        text='\u0413\u0443\u043d\u0434\u0430\u0301\u0445\u0430\u0440 \u0425\u0430\u043c\u0435\u043b\u0435\u043e\u043d'
        plain=text.replace('\u0301',''); received=[]
        title='\u0425\u0430\u043c\u0435\u043b\u0435\u043e\u043d'; marked_title=title[:-1]+'\u0301'+title[-1]
        source={'source_id':'title','source_text':text,'source_start':0,'source_end':len(text)}
        def silero(value):
            received.append(('silero',value))
            # Dense tagging would duplicate a protected acute if given the marked input.
            return value.replace(plain.split()[0][:5],text.split()[0][:6]).replace(title,marked_title)
        def ruaccent(value):
            received.append(('ruaccent',value))
            return {(plain.split()[0].casefold(),0):1,(title.casefold(),0):6}
        witness=heading.analyze(text,source,kind='heading',names={'g':text.split()[0]},
            silero=silero,ruaccent=ruaccent,known=lambda word:True)
        final,receipt=heading.apply(text,witness)
        self.assertEqual([('silero',plain),('ruaccent',plain)],received)
        self.assertEqual(text.replace(title,marked_title),final)
        self.assertEqual(text.index(title),receipt['edits'][0]['start'])
        self.assertEqual([title],[row['surface'] for row in witness['rows']])
        self.assertEqual(text,witness['text']); self.assertEqual(recipe.source_binding(source),witness['source'])

    def test_protected_name_and_unknown_dictionary_word_skip(self):
        for names,known in (({'c':'Хамелео́н'},lambda word:True),({},lambda word:False)):
            witness=self.witness(names=names,known=known)
            self.assertEqual(heading.apply('Хамелеон',witness)[0],'Хамелеон')

    def test_whole_word_guards_mixed_identifiers_and_compounds(self):
        for text in ('idХамелеон7','ХамелеонX','_Хамелеон','Хамелеон-то','кое-Хамелеон'):
            witness=self.witness(text,text,positions={})
            self.assertEqual(witness['rows'],[])

    def test_disagreement_requires_exact_opus_decision(self):
        witness=self.witness('Замок','За́мок',{('замок',0):3})
        self.assertEqual(len(witness['disagreements']),1)
        req=heading.request(witness);self.assertEqual(req['items'][0]['candidate_b_ruaccent'],'Замо́к')
        with self.assertRaisesRegex(ValueError,'Missing decisions'):heading.apply('Замок',witness)
        answer={'decisions':[{'id':'h0000','choice':'ruaccent','stressed':'Замо́к','basis':'Synthetic context'}]}
        self.assertEqual(heading.apply('Замок',witness,answer)[0],'Замо́к')
        bad=copy.deepcopy(answer);bad['decisions'][0]['stressed']='За́мок'
        with self.assertRaises(ValueError):heading.apply('Замок',witness,bad)

    def test_unresolved_neither_holds_instead_of_claiming_secure_stress(self):
        witness=self.witness('Замок','За́мок',{('замок',0):3})
        answer={'decisions':[{'id':'h0000','choice':'neither','stressed':'Замок','basis':'Synthetic uncertainty'}]}
        with self.assertRaisesRegex(ValueError,'unresolved'):heading.apply('Замок',witness,answer)

    def test_both_taggers_missing_or_invalid_vowel_holds(self):
        for dense,positions in (('Хамелеон',{}),('Хамелео́н',{('хамелеон',0):7})):
            with self.assertRaises(ValueError):self.witness(dense=dense,positions=positions)

    def test_lexical_layer_may_not_change_letters_or_spacing(self):
        for dense in ('Хамелио́н',' Хамелео́н'):
            with self.assertRaisesRegex(ValueError,'letters or spacing'):self.witness(dense=dense)

    def test_witness_source_or_input_tampering_rejected(self):
        witness=self.witness();bad=copy.deepcopy(witness);bad['rows'][0]['silero_stress']=1
        with self.assertRaisesRegex(ValueError,'witness changed'):heading.apply('Хамелеон',bad)
        with self.assertRaisesRegex(ValueError,'another source'):heading.apply('Хамелеон!',witness)

    def test_repeated_title_words_are_source_bound_independently(self):
        witness=self.witness('Замок, замок','За́мок, замо́к',{('замок',0):1,('замок',1):3})
        self.assertEqual(heading.apply('Замок, замок',witness)[0],'За́мок, замо́к')
        self.assertEqual([(r['word'],r['index'])for r in witness['rows']],[('замок',0),('замок',1)])

    def test_derived_entry_preserves_base_and_binds_extra_policy(self):
        text='Хамелеон';witness=self.witness()
        entry={'policy':recipe.POLICY,'book_sha256':'b'*64,'source':witness['source'],
            'text_sha256':digest(text.encode()),'automatic_text':text,'module_pins':{'base':'a'*64},'model_pins':{}}
        entry['sound_sha256']=digest(recipe.sound_binding(entry));entry['sha256']=digest(entry)
        original=copy.deepcopy(entry);derived=heading.derive_entry(entry,text,witness,module_sha256='d'*64)
        self.assertEqual(entry,original);self.assertEqual(derived['heading_stress']['base_entry'],original)
        self.assertEqual(derived['heading_stress']['base_entry_sha256'],original['sha256'])
        self.assertEqual(derived['module_pins']['heading_stress'],'d'*64)
        self.assertNotEqual(derived['sound_sha256'],original['sound_sha256']);recipe.validate_entry(derived)

    def test_final_human_occurrence_overrides_heading_marks(self):
        text='Хамелеон';witness=self.witness();automatic,_=heading.apply(text,witness)
        frozen={'policy':listener_stress.POLICY,'names':{},'occurrences':[{'source_id':'title',
            'passage_sha256':digest(text),'text_offset':0,'selected_text':text,
            'intended_stress':'Хаме́леон','report_id':'synthetic-human'}]}
        frozen['sha256']=digest(frozen)
        final,evidence=listener_stress.apply(automatic,frozen,source_id='title',source_text=text,source_start=0,source_end=len(text))
        self.assertEqual(final,'Хаме́леон');self.assertEqual(evidence['applications'][0]['report_id'],'synthetic-human')

if __name__=='__main__':unittest.main()
