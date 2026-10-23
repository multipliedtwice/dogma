"""Conflict-safe persistence for global narrator voice-family descriptions."""
from __future__ import annotations

from datetime import datetime, timezone
import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import time
import unicodedata


NARRATOR_ID = 'vlad-rimsky'
REGISTRY_VERSION = 'vlad-rimsky-v1'
REGISTRY_RELATIVE = Path('manual-labeling/voice-family-registries') / REGISTRY_VERSION
EVIDENCE_RELATIVE = Path('training-work/voice-family-registry-evidence') / REGISTRY_VERSION / 'review-data.json'
TAXONOMY_FILE = 'families.json'
LOCK = threading.RLock()
REUSE = {'reusable', 'character_voice', 'split_needed', 'unsure'}
DESCRIPTORS = {
    'perceived_gender': {'masculine', 'feminine', 'neutral', 'unclear'},
    'perceived_age': {'child', 'young', 'adult', 'mature', 'elderly', 'unclear'},
    'voice_weight': {'light', 'medium', 'heavy', 'unclear'},
    'voice_depth': {'low', 'medium', 'high', 'unclear'},
    'brightness': {'dark', 'balanced', 'bright', 'unclear'},
    'roughness': {'smooth', 'slightly_rough', 'rough', 'unclear'},
    'voice_quality': {'normal', 'bright', 'dark', 'soft', 'rough', 'nasal', 'mechanical', 'other', 'unclear'},
    'otherness': {'human', 'non_human', 'mechanical', 'unclear'},
}
TEXT_FIELDS = ('casting_description', 'character_fit', 'avoid_for', 'notes')


@contextlib.contextmanager
def process_lock(root: Path, timeout: float = 10.0):
    """Serialize registry writers across the separate Book 1/2/3 servers."""
    folder = registry_folder(root)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / '.registry.lock'
    handle = path.open('a+b')
    if path.stat().st_size == 0:
        handle.write(b'0')
        handle.flush()
    acquired = False
    deadline = time.monotonic() + timeout
    try:
        while not acquired:
            handle.seek(0)
            try:
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError('Another voice-family registry save is still running')
                time.sleep(.025)
        yield
    finally:
        if acquired:
            handle.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()


class RegistryConflict(RuntimeError):
    pass


class TaxonomyConflict(RuntimeError):
    pass


