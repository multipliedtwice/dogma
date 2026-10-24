"""Private ambiguity inventory from validated fixture receipts; no providers/audio."""
import json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'runtime')]
import report_pronunciation_ambiguities as m


class AmbiguityReport(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        exe=self.root/'codex.exe';exe.write_bytes(b'fixture')
        self.metadata=dict(zip(('provider','model','effort'),m.builder.PIN))|{'policy':m.rules.RULE,
            'requested_model':'gpt-6-luna','model_provenance':'explicit-cli-model-selection','cli_version':'fixture',
            'executable':str(exe),'executable_sha256':m.file_digest(exe),
            'structured_call_sha256':m.file_digest(Path(m.builder.codex_analysis.__file__))}
        first='Здесь стоит человек.';second='Билет стоит дорого.'
        self.inputs=[{'source':first,'selected':first.replace('стоит','сто́ит'),'chapter':'c0001'},
                     {'source':second,'selected':second,'chapter':'c0002'}]
        answer={'decisions':[{'id':'c0000','word':'стоит','stressed':'стои́т','basis':'Person is standing.'},
                             {'id':'c0001','word':'стоит','stressed':'сто́ит','basis':'Ticket price.'}]}
        with patch.object(m.builder,'call',return_value=(answer,self.metadata)):
            m.builder.prepare(self.inputs,self.root/'context')
        self.answer=self.root/'context/answer.json'
    def test_all_detected_readings_changed_and_unmarked_input_are_reported_without_calls(self):
        with patch.object(m.builder,'call',side_effect=AssertionError('No report provider calls')) as call:
            report=m.report(self.answer,self.root/'report');call.assert_not_called()
        self.assertEqual(report['counts'],{'prepared_occurrences':2,'input_observations':2,'located_occurrences':2})
        first,second=report['occurrences']
        self.assertEqual(set(first['possible_readings']),{'сто́ит','стои́т'})
        self.assertEqual(first['luna']['stressed'],'стои́т');self.assertTrue(first['input_observations'][0]['superseded_automatic_mark'])
        self.assertTrue(second['input_observations'][0]['unmarked_before_context'])
        self.assertEqual(first['locations'],[{'chapter':'c0001'}]);self.assertFalse(report['human_approval'])
        self.assertEqual(report['recognition_status'],'not_checked');self.assertIn('not exhaustive',report['scope'])
        text=(self.root/'report/report.txt').read_text('utf8')
        self.assertIn('стои́т',text);self.assertIn('No audio/recognition/listening approval',text)
        manifest=json.loads((self.root/'report/manifest.json').read_text('utf8'))
        for name,checksum in manifest['artifacts'].items():self.assertEqual(m.file_digest(self.root/'report'/name),checksum)
    def test_assignment_maps_all_equal_source_units_without_inventing_offsets(self):
        units=[]
        for i,item in enumerate(self.inputs):
            units.append({'id':f'u{i}','chapter_id':f'c{i+1:04d}','source_id':f'p{i}',
                'start':100,'end':100+len(item['source']),'text':item['source'],'spoken':item['source']})
        units.append({**units[0],'id':'u-copy','source_id':'p-copy','text':'— '+units[0]['text']})
        path=self.root/'assignment.json';m.builder.immutable(path,{'units':units})
        report=m.report(self.answer,self.root/'located',path);locations=report['occurrences'][0]['locations']
        self.assertEqual([r['id'] for r in locations],['u0','u-copy'])
        self.assertEqual(locations[0]['word_source_start'],106);self.assertNotIn('word_source_start',locations[1])
        self.assertEqual(report['provenance']['assignment_sha256'],m.file_digest(path))
    def test_resume_is_byte_identical_and_never_replaces_a_different_report(self):
        out=self.root/'report';m.report(self.answer,out);before={p.name:p.read_bytes() for p in out.iterdir()}
        m.report(self.answer,out);self.assertEqual(before,{p.name:p.read_bytes() for p in out.iterdir()})
        (out/'report.txt').write_text('changed',encoding='utf8')
        with self.assertRaisesRegex(ValueError,'Immutable ambiguity report changed'):m.report(self.answer,out)
        self.assertEqual((out/'report.txt').read_text('utf8'),'changed')
    def test_tampered_source_and_unrelated_assignment_fail_before_output(self):
        wrong=self.root/'wrong.json';m.builder.immutable(wrong,{'units':[{'id':'x','text':'Другое слово.','spoken':'Другое слово.'}]})
        with self.assertRaisesRegex(ValueError,'Assignment does not contain'):m.report(self.answer,self.root/'wrong-report',wrong)
        self.assertFalse((self.root/'wrong-report').exists())
        path=self.answer.parent/'inputs.json';value=m.builder.read(path);value['inputs'][0]['source']='Different source.'
        path.write_text(json.dumps(value),encoding='utf8')
        with self.assertRaises(ValueError):m.report(self.answer,self.root/'tampered')
        self.assertFalse((self.root/'tampered').exists())


if __name__=='__main__':unittest.main()
