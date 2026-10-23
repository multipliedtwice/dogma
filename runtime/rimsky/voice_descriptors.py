"""Validated sound catalogs used by casting; never a corpus-label exporter."""
import json
from pathlib import Path
from .util import file_digest,digest

VALUES={'voice_weight':{'light','medium','heavy','unclear'},
        'voice_depth':{'low','medium','high','unclear'},
        'brightness':{'dark','balanced','bright','unclear'},
        'roughness':{'smooth','slightly_rough','rough','unclear'},
        'perceived_age':{'child','young','adult','mature','elderly','unclear'},
        'performed_gender':{'masculine','feminine','neutral','unclear'},
        'voice_quality':{'normal','bright','dark','soft','rough','nasal','mechanical','other','unclear'},
        'otherness':{'human','non_human','mechanical','unclear'}}
REQUIRED_MEASURED={'voice_weight','voice_depth','brightness','roughness','perceived_age','performed_gender'}


def load_catalog(path):
    data=json.loads(path.read_text('utf-8'))
    if data.get('schema')!=1 or data.get('kind')!='voice_family_sound_catalog':
        raise ValueError('Invalid sound descriptor catalog')
    if file_digest(path.with_name('measurements.json'))!=data['measurement_sha256']:
        raise ValueError('Descriptor measurements changed')
    for family,row in data['families'].items():
        if not REQUIRED_MEASURED<=set(row['descriptors']):
            raise ValueError('Incomplete sound descriptors: '+family)
        for key,value in row['descriptors'].items():
            if key not in VALUES or value not in VALUES[key]:raise ValueError('Invalid sound descriptor value')
        if row.get('reviewed') is not False or not row.get('basis') or not row.get('audio_evidence_sha256'):
            raise ValueError('Automated descriptor must retain its evidence and unreviewed status')
    return {'sha256':file_digest(path),'bank_sha256':data['bank_sha256'],
            'families':data['families'],'basis':'automated_evidence_bound_catalog'}


def validate_catalog_palette(catalog,groups,bank_sha256):
    if catalog.get('bank_sha256')!=bank_sha256:raise ValueError('Voice descriptors use a different palette')
    by_family={}
    for group in groups.values():
        by_family.setdefault(group['voice_family'],set()).update(
            r['audio_sha256'] for rows in group['moods'].values() for r in rows)
    for family,row in catalog['families'].items():
        if not set(row['audio_evidence_sha256'])<=by_family.get(family,set()):
            raise ValueError('Descriptor references are outside the reviewed family palette')


def validate_descriptor_fit(choice,group,descriptions):
    fit=choice['voice_fit'];family=group['voice_family']
    if fit['descriptor_family']!=family:raise ValueError('Sound citation belongs to another voice family')
    row=descriptions[family];dimensions=fit['supporting_dimensions']
    if len(dimensions)!=len(set(dimensions)):
        raise ValueError('Casting requires unique supporting sound dimensions')
    if not dimensions and not (choice['needs_review'] and fit['missing_traits']):
        raise ValueError('Unsupported cast must abstain with disclosed missing traits')
    for dimension in dimensions:
        if row['descriptors'].get(dimension,'unclear')=='unclear':
            raise ValueError('Unknown sound trait cannot support casting: '+dimension)
    if fit['missing_traits'] and not choice['needs_review']:
        raise ValueError('Unmet vocal requirements must remain disclosed for review')


def apply_listener_overrides(catalog,reviewed):
    """Layer explicit listening decisions without changing measured evidence."""
    for family,human in reviewed['families'].items():
        if family not in catalog['families']:continue
        original=catalog['families'][family]
        human_traits=dict(human['descriptors'])
        if 'perceived_gender' in human_traits:human_traits['performed_gender']=human_traits.pop('perceived_gender')
        basis={**original.get('trait_basis',{}),**{key:{'kind':'explicit_listener'} for key in human_traits}}
        catalog['families'][family]={**original,**human,
            'descriptors':{**original['descriptors'],**human_traits},'trait_basis':basis,
            'basis':'explicit_listener_overrides_measured_audio',
            'audio_evidence_sha256':original['audio_evidence_sha256']}
    catalog['sha256']=digest({'catalog':catalog['sha256'],'human_registry':reviewed['sha256']})
    return catalog


def default_catalog(root,reviewed,catalog_path=None):
    """New casts use a measured catalog plus hash-bound explicit human overrides."""
    if catalog_path is None:
        pointer_path=root/'training-work/higgs-production/voice-descriptors/latest.json'
        if not pointer_path.exists():return reviewed
        pointer=json.loads(pointer_path.read_text('utf-8'))
        path=(pointer_path.parent/pointer['catalog']).resolve()
        if not path.is_relative_to(pointer_path.parent.resolve()) or file_digest(path)!=pointer['sha256']:
            raise ValueError('Default sound catalog changed or escaped its directory')
    else:
        path=Path(catalog_path).resolve()
    return apply_listener_overrides(load_catalog(path),reviewed)


def resolved_catalog(root, catalog_path=None, review_path=None):
    """Resolve the measured catalog and listener registry into one hash-bound view."""
    from .listener_directions import family_descriptions
    root=Path(root)
    review_path=Path(review_path) if review_path else root/'manual-labeling/voice-family-registries/vlad-rimsky-v1/review.json'
    if not review_path.is_file():
        if catalog_path is not None:raise ValueError('Voice-family listener registry is missing')
        return None
    reviewed=family_descriptions(review_path)
    return default_catalog(root,reviewed,catalog_path)


def casting_guidance(root, catalog_path=None, review_path=None, available_families=None):
    """Small immutable casting input; raw reviewer notes are deliberately excluded."""
    catalog=resolved_catalog(root,catalog_path,review_path)
    if catalog is None:return None
    fields=('casting_description','character_fit','avoid_for')
    available=set(available_families) if available_families is not None else None
    families={family:{'descriptors':dict(row.get('descriptors',{})),
                      **{key:row.get(key,'') for key in fields},
                      'basis':row.get('basis','')}
              for family,row in catalog.get('families',{}).items()
              if available is None or family in available}
    result={'schema':1,'catalog_sha256':catalog['sha256'],'families':families}
    result['guidance_sha256']=digest(result)
    return result