def atomic_json(path: Path, value: dict) -> bytes:
    raw = (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    try:
        with os.fdopen(handle, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return raw


def empty_review() -> dict:
    return {'schema': 1, 'narrator_id': NARRATOR_ID,
            'registry_version': REGISTRY_VERSION, 'decisions': {}}


def registry_folder(root: Path) -> Path:
    return root / REGISTRY_RELATIVE


def ensure_archive(root: Path) -> None:
    folder = registry_folder(root)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / 'review.json'
    if not target.exists():
        raw = atomic_json(target, empty_review())
        update_latest(root, schema=1, review_sha256=hashlib.sha256(raw).hexdigest(),
                      updated_at=None)


def update_latest(root: Path, **fields) -> None:
    """Update one checksum pointer without dropping the registry's other pointer."""
    folder = registry_folder(root)
    path = folder / 'latest.json'
    latest = json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {'schema': 1}
    atomic_json(path, {**latest, **fields})


def read_review(root: Path) -> dict:
    with LOCK:
        ensure_archive(root)
        return json.loads((registry_folder(root) / 'review.json').read_text(encoding='utf-8'))


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _valid_text(value, name, maximum):
    if not isinstance(value, str):
        raise ValueError('Invalid ' + name)
    value = ' '.join(value.split())
    if not 2 <= len(value) <= maximum or any(unicodedata.category(char) == 'Cc' for char in value):
        raise ValueError('Invalid ' + name)
    return value


CYRILLIC = str.maketrans({
    'а':'a','б':'b','в':'v','г':'g','д':'d','е':'e','ё':'yo','ж':'zh','з':'z','и':'i','й':'y',
    'к':'k','л':'l','м':'m','н':'n','о':'o','п':'p','р':'r','с':'s','т':'t','у':'u','ф':'f',
    'х':'h','ц':'ts','ч':'ch','ш':'sh','щ':'shch','ъ':'','ы':'y','ь':'','э':'e','ю':'yu','я':'ya',
})


def proposed_family_id(display_name: str) -> str:
    """Return the deterministic preview ID; the server always recalculates it."""
    text = unicodedata.normalize('NFKD', display_name.casefold().translate(CYRILLIC))
    text = ''.join(char for char in text if not unicodedata.combining(char))
    slug = re.sub(r'[^a-z0-9]+', '_', text).strip('_')
    slug = re.sub(r'_+', '_', slug)[:56].rstrip('_')
    if not slug:
        raise ValueError('Display name cannot produce a stable family ID')
    result = 'custom_' + slug
    if not re.fullmatch(r'[a-z][a-z0-9_]{2,63}', result):
        raise ValueError('Invalid generated family ID')
    return result


def _base_taxonomy(root: Path) -> tuple[dict, str]:
    path = root / 'training/voice_families.json'
    data = json.loads(path.read_text(encoding='utf-8'))
    families = data.get('families')
    if data.get('schema') != 1 or not isinstance(families, list) or len(families) != 20:
        raise ValueError('Expected the 20-family production voice taxonomy')
    compact = []
    for family in families:
        if (not isinstance(family, dict) or not isinstance(family.get('id'), str)
                or not isinstance(family.get('label'), str) or not isinstance(family.get('group'), str)):
            raise ValueError('Invalid voice taxonomy family')
        compact.append({key: family[key] for key in ('id', 'label', 'group')})
    if len({family['id'] for family in compact}) != len(compact):
        raise ValueError('Duplicate voice taxonomy family')
    return {'schema': 1, 'narrator_id': NARRATOR_ID, 'registry_version': REGISTRY_VERSION,
            'base_taxonomy_sha256': _file_sha256(path), 'revision': 0,
            'updated_at': None, 'families': compact, 'audit': [],
            'legacy_aliases': data.get('legacy_aliases', {})}, _file_sha256(path)


def _validate_taxonomy(document: dict, base: dict, base_sha256: str) -> None:
    families = document.get('families')
    if (document.get('schema') != 1 or document.get('narrator_id') != NARRATOR_ID
            or document.get('registry_version') != REGISTRY_VERSION
            or document.get('base_taxonomy_sha256') != base_sha256
            or not isinstance(families, list) or not isinstance(document.get('audit'), list)
            or type(document.get('revision')) is not int
            or document['revision'] != len(document['audit'])):
        raise ValueError('Invalid canonical voice taxonomy')
    base_ids = [family['id'] for family in base['families']]
    if [family.get('id') for family in families[:len(base_ids)]] != base_ids:
        raise ValueError('The original 20 voice-family IDs changed')
    seen_ids, seen_labels = set(), set()
    for family in families:
        if (not isinstance(family, dict) or not re.fullmatch(r'[a-z][a-z0-9_]{2,63}', family.get('id', ''))
                or not isinstance(family.get('label'), str) or not isinstance(family.get('group'), str)
                or family['id'] in seen_ids or family['label'].casefold() in seen_labels):
            raise ValueError('Invalid or duplicate canonical voice family')
        seen_ids.add(family['id']);seen_labels.add(family['label'].casefold())


def _read_taxonomy(root: Path) -> dict:
    base, base_sha256 = _base_taxonomy(root)
    folder = registry_folder(root)
    path = folder / TAXONOMY_FILE
    if not path.is_file():
        document = base
        checksum = hashlib.sha256(json.dumps(document, ensure_ascii=False, sort_keys=True,
                                               separators=(',', ':')).encode('utf-8')).hexdigest()
    else:
        latest_path = folder / 'latest.json'
        if not latest_path.is_file():
            raise ValueError('Canonical voice taxonomy checksum is missing')
        latest = json.loads(latest_path.read_text(encoding='utf-8'))
        checksum = _file_sha256(path)
        if latest.get('families_sha256') != checksum:
            raise ValueError('Canonical voice taxonomy checksum mismatch')
        document = json.loads(path.read_text(encoding='utf-8'))
    _validate_taxonomy(document, base, base_sha256)
    return {**document, 'families_sha256': checksum}


def taxonomy(root: Path) -> dict:
    """Return the checksummed canonical taxonomy without modifying source taxonomy."""
    with LOCK:
        return _read_taxonomy(root)


def create_family(root: Path, payload: dict) -> dict:
    if (not isinstance(payload, dict) or payload.get('schema') != 1
            or payload.get('narrator_id') != NARRATOR_ID
            or payload.get('registry_version') != REGISTRY_VERSION):
        raise ValueError('Family request does not match this registry')
    label = _valid_text(payload.get('display_name'), 'display name', 120)
    group = _valid_text(payload.get('group'), 'group', 80)
    family_id = proposed_family_id(label)
    preview = payload.get('id_preview')
    if preview is not None and (not isinstance(preview, str)
                                or not re.fullmatch(r'[a-z][a-z0-9_]{2,63}', preview)):
        raise ValueError('Invalid family ID preview')
    with LOCK, process_lock(root):
        ensure_archive(root)
        current = taxonomy(root)
        if (payload.get('before_revision') != current['revision']
                or payload.get('before_families_sha256') != current['families_sha256']):
            raise TaxonomyConflict('The family list changed in another tab. Reload it before creating a family.')
        groups = {family['group'] for family in current['families']}
        if group not in groups:
            raise ValueError('Choose an existing canonical family group')
        if family_id == 'voice_pending' or family_id in current.get('legacy_aliases', {}):
            raise ValueError('Generated family ID is reserved')
        if any(family['id'] == family_id for family in current['families']):
            raise TaxonomyConflict('A family with this stable ID already exists.')
        if any(family['label'].casefold() == label.casefold() for family in current['families']):
            raise TaxonomyConflict('A family with this display name already exists.')
        now = datetime.now(timezone.utc).isoformat()
        family = {'id': family_id, 'label': label, 'group': group,
                  'origin': 'listener_created', 'synthesis_status': 'requires_pack_voice',
                  'created_at': now}
        revision = current['revision'] + 1
        audit = [*current['audit'], {'revision': revision, 'action': 'create_family',
                 'family': family, 'previous_families_sha256': current['families_sha256'],
                 'created_at': now}]
        document = {key: current[key] for key in ('schema', 'narrator_id', 'registry_version',
                    'base_taxonomy_sha256', 'legacy_aliases')}
        document.update(revision=revision, updated_at=now,
                        families=[*current['families'], family], audit=audit)
        folder = registry_folder(root)
        raw = atomic_json(folder / TAXONOMY_FILE, document)
        checksum = hashlib.sha256(raw).hexdigest()
        update_latest(root, schema=1, families_sha256=checksum,
                      families_revision=revision, updated_at=now)
        return {**document, 'families_sha256': checksum}


def _evidence(root: Path, family_id: str) -> tuple[list[str] | None, str | None]:
    path = root / EVIDENCE_RELATIVE
    if not path.is_file():
        return None, None
    data = json.loads(path.read_text(encoding='utf-8'))
    family = next((row for row in data.get('families', []) if row.get('id') == family_id), None)
    if not family or not family.get('palette_examples_sha256'):
        return None, None
    return family['palette_examples_sha256'], data.get('palette_bank_sha256')


def save_decision(root: Path, payload: dict) -> dict:
    if (not isinstance(payload, dict) or payload.get('schema') != 1
            or payload.get('narrator_id') != NARRATOR_ID
            or payload.get('registry_version') != REGISTRY_VERSION):
        raise ValueError('Decision does not match this registry')
    family_id = payload.get('family_id')
    incoming = payload.get('decision')
    if not isinstance(incoming, dict):
        raise ValueError('Invalid decision')
    with LOCK, process_lock(root):
        catalog = taxonomy(root)
        if family_id not in {family['id'] for family in catalog['families']}:
            raise ValueError('Decision does not match this registry')
        folder = registry_folder(root)
        current = read_review(root)
        before = current.get('decisions', {}).get(family_id, {})
        revision = payload.get('before_revision', 0)
        if type(revision) is not int or revision != before.get('_revision', 0):
            raise RegistryConflict('This family changed in another tab. Reload its saved description.')
        verdict = incoming.get('reuse_verdict')
        if verdict is not None and verdict not in REUSE:
            raise ValueError('Unknown family conclusion')
        descriptors = incoming.get('descriptors', {})
        if not isinstance(descriptors, dict) or set(descriptors) - set(DESCRIPTORS):
            raise ValueError('Invalid descriptors')
        for name, value in descriptors.items():
            if value not in DESCRIPTORS[name]:
                raise ValueError(f'Invalid {name}')
        text = {}
        for name in TEXT_FIELDS:
            value = incoming.get(name, '')
            if not isinstance(value, str) or len(value) > 4000:
                raise ValueError('Invalid ' + name)
            if value.strip():
                text[name] = value.strip()
        now = datetime.now(timezone.utc).isoformat()
        decision = {
            **({'reuse_verdict': verdict} if verdict else {}),
            **({'descriptors': descriptors} if descriptors else {}),
            **text,
            '_revision': before.get('_revision', 0) + 1,
            'reviewed_at': now,
        }
        evidence, bank = _evidence(root, family_id)
        if evidence:
            decision['audio_evidence_sha256'] = evidence
            decision['palette_bank_sha256'] = bank
        decisions = dict(current.get('decisions', {}))
        decisions[family_id] = decision
        result = {**current, 'schema': 1, 'narrator_id': NARRATOR_ID,
                  'registry_version': REGISTRY_VERSION, 'updated_at': now,
                  'decisions': decisions}
        raw = atomic_json(folder / 'review.json', result)
        update_latest(root, schema=1, review_sha256=hashlib.sha256(raw).hexdigest(),
                      updated_at=now)
        return result
