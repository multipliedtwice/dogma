import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'runtime'))
from rimsky.voice_descriptors import (casting_guidance, default_catalog, load_catalog,
                                      validate_catalog_palette, validate_descriptor_fit)
from rimsky.listener_directions import family_descriptions
from rimsky.jobs import bind_voice_family_guidance
from rimsky.util import file_digest,write_json


class DescriptorTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'catalog.json'
        write_json(self.path.with_name('measurements.json'),{'samples':[]})
        self.row={'descriptors':{'voice_weight':'heavy','voice_depth':'low','brightness':'dark',
            'roughness':'unclear','perceived_age':'mature','performed_gender':'masculine'},
            'basis':'measured_audio_and_listener_metadata','reviewed':False,'audio_evidence_sha256':['audio']}
        self.data={'schema':1,'kind':'voice_family_sound_catalog','bank_sha256':'bank',
            'measurement_sha256':file_digest(self.path.with_name('measurements.json')),'families':{'strong':self.row}}
        write_json(self.path,self.data)

    def test_descriptor_catalog_is_bound_to_measured_audio_and_palette(self):
        catalog=load_catalog(self.path)
        groups={'one':{'voice_family':'strong','moods':{'calm':[{'audio_sha256':'audio'}]}}}
        validate_catalog_palette(catalog,groups,'bank')
        with self.assertRaisesRegex(ValueError,'different palette'):validate_catalog_palette(catalog,groups,'other')
        with self.assertRaisesRegex(ValueError,'outside'):validate_catalog_palette(catalog,{},'bank')
        write_json(self.path.with_name('measurements.json'),{'changed':True})
        with self.assertRaisesRegex(ValueError,'measurements changed'):load_catalog(self.path)

    def test_unmeasured_traits_cannot_be_claimed_as_casting_evidence(self):
        choice={'needs_review':True,'voice_fit':{'descriptor_family':'strong','supporting_dimensions':['voice_weight'],
            'missing_traits':['roughness'],'signature_reason':'weighty voice; roughness unresolved'}}
        validate_descriptor_fit(choice,{'voice_family':'strong'},self.data['families'])
        wrong=copy.deepcopy(choice);wrong['voice_fit']['supporting_dimensions']=['roughness']
        with self.assertRaisesRegex(ValueError,'Unknown'):validate_descriptor_fit(wrong,{'voice_family':'strong'},self.data['families'])
        wrong=copy.deepcopy(choice);wrong['needs_review']=False
        with self.assertRaisesRegex(ValueError,'Unmet'):validate_descriptor_fit(wrong,{'voice_family':'strong'},self.data['families'])
        wrong=copy.deepcopy(choice);wrong['voice_fit']['descriptor_family']='another'
        with self.assertRaisesRegex(ValueError,'another voice'):validate_descriptor_fit(wrong,{'voice_family':'strong'},self.data['families'])

    def test_automated_descriptions_cannot_be_saved_as_human_approvals(self):
        self.data['families']['strong']['reviewed']=True;write_json(self.path,self.data)
        with self.assertRaisesRegex(ValueError,'unreviewed'):load_catalog(self.path)

    def test_explicit_catalog_layers_hash_bound_listener_overrides(self):
        reviewed={'sha256':'listener-review-v2','families':{'strong':{
            'descriptors':{'voice_weight':'light','perceived_gender':'neutral','otherness':'human'},
            'casting_description':'Dry, compact and direct.',
            'character_fit':'Restrained authority figures.',
            'avoid_for':'Do not confuse with the heavier low family.',
            'basis':'explicit_listener'}}}
        catalog=default_catalog(self.path.parent,reviewed,self.path)
        family=catalog['families']['strong']
        self.assertEqual(family['descriptors']['voice_weight'],'light')
        self.assertEqual(family['descriptors']['performed_gender'],'neutral')
        self.assertEqual(family['descriptors']['otherness'],'human')
        self.assertEqual(family['casting_description'],'Dry, compact and direct.')
        self.assertEqual(family['audio_evidence_sha256'],['audio'],'Measured evidence remains bound')
        self.assertNotEqual(catalog['sha256'],file_digest(self.path))
        changed=default_catalog(self.path.parent,{**reviewed,'sha256':'listener-review-v3'},self.path)
        self.assertNotEqual(changed['sha256'],catalog['sha256'])

    def test_casting_guidance_excludes_raw_notes_and_binds_merged_hash(self):
        registry=self.path.parent/'registry/review.json';registry.parent.mkdir()
        write_json(registry,{'schema':1,'narrator_id':'vlad-rimsky','registry_version':'vlad-rimsky-v1','decisions':{
            'strong':{'descriptors':{'voice_weight':'light','voice_depth':'low','brightness':'dark',
                'roughness':'rough','perceived_age':'mature'},'casting_description':'Dry authority',
                'character_fit':'captain','avoid_for':'child','notes':'private raw review note'}}})
        write_json(registry.with_name('latest.json'),{'review_sha256':file_digest(registry)})
        guidance=casting_guidance(self.path.parent,self.path,registry)
        self.assertEqual(guidance['catalog_sha256'],default_catalog(
            self.path.parent,family_descriptions(registry),self.path)['sha256'])
        self.assertEqual(guidance['families']['strong']['character_fit'],'captain')
        self.assertNotIn('notes',guidance['families']['strong'])

    def test_generation_keeps_its_frozen_guidance_when_registry_changes(self):
        measured=self.path.parent/'training-work/higgs-production/voice-descriptors/measured'
        measured.mkdir(parents=True)
        write_json(measured/'measurements.json',{'samples':[]})
        write_json(measured/'catalog.json',{**self.data,'measurement_sha256':file_digest(measured/'measurements.json')})
        write_json(measured.parent/'latest.json',{'catalog':'measured/catalog.json','sha256':file_digest(measured/'catalog.json')})
        registry=self.path.parent/'manual-labeling/voice-family-registries/vlad-rimsky-v1/review.json'
        registry.parent.mkdir(parents=True)
        write_json(registry,{'schema':1,'narrator_id':'vlad-rimsky','registry_version':'vlad-rimsky-v1','decisions':{
            'strong':{'descriptors':{'voice_weight':'light','voice_depth':'low','brightness':'dark',
                'roughness':'rough','perceived_age':'mature'},'casting_description':'first'}}})
        write_json(registry.with_name('latest.json'),{'review_sha256':file_digest(registry)})
        frozen=bind_voice_family_guidance({},self.path.parent/'resources')
        first=frozen['voice_family_guidance']['catalog_sha256']
        changed=json.loads(registry.read_text());changed['decisions']['strong']['casting_description']='second';write_json(registry,changed)
        write_json(registry.with_name('latest.json'),{'review_sha256':file_digest(registry)})
        self.assertIs(bind_voice_family_guidance(frozen,self.path.parent/'resources'),frozen)
        self.assertEqual(frozen['voice_family_guidance']['catalog_sha256'],first)
        self.assertNotEqual(bind_voice_family_guidance({},self.path.parent/'resources')['voice_family_guidance']['catalog_sha256'],first)

    def test_generation_guidance_only_advertises_families_in_selected_pack(self):
        measured=self.path.parent/'training-work/higgs-production/voice-descriptors/measured'
        measured.mkdir(parents=True)
        write_json(measured/'measurements.json',{'samples':[]})
        write_json(measured/'catalog.json',{**self.data,'measurement_sha256':file_digest(measured/'measurements.json')})
        write_json(measured.parent/'latest.json',{'catalog':'measured/catalog.json','sha256':file_digest(measured/'catalog.json')})
        registry=self.path.parent/'manual-labeling/voice-family-registries/vlad-rimsky-v1/review.json'
        registry.parent.mkdir(parents=True)
        write_json(registry,{'schema':1,'narrator_id':'vlad-rimsky','registry_version':'vlad-rimsky-v1','decisions':{}})
        write_json(registry.with_name('latest.json'),{'review_sha256':file_digest(registry)})
        selected={'voices':[{'id':'available-voice','family':'another_family'}]}
        guidance=bind_voice_family_guidance({},self.path.parent/'resources',selected)['voice_family_guidance']
        self.assertEqual(guidance['families'],{})
        self.assertIn('guidance_sha256',guidance)

    def test_unknown_character_traits_allow_only_explicitly_unqualified_proposals(self):
        choice={'needs_review':True,'voice_fit':{'descriptor_family':'strong','supporting_dimensions':[],
            'missing_traits':['Unknown speaker voice requirements'],'signature_reason':'Unqualified placeholder'}}
        validate_descriptor_fit(choice,{'voice_family':'strong'},self.data['families'])
        choice['needs_review']=False
        with self.assertRaisesRegex(ValueError,'abstain'):validate_descriptor_fit(choice,{'voice_family':'strong'},self.data['families'])


if __name__=='__main__':unittest.main()
