import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'runtime'))
sys.path.insert(0, str(ROOT/'scripts'))
from rimsky.listener_directions import (read_directions, validate_palette, validate_cast,
                                      scoped_delivery, scene_allocation, family_descriptions)
from rimsky.util import write_json, file_digest
from render_higgs_palette_preview import direction_context, assignments, CAST_SYSTEM, MOOD_SYSTEM


class ListenerDirectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.book = {'source_hash':'source', 'chapters':[{'id':'c1','blocks':[{'id':'p1','text':'Good dog.'}]}]}
        self.unit = {'id':'u1','source_id':'p1','start':0,'end':9,'text':'Good dog.',
                     'speaker':'friend','mode':'speech','chapter_id':'c1','scene':0,'_template':'ordinary'}
        self.data = {'schema':1,'source_sha256':'source','casting':{
            'giant':{'voice_family':'strong','preferred_source_character':'example',
                     'instruction':'weighty','listener_evidence':'choose the strong voice'}},
            'delivery':[{'speaker':'friend','source_id':'p1','start':0,'end':9,'text':'Good dog.',
                         'voice_family':'narrator','source_character':'narrator','route':'narration',
                         'mood':'soft','listener_evidence':'use narrator Soft here'}]}
        self.path = self.root/'listener-directions.json'
        write_json(self.path,self.data)
        self.groups = {'strong':{'voice_family':'strong','character':'example','route':'dialogue','moods':{'calm':[]}},
                       'ordinary':{'voice_family':'ordinary','character':'friend_example','route':'dialogue','moods':{'calm':[{'audio_sha256':'one'}]}},
                       'soft':{'voice_family':'narrator','character':'narrator','route':'narration','moods':{'soft':[{'audio_sha256':'two'}]}}}

    def binding(self):return read_directions(self.path,self.book)

    def test_source_binding_and_offsets_prevent_cross_book_or_broad_application(self):
        binding=self.binding()
        self.assertEqual(scoped_delivery(binding,self.unit)['mood'],'soft')
        self.assertIsNone(scoped_delivery(binding,{**self.unit,'source_id':'another'}))
        self.assertIsNone(scoped_delivery(binding,{**self.unit,'speaker':'other'}))
        with self.assertRaisesRegex(ValueError,'different book'):
            read_directions(self.path,{**self.book,'source_hash':'other book'})
        broken=copy.deepcopy(self.data);broken['delivery'][0]['end']=8
        write_json(self.path,broken)
        with self.assertRaisesRegex(ValueError,'offsets'):self.binding()

    def test_fixed_cast_and_missing_performance_are_enforced(self):
        binding=self.binding()
        validate_palette(binding,[self.unit,{**self.unit,'id':'u2','speaker':'giant'}],self.groups)
        validate_cast(binding,{'giant':{'template_id':'strong'}},self.groups)
        with self.assertRaisesRegex(ValueError,'contradicts'):
            validate_cast(binding,{'giant':{'template_id':'ordinary'}},self.groups)
        with self.assertRaisesRegex(ValueError,'unique reviewed'):
            validate_palette(binding,[self.unit,{**self.unit,'id':'u2','speaker':'giant'}],
                             {k:v for k,v in self.groups.items() if k!='soft'})

    def test_fixed_scoped_mood_is_sent_validated_and_pinned_to_audio_assignment(self):
        binding=self.binding();calls=[]
        class Fake:
            def call(inner,out,name,system,payload,schema,validate):
                calls.append(payload)
                self.assertEqual(payload['listener_delivery_directions']['u1']['mood'],'soft')
                answer={'u1':{'mood':'soft','evidence':'Good dog.','reason':'care','needs_review':False}}
                validate(answer)
                for invalid in [{**answer['u1'],'mood':'calm'}, {**answer['u1'],'needs_review':True}]:
                    with self.assertRaisesRegex(ValueError,'contradicts'):validate({'u1':invalid})
                return answer
        dest=self.root/'direction';dest.mkdir()
        result=assignments(Fake(),dest,[self.unit],{'p1':{'text':'Good dog.'}},
                           {'friend':{'template_id':'ordinary','baseline_mood':'calm','needs_review':False}},
                           self.groups,{'directions':binding,'mood_system':MOOD_SYSTEM})
        self.assertEqual(result[0]['voice'],'narrator')
        self.assertEqual(result[0]['palette_assignment']['listener_directions_sha256'],binding['sha256'])
        self.assertEqual(result[0]['palette_assignment']['template_id'],'soft')
        self.assertTrue(result[0]['presentation_route'].startswith('listener_scoped:'))
        self.assertEqual(self.unit['_template'],'ordinary')
        self.assertEqual(len(calls),2)

    def test_changed_direction_or_registry_refuses_new_generation_resume(self):
        registry=self.root/'registry/review.json'
        write_json(registry,{'decisions':{}})
        write_json(registry.with_name('latest.json'),{'review_sha256':file_digest(registry)})
        units=[self.unit,{**self.unit,'id':'u2','speaker':'giant'}]
        out=self.root/'new';out.mkdir()
        context=direction_context(self.root,out,self.book,units,self.groups,registry_path=registry)
        write_json(out/'manifest.json',{'direction_contract':context['contract']})
        changed=copy.deepcopy(self.data);changed['casting']['giant']['instruction']='even weightier'
        write_json(self.path,changed)
        with self.assertRaisesRegex(ValueError,'Frozen listener'):
            direction_context(self.root,out,self.book,units,self.groups,registry_path=registry)
        write_json(self.path,self.data)
        write_json(registry,{'decisions':{'strong':{'descriptors':{'voice_weight':'heavy'}}}})
        write_json(registry.with_name('latest.json'),{'review_sha256':file_digest(registry)})
        with self.assertRaisesRegex(ValueError,'Frozen listener'):
            direction_context(self.root,out,self.book,units,self.groups,registry_path=registry)
        resumed=direction_context(self.root,out,self.book,units,self.groups,registry_path=registry,
                                  preserve_frozen_descriptions=True)
        self.assertEqual(context['contract'],resumed['contract'])

    def test_legacy_resume_uses_frozen_system_without_adding_book_directions(self):
        out=self.root/'legacy'
        write_json(out/'manifest.json',{'cast':{}})
        write_json(self.root/'palette-preview-v1/cast/primary.request.json',{'system':'old cast'})
        write_json(out/'c1/chunk-000/direction/primary.request.json',{'system':'old delivery'})
        context=direction_context(self.root,out,self.book,[],{})
        self.assertEqual(context['cast_system'],'old cast')
        self.assertEqual(context['mood_system'],'old delivery')
        self.assertIsNone(context['directions'])
        with self.assertRaisesRegex(ValueError,'new generation'):
            direction_context(self.root,out,self.book,[],{},self.path)
        self.assertNotIn('Okrus',CAST_SYSTEM+MOOD_SYSTEM)

    def test_joint_allocation_reports_fixed_family_collisions_without_claiming_distinctness(self):
        units=[{**self.unit,'speaker':s} for s in ['giant','friend']]
        cast={s:{'template_id':'strong'} for s in ['giant','friend']}
        report=scene_allocation(units,cast,self.groups,self.binding())
        self.assertTrue(report['interactions'][0]['same_family'])
        self.assertEqual(report['interactions'][0]['qualification'],'unresolved')

    def test_stable_moods_can_distinguish_shared_family_without_automatic_recast(self):
        units=[{**self.unit,'speaker':s} for s in ['giant','friend']]
        cast={'giant':{'template_id':'strong','baseline_mood':'calm'},
              'friend':{'template_id':'strong','baseline_mood':'neutral'}}
        pair=scene_allocation(units,cast,self.groups,self.binding())['interactions'][0]
        self.assertTrue(pair['same_family'])
        self.assertFalse(pair['same_family_and_mood'])
        self.assertIn('qualified_stable_family_mood_pair',pair['resolution_options'])
        self.assertEqual(pair['qualification'],'unresolved')
        cast['friend']['baseline_mood']='calm'
        self.assertTrue(scene_allocation(units,cast,self.groups,self.binding())['interactions'][0]['same_family_and_mood'])

    def test_all_five_sound_dimensions_and_matching_checksum_required(self):
        registry=self.root/'registry/review.json'
        from serve_voice_family_descriptor_review import DESCRIPTORS
        dimensions={'voice_weight':'heavy','voice_depth':'low','brightness':'dark',
                    'roughness':'rough','perceived_age':'mature'}
        self.assertTrue(all(value in DESCRIPTORS[key] for key,value in dimensions.items()))
        write_json(registry,{'decisions':{'strong':{'descriptors':dimensions},
                                         'guidance_only':{'casting_description':'A precise, dry voice.'},
                                         'incomplete':{'descriptors':{'voice_weight':'heavy'}}}})
        write_json(registry.with_name('latest.json'),{'review_sha256':file_digest(registry)})
        descriptions=family_descriptions(registry)['families']
        self.assertEqual(set(descriptions),{'strong','guidance_only'})
        self.assertEqual(descriptions['guidance_only']['casting_description'],'A precise, dry voice.')
        registry.write_text('{}',encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'checksum'):family_descriptions(registry)


if __name__=='__main__':unittest.main()
