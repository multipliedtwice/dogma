"""Production-rulebook isolation, identity/route separation and prompt scoping."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'scripts'), str(ROOT/'runtime')]
from build_production_rulebook import build, json_bytes
from production_rulebook_rules import MOOD_RULES
from rimsky.delivery_rulebook import delivery_context, load_rulebook, validate_rulebook, example_citation_key


def row(cid, speaker, voice, mood='neutral', chapter='dev', book='b1', **extra):
    return dict(id=cid, book=book, chapter_id=chapter, source_id=cid, text='Exact text '+cid,
        paragraph='Context '+cid, speaker=speaker, performer=speaker, speaker_basis='proposal',
        voice=voice, voice_basis='human', mood=mood, mood_basis='human', unit='clip', **extra)


class ProductionRulebookTests(unittest.TestCase):
    def setUp(self):
        self.split = {'books':{'b1':{'development_chapters':['dev'], 'test_chapters':['sealed']}}}
        self.cast = {'characters':[{'id':'rb1_kirill','name':'Kirill','aliases':['Komar']},
                                   {'id':'ivan','name':'Ivan','aliases':[]},
                                   {'id':'other','name':'Other','aliases':[]}]}
        self.rows = [row('k1','Kirill','masculine_4','confident'),
                     row('k2','Komar','masculine_4','calm'),
                     row('i1','Ivan','masculine_2','confident'),
                     row('i2','Ivan','masculine_2','calm'),
                     row('i3','Ivan','masculine_6_mechanical','neutral'),
                     row('other1','Other','masculine_2','calm'),
                     row('n1','Komar','narrator_baseline','calm'),
                     row('n2','narrator','narrator_opening','neutral'),
                     row('g1','unknown','mechanical_stats','neutral'),
                     row('u1','unknown','masculine_2','angry'),
                     row('t1','Ivan','masculine_2','disgusted','sealed')]
        self.book, self.dev = build(self.rows,self.split,self.cast,{m:m for m in MOOD_RULES},{})

    def test_test_examples_and_test_only_moods_never_enter_character_evidence(self):
        self.assertEqual(1,self.book['coverage']['excluded']['sealed_test'])
        self.assertNotIn('t1',{r['id'] for r in self.dev})
        for profile in self.book['characters']:
            for route in profile['routes']:
                self.assertNotIn('disgusted',route['mood_counts'])
                for rule in route['mood_rules']:
                    for example in rule['positive_examples']+rule['contrast_examples']:
                        self.assertEqual('dev',example['chapter_id'])
                        self.assertNotIn('t1',example['paragraph'])

    def test_aliases_merge_identity_but_narration_and_voice_variants_stay_separate(self):
        profiles={p['id']:p for p in self.book['characters']}
        self.assertEqual(2,profiles['rb1_kirill']['routes'][0]['development_choices'])
        self.assertEqual({'masculine_2','masculine_6_mechanical'}, {r['voice_family'] for r in profiles['ivan']['routes']})
        self.assertEqual({'narrator_baseline','narrator_opening'}, {r['voice_family'] for r in profiles['narrator']['routes']})
        self.assertNotIn('Komar',profiles['narrator']['aliases'])
        self.assertEqual(1,len(profiles['game_system']['routes']))
        self.assertFalse(any(p['name']=='unknown' for p in profiles.values()))

    def test_context_contains_only_requested_character_and_voice(self):
        context=delivery_context(self.book,['ivan/masculine_2'],['confident'])
        references=[e for p in context['character_routes'] for r in p['rules'] for e in r['examples']+r['contrasts']]
        self.assertEqual({example_citation_key(r) for r in self.rows if r['id'] in ('i1','i2')}, {e['citation_key'] for e in references})
        self.assertNotIn('Other',json.dumps(context))
        self.assertNotIn('Exact text i3',json.dumps(context))
        with self.assertRaises(ValueError):delivery_context(self.book,['unknown/masculine_2'],['calm'])
        with self.assertRaises(ValueError):delivery_context(self.book,['ivan/masculine_2'],['calm'],max_characters=100)

    def test_sparse_or_unseen_mood_is_retained_as_uncertain_not_folded_to_baseline(self):
        context=delivery_context(self.book,['ivan/masculine_2'],['disgusted'])
        rule=next(r for r in context['character_routes'][0]['rules'] if r['mood']=='disgusted')
        self.assertEqual('unobserved_in_development',rule['status'])
        self.assertEqual([],rule['examples'])
        self.assertIn('disgusted',context['all_supported_mood_ids'])
        self.assertEqual(['proposed','needs_adjudication'],context['decision_contract']['statuses'])

    def test_ambiguous_alias_is_not_merged(self):
        cast=copy.deepcopy(self.cast)
        cast['characters'][1]['aliases']=['shared'];cast['characters'][2]['aliases']=['shared']
        book,_=build([row('amb','shared','masculine_2')],self.split,cast,{m:m for m in MOOD_RULES},{})
        self.assertEqual([],book['characters'])
        self.assertEqual(1,len(book['coverage']['unresolved_development']))

    def merged_fixture(self, citation='i1'):
        rows = [dict(r, identity=r['speaker'], route='dialogue') for r in self.rows]
        item = {'instructions_file': 'instructions/ivan.json', 'instructions_sha256': 'pinned',
                'production_baseline': 'calm', 'frozen_voice': 'masculine_2',
                'instructions': {'identity': 'Ivan', 'route': 'dialogue', 'packet': 'ivan.json',
                    'moods': [{'mood': 'confident', 'choose_when': ['He commits to an outcome despite resistance.'],
                        'avoid_when': ['He is only giving background information.'],
                        'versus': [{'mood':'neutral','how_to_tell':'Commitment rather than factual description.'}],
                        'continuity': 'Always use this in Book 2 chapter 5.',
                        'limits': 'Sparse voice-specific evidence; rb2_1234567890abcdef12 is an archived reference.',
                        'examples': [{'id':citation}],
                        'counterexamples': [{'id':'i2','labelled_mood':'calm'}]}]}}
        policy = {'identities': {}, 'performers': {'Ivan': {'dialogue': item}}}
        return rows, policy

    def test_merge_specific_rules_enter_runtime_and_ablation_removes_them(self):
        rows, policy = self.merged_fixture()
        rows[2]['before'] = 'Exact text t1'  # neighbour from sealed chapter
        book, dev = build(rows,self.split,self.cast,{m:m for m in MOOD_RULES},{},policy)
        context = delivery_context(book,['ivan/masculine_2'],['confident'])
        rule = next(r for r in context['character_routes'][0]['rules'] if r['mood']=='confident')
        self.assertIn('despite resistance',rule['choose_when'])
        self.assertIsNone(rule['character_specific_instructions'][0]['continuity'])
        self.assertNotIn('Book 2',json.dumps(context))
        self.assertNotIn('rb2_1234567890abcdef12',json.dumps(context))
        self.assertIn('[offline example reference]',json.dumps(context))
        self.assertNotIn('Exact text t1',json.dumps(rule['example_context']))
        self.assertEqual(1,book['merge_report']['specific_mood_rules'])
        alternate = delivery_context(book,['ivan/masculine_6_mechanical'],['confident'])
        self.assertNotIn('despite resistance',json.dumps(alternate))
        general = delivery_context(book,['ivan/masculine_2'],['confident'],include_character_instructions=False)
        self.assertTrue(general['character_routes'][0]['rules'])
        for specific_rule, general_rule in zip(context['character_routes'][0]['rules'], general['character_routes'][0]['rules']):
            for key in ('examples', 'contrasts', 'contrast_moods', 'example_context'):
                self.assertEqual(specific_rule[key],general_rule[key])
        self.assertNotIn('despite resistance',json.dumps(general))
        self.assertEqual(context['policy_version'],general['policy_version'])

    def test_example_payloads_hide_provenance_but_keep_contrast_labels_and_context(self):
        context = delivery_context(self.book,['ivan/masculine_2'],['confident'])
        rules = context['character_routes'][0]['rules']
        for rule in rules:
            for example in rule['examples'] + rule['contrasts']:
                self.assertEqual({'citation_key','text'},set(example))
                self.assertRegex(example['citation_key'],r'^e_[0-9a-f]{32}$')
            for example in rule['contrasts']:
                self.assertIn(example['citation_key'],rule['contrast_moods'])
        encoded = json.dumps(context)
        for field in ('"book":','"chapter_id":','"source_id":','"speaker_basis":'):
            self.assertNotIn(field,encoded)
        self.assertTrue(any('Context i1' in value for rule in rules for value in rule['example_context'].values()))
        original = self.rows[2]
        self.assertEqual(example_citation_key(original),example_citation_key(dict(original,chapter_id='different')))
        self.assertNotEqual(example_citation_key(original),example_citation_key(dict(original,book='another book')))

    def exclusion_fixture(self):
        split=copy.deepcopy(self.split)
        split['books']['b1']['development_chapters'].append('spare')
        split['books']['b2']={'development_chapters':['dev'],'test_chapters':[]}
        rows=self.rows + [row('spare','Ivan','masculine_2','confident',chapter='spare'),
                          row('same_chapter_other_book','Ivan','masculine_2','confident',book='b2')]
        book,_=build(rows,split,self.cast,{m:m for m in MOOD_RULES},{})
        route=next(r for p in book['characters'] if p['id']=='ivan' for r in p['routes'] if r['voice_family']=='masculine_2')
        rule=next(r for r in route['mood_rules'] if r['mood']=='confident')
        # Put the excluded example first to exercise filter-before-limit.
        rule['positive_examples'].sort(key=lambda e:e['id']!='i1')
        return book

    def test_chapter_exclusion_precedes_limit_and_removes_contrasts_and_context(self):
        book=self.exclusion_fixture();before=copy.deepcopy(book)
        context=delivery_context(book,['ivan/masculine_2'],['confident'],examples_per_mood=1,
                                 exclude_example_chapters=[('b1','dev')])
        references=[e for r in context['character_routes'][0]['rules'] for e in r['examples']+r['contrasts']]
        self.assertTrue(references)
        self.assertNotIn('Exact text i1',json.dumps(context))
        self.assertNotIn('Context i1',json.dumps(context))
        self.assertNotIn('Exact text i2',json.dumps(context))
        self.assertNotIn('Context i2',json.dumps(context))
        self.assertEqual(before,book)
        general=delivery_context(book,['ivan/masculine_2'],['confident'],examples_per_mood=1,
                                 include_character_instructions=False,exclude_example_chapters=[('b1','dev')])
        for a,b in zip(context['character_routes'][0]['rules'],general['character_routes'][0]['rules']):
            for field in ('examples','contrasts','contrast_moods','example_context'):
                self.assertEqual(a[field],b[field])

    def test_chapter_exclusion_is_scoped_by_book_and_does_not_rebuild_policy(self):
        book=self.exclusion_fixture()
        context=delivery_context(book,['ivan/masculine_2'],['confident'],examples_per_mood=10,
                                 exclude_example_chapters=[('b1','dev')])
        self.assertIn('Exact text same_chapter_other_book',json.dumps(context))
        unfiltered=delivery_context(book,['ivan/masculine_2'],['confident'],examples_per_mood=10)
        self.assertEqual(unfiltered['character_routes'][0]['baseline'],context['character_routes'][0]['baseline'])
        self.assertEqual(unfiltered['policy_version'],context['policy_version'])
        self.assertNotIn('"chapter_id":',json.dumps(context))
        self.assertNotIn('"book":',json.dumps(context))

    def test_empty_filtered_pool_does_not_borrow_another_route(self):
        context=delivery_context(self.book,['ivan/masculine_2'],['confident'],
                                 exclude_example_chapters=[('b1','dev')])
        for rule in context['character_routes'][0]['rules']:
            self.assertEqual([],rule['examples'])
            self.assertEqual([],rule['contrasts'])
            self.assertEqual({},rule['example_context'])
            self.assertEqual({},rule['contrast_moods'])
        self.assertNotIn('Other',json.dumps(context))

    def test_exclusion_refuses_ambiguous_scope_and_missing_provenance(self):
        for invalid in (None,'dev',['dev'],[('b1',)],[(None,'dev')],[('b1','')]):
            with self.assertRaises(ValueError):
                delivery_context(self.book,['ivan/masculine_2'],['confident'],exclude_example_chapters=invalid)
        book=copy.deepcopy(self.book)
        for profile in book['characters']:
            for route in profile['routes']:
                for rule in route['mood_rules']:
                    for example in rule['positive_examples']:
                        example.pop('chapter_id',None)
        with self.assertRaisesRegex(ValueError,'offline provenance'):
            delivery_context(book,['ivan/masculine_2'],['confident'],exclude_example_chapters=[('b1','dev')])

    def test_calibration_chapters_are_excluded_from_all_learned_counts_and_examples(self):
        split=copy.deepcopy(self.split)
        split['books']['b1']['calibration_chapters']=['calibration']
        rows=self.rows + [row('cal','Ivan','masculine_2','calm',chapter='calibration')]
        book,dev=build(rows,split,self.cast,{m:m for m in MOOD_RULES},{})
        self.assertEqual(1,book['coverage']['excluded']['calibration'])
        self.assertNotIn('cal',{r['id'] for r in dev})
        route=next(r for p in book['characters'] if p['id']=='ivan' for r in p['routes'] if r['voice_family']=='masculine_2')
        self.assertEqual({'confident':1,'calm':1},route['mood_counts'])
        self.assertEqual(2,route['development_choices'])
        self.assertEqual(1,route['baseline']['support'])
        self.assertNotIn('Exact text cal',json.dumps(book))
        # Known character director lines from the original full-development
        # policy cannot leak back into the new reduced-development build.
        self.assertNotIn('retrospective storytelling voice',json.dumps(book))
        validate_rulebook(book)

    def test_calibration_partition_cannot_overlap_development_or_test(self):
        for chapter in ('dev','sealed'):
            split=copy.deepcopy(self.split)
            split['books']['b1']['calibration_chapters']=[chapter]
            with self.assertRaisesRegex(ValueError,'disjoint'):
                build(self.rows,split,self.cast,{m:m for m in MOOD_RULES},{})

    def test_fresh_baseline_description_reaches_only_character_specific_arm(self):
        rows,policy=self.merged_fixture()
        written=policy['performers']['Ivan']['dialogue']['instructions']
        written['baseline']={'mood':'calm','description':'Stays baseline even though the situation is dangerous.',
                             'evidence_ids':['i2']}
        written['route_notes']='A reported event is separate from the direct reaction.'
        split=copy.deepcopy(self.split);split['books']['b1']['calibration_chapters']=['calibration']
        book,_=build(rows,split,self.cast,{m:m for m in MOOD_RULES},{},policy)
        specific=delivery_context(book,['ivan/masculine_2'],['confident'])
        general=delivery_context(book,['ivan/masculine_2'],['confident'],include_character_instructions=False)
        self.assertIn('Stays baseline even though',json.dumps(specific))
        self.assertNotIn('Stays baseline even though',json.dumps(general))
        written['baseline']['evidence_ids']=['t1']
        with self.assertRaisesRegex(ValueError,'Baseline citation'):
            build(rows,split,self.cast,{m:m for m in MOOD_RULES},{},policy)

    def test_reduced_policy_resolves_baseline_in_favour_of_supported_written_instructions(self):
        rows,policy=self.merged_fixture()
        rows += [row('n','Ivan','masculine_2','neutral',identity='Ivan',route='dialogue'),
                 row('c1','Ivan','masculine_2','calm',identity='Ivan',route='dialogue'),
                 row('c2','Ivan','masculine_2','calm',identity='Ivan',route='dialogue')]
        policy['performers']['Ivan']['dialogue']['instructions']['baseline']={'mood':'neutral','evidence_ids':['n']}
        split=copy.deepcopy(self.split);split['books']['b1']['calibration_chapters']=['calibration']
        book,_=build(rows,split,self.cast,{m:m for m in MOOD_RULES},{},policy)
        route=next(r for p in book['characters'] if p['id']=='ivan' for r in p['routes'] if r['voice_family']=='masculine_2')
        self.assertEqual('neutral',route['baseline']['mood'])
        self.assertEqual({'calm':3,'neutral':1},route['baseline']['alternatives'])
        self.assertEqual('neutral',book['merge_report']['baseline_resolutions'][0]['selected'])

    def test_merge_rejects_test_or_cross_character_citations(self):
        for citation in ('t1','other1'):
            rows, policy = self.merged_fixture(citation)
            with self.assertRaises(ValueError):
                build(rows,self.split,self.cast,{m:m for m in MOOD_RULES},{},policy)

    def test_two_verified_alias_packets_preserve_both_sets_of_instructions(self):
        rows, policy = self.merged_fixture()
        cast = copy.deepcopy(self.cast)
        cast['characters'][1]['aliases'] = ['AliasIvan']
        rows.append(row('a1','AliasIvan','masculine_2','confident',identity='AliasIvan',route='dialogue'))
        second = copy.deepcopy(policy['performers']['Ivan']['dialogue'])
        second['instructions']['identity'] = 'AliasIvan'
        entry = second['instructions']['moods'][0]
        entry['choose_when'] = ['He gives a settled answer to a challenge.']
        entry['examples'] = [{'id':'a1'}]
        entry['counterexamples'] = []
        policy['performers']['AliasIvan'] = {'dialogue':second}
        book,_ = build(rows,self.split,cast,{m:m for m in MOOD_RULES},{},policy)
        context = delivery_context(book,['ivan/masculine_2'],['confident'])
        rule = next(r for r in context['character_routes'][0]['rules'] if r['mood']=='confident')
        self.assertIn('despite resistance',rule['choose_when'])
        self.assertIn('settled answer',rule['choose_when'])

    def test_merge_drops_stale_unresolved_split_citation(self):
        rows, policy = self.merged_fixture()
        rows.append(row('split','Ivan','masculine_2','confident',identity='unresolved_part',route='unresolved'))
        policy['performers']['Ivan']['dialogue']['instructions']['moods'][0]['examples'].append({'id':'split'})
        book,_ = build(rows,self.split,self.cast,{m:m for m in MOOD_RULES},{},policy)
        self.assertEqual('split',book['merge_report']['unrouted_citations'][0]['id'])
        self.assertNotIn('Exact text split',json.dumps(delivery_context(book,['ivan/masculine_2'],['confident'])))

    def test_documented_aliases_merge_but_short_ambiguous_name_stays_separate(self):
        rows, policy = self.merged_fixture()
        policy['identities']={'Foxi':{'members':['Foxi','Morph'],'basis':'Both cast entries identify the same companion.'}}
        policy['ambiguous_names']={'Ayni':'Translator or disguise; resolve occurrence.'}
        rows += [row('f','Foxi','feminine_3',identity='Foxi',route='dialogue'),
                 row('m','Morph','feminine_3',identity='Foxi',route='dialogue'),
                 row('a','Ayni','feminine_3',identity='Ayni',route='dialogue')]
        book,_=build(rows,self.split,self.cast,{m:m for m in MOOD_RULES},{},policy)
        profiles={p['id']:p for p in book['characters']}
        self.assertEqual(2,profiles['book2:Foxi']['routes'][0]['development_choices'])
        self.assertIn('book2:Ayni',profiles)

    def test_byte_hashes_version_and_cross_character_examples_are_checked(self):
        validate_rulebook(self.book)
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'rulebook.json';path.write_bytes(json_bytes(self.book))
            self.assertEqual(self.book['version'],load_rulebook(path)['version'])
            raw=path.read_bytes()
            self.assertNotIn(b'\r\n',raw)
            self.assertEqual(hashlib.sha256(json_bytes({k:v for k,v in self.book.items() if k!='version'})).hexdigest()[:16],self.book['version'])
            broken=copy.deepcopy(self.book);broken['director_protocol'][0]='tampered'
            path.write_bytes(json_bytes(broken))
            with self.assertRaises(ValueError):load_rulebook(path)
        broken=copy.deepcopy(self.book)
        profile=next(p for p in broken['characters'] if p['id']=='ivan')
        rule=next(r for route in profile['routes'] for r in route['mood_rules'] if r['positive_examples'])
        rule['positive_examples'][0]['policy_character_id']='other'
        broken['version']=hashlib.sha256(json_bytes({k:v for k,v in broken.items() if k!='version'})).hexdigest()[:16]
        with self.assertRaisesRegex(ValueError,'another character'):validate_rulebook(broken)


if __name__=='__main__':unittest.main()
