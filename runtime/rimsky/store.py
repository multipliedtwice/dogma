from __future__ import annotations
import contextlib
import itertools
import json
import math
import re
import shutil
import sqlite3
import threading
import unicodedata
from pathlib import Path
from .books import import_book
from .preparation import narratable, preparation_report, speech_sources
from .integrity import chapter_selection, validate_coverage
from .recommendations import GENRE_IDS
from .respelling import validate_respelled_stress
from .util import AppError, atomic_write, bounded_text, digest, now, require_id, uid

SCHEMA = '''
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
BEGIN IMMEDIATE;
CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY CHECK(id=1), value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS books (id TEXT PRIMARY KEY, title TEXT, author TEXT, added TEXT, manifest TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS generations (id TEXT PRIMARY KEY, book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE, name TEXT, created TEXT, updated TEXT, status TEXT, config TEXT, cast TEXT NOT NULL DEFAULT '[]', analysis TEXT NOT NULL DEFAULT '{}', error TEXT NOT NULL DEFAULT '', parent_id TEXT, resume_on_launch INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS segments (generation_id TEXT NOT NULL REFERENCES generations(id) ON DELETE CASCADE, id TEXT NOT NULL, ordinal INTEGER, chapter_id TEXT, source_id TEXT, start INTEGER, end INTEGER, text TEXT, spoken TEXT, speaker TEXT NOT NULL DEFAULT 'narrator', delivery TEXT NOT NULL DEFAULT 'neutral', status TEXT NOT NULL DEFAULT 'pending', audio_hash TEXT, duration REAL NOT NULL DEFAULT 0, cues TEXT NOT NULL DEFAULT '[]', warning TEXT NOT NULL DEFAULT '', PRIMARY KEY(generation_id,id));
CREATE INDEX IF NOT EXISTS segments_order ON segments(generation_id,ordinal);
CREATE TABLE IF NOT EXISTS positions (book_id TEXT PRIMARY KEY REFERENCES books(id) ON DELETE CASCADE, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS reading_positions (book_id TEXT PRIMARY KEY REFERENCES books(id) ON DELETE CASCADE, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS bookmarks (id TEXT PRIMARY KEY, book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE, source_id TEXT, note TEXT, created TEXT);
CREATE TABLE IF NOT EXISTS stress_reports (id TEXT PRIMARY KEY, book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE, generation_id TEXT NOT NULL, segment_id TEXT NOT NULL, chapter_id TEXT NOT NULL, source_id TEXT NOT NULL, selected_text TEXT NOT NULL, text_offset INTEGER, audio_offset REAL, audio_hash TEXT, passage_text TEXT NOT NULL, spoken_text TEXT NOT NULL, created TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'stress', comment TEXT NOT NULL DEFAULT '', intended_stress TEXT, synthesis_snapshot TEXT);
CREATE INDEX IF NOT EXISTS stress_reports_book ON stress_reports(book_id,created);
CREATE TABLE IF NOT EXISTS listener_correction_jobs (
    id TEXT PRIMARY KEY,report_id TEXT NOT NULL,book_id TEXT NOT NULL,
    generation_id TEXT NOT NULL REFERENCES generations(id) ON DELETE CASCADE,
    segment_ids TEXT NOT NULL,status TEXT NOT NULL,created TEXT NOT NULL,
    updated TEXT NOT NULL,error TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS series_character_state (
    series_id TEXT NOT NULL,
    character_key TEXT NOT NULL,
    series_name TEXT NOT NULL,
    author TEXT NOT NULL,
    canonical_name TEXT NOT NULL,
    aliases TEXT NOT NULL DEFAULT '[]',
    state TEXT NOT NULL DEFAULT '{}',
    created TEXT NOT NULL,
    updated TEXT NOT NULL,
    PRIMARY KEY(series_id,character_key)
);
CREATE INDEX IF NOT EXISTS series_character_state_name ON series_character_state(series_id,canonical_name);
CREATE TABLE IF NOT EXISTS visuals (id TEXT PRIMARY KEY, book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE, source_id TEXT, caption TEXT, file TEXT, kind TEXT, created TEXT);
CREATE INDEX IF NOT EXISTS visuals_source ON visuals(book_id,source_id);
CREATE TABLE IF NOT EXISTS exports (id TEXT PRIMARY KEY, generation_id TEXT NOT NULL REFERENCES generations(id) ON DELETE CASCADE, file TEXT, format TEXT, created TEXT);
CREATE TABLE IF NOT EXISTS generation_versions (id TEXT PRIMARY KEY REFERENCES generations(id) ON DELETE CASCADE, revision INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS generation_segment_changes (generation_id TEXT NOT NULL REFERENCES generations(id) ON DELETE CASCADE, segment_id TEXT NOT NULL, revision INTEGER NOT NULL, deleted INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(generation_id,segment_id));
CREATE INDEX IF NOT EXISTS generation_segment_changes_revision ON generation_segment_changes(generation_id,revision);
INSERT OR IGNORE INTO generation_versions(id) SELECT id FROM generations;
CREATE TRIGGER IF NOT EXISTS generation_version_insert AFTER INSERT ON generations BEGIN INSERT INTO generation_versions(id) VALUES(NEW.id); END;
CREATE TRIGGER IF NOT EXISTS generation_version_update AFTER UPDATE ON generations BEGIN UPDATE generation_versions SET revision=revision+1 WHERE id=NEW.id; END;
CREATE TABLE IF NOT EXISTS illustration_runs (id TEXT PRIMARY KEY, book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE, created TEXT NOT NULL, updated TEXT NOT NULL, status TEXT NOT NULL, state TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS book_visual_versions (id TEXT PRIMARY KEY REFERENCES books(id) ON DELETE CASCADE, revision INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS book_visual_changes (book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE, visual_id TEXT NOT NULL, revision INTEGER NOT NULL, deleted INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(book_id,visual_id));
CREATE INDEX IF NOT EXISTS book_visual_changes_revision ON book_visual_changes(book_id,revision);
INSERT OR IGNORE INTO book_visual_versions(id) SELECT id FROM books;
CREATE TRIGGER IF NOT EXISTS book_visual_version_insert AFTER INSERT ON books BEGIN INSERT INTO book_visual_versions(id) VALUES(NEW.id); END;
CREATE TRIGGER IF NOT EXISTS visual_version_insert AFTER INSERT ON visuals BEGIN UPDATE book_visual_versions SET revision=revision+1 WHERE id=NEW.book_id; END;
CREATE TRIGGER IF NOT EXISTS visual_version_update AFTER UPDATE ON visuals BEGIN UPDATE book_visual_versions SET revision=revision+1 WHERE id=NEW.book_id; END;
CREATE TRIGGER IF NOT EXISTS visual_version_delete AFTER DELETE ON visuals BEGIN UPDATE book_visual_versions SET revision=revision+1 WHERE id=OLD.book_id; END;
PRAGMA user_version=5;
'''
DEFAULTS = {'pack_path': '', 'device': 'auto', 'theme': 'light', 'font_size': 20, 'line_height': 1.9, 'language': 'en', 'playback_rate': 1.0, 'volume': 0.85, 'follow': True, 'online_images': False, 'image_model': 'gpt-image-1', 'align_words': False, 'character_analysis': 'auto', 'speaker_tie_breaker': 'none', 'unresolved_policy': 'hold_flagged_segments', 'cast_review': True, 'music': True, 'music_volume': 1.0, 'music_pack_path': '', 'online_recommendations': False, 'recommendation_genres': []}
POLLED_ANALYSIS = ('complete', 'casting_warnings', 'casting_notices', 'progress', 'degraded', 'approved', 'narrator_voices',
                   'speaker_resolution', 'speaker_resolution_metrics', 'speaker_review_holds', 'speaker_verdicts')
ACTIVE = ('queued', 'analyzing', 'casting', 'rendering', 'aligning')
HOLD_NOTICE = 'Held until the speaker is reviewed.'
NEIGHBOUR_NOTICE = 'Held next to a speaker-review gap.'
PRESENTATION_NOTICE = 'Held until its presentation voice is reviewed.'
PREVIEW_NOTICE = 'Unreviewed preview; this passage is still held for review.'
HOLD_CODES = ('degraded_analysis', 'unresolved_speaker', 'neighbor_only_evidence', 'backend_limit', 'presentation_review', 'render_degraded')
# A degraded render (planned controls removed, or a stand-in voice) is cleared
# only by a new take; a speaker verdict cannot accept it.
RENDER_HOLD = 'render_degraded'
VERDICT_LOCKED = frozenset({'backend_limit', RENDER_HOLD})
PRESENTATION = 'presentation_review'
# Concerns about one passage's own capacity or take say nothing about its neighbours.
UNSPREAD = frozenset({'critic_unavailable', 'render_degraded'})

def visible_chapters(manifest):
    return [chapter for chapter in manifest['chapters'] if any(
        block.get('kind') != 'separator' and (block.get('text', '').strip() or block.get('assets') or '<svg' in block.get('html', ''))
        for block in chapter.get('blocks', []))]

def discard(path):
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path, ignore_errors=True)
    else:
        path.unlink(missing_ok=True)

def overlaps(item, segment):
    return (item.get('source_id') == segment.get('source_id') and item.get('start', segment['end']) < segment['end']
            and item.get('end', segment['start']) > segment['start'])

def verified(segment, verdicts):
    verdict = (verdicts or {}).get(segment['id'])
    return isinstance(verdict, dict) and segment['speaker'] != 'unknown' and verdict.get('speaker') == segment['speaker']

def mixed_parts(segment):
    parts = (segment.get('performance') or {}).get('analysis_parts') or []
    return parts if (segment.get('performance') or {}).get('internal_transition') and len(parts) > 1 else []

def part_verified(segment, index, part, verdicts):
    if part.get('speaker') in (None, 'unknown'):
        return False
    verdict = (verdicts or {}).get(f'{segment["id"]}#{index}')
    if isinstance(verdict, dict) and verdict.get('speaker') == part['speaker']:
        return True
    return verified(segment, verdicts) and part['speaker'] == segment['speaker']

def cleared(item, segment, verdicts):
    parts = mixed_parts(segment)
    if not parts:
        return verified(segment, verdicts)
    touched = [(index, part) for index, part in enumerate(parts) if part.get('start', 0) < item.get('end', 0) and item.get('start', 0) < part.get('end', 0)]
    return bool(touched) and all(part_verified(segment, index, part, verdicts) for index, part in touched)

def fully_verified(segment, verdicts):
    parts = mixed_parts(segment)
    return all(part_verified(segment, index, part, verdicts) for index, part in enumerate(parts)) if parts else verified(segment, verdicts)

def concern(item):
    triggers = set(item.get('triggers') or ())
    return 'backend' if VERDICT_LOCKED & triggers else 'presentation' if PRESENTATION in triggers else 'identity'

def bindings(segment):
    entries = (segment.get('performance') or {}).get('presentation_binding')
    return [entry for entry in entries if isinstance(entry, dict)] if isinstance(entries, list) else []

def presentation_key(sid, part=None):
    return f'{sid}@presentation' if part is None else f'{sid}#{part}@presentation'

def presentation_cleared(item, segment, verdicts):
    touched = [entry for entry in bindings(segment) if entry.get('start', 0) < item.get('end', 0) and item.get('start', 0) < entry.get('end', 0)]
    return bool(touched) and all(((verdicts or {}).get(presentation_key(segment['id'], entry.get('part'))) or {}).get('binding') == entry for entry in touched)

def item_open(item, segment, verdicts):
    kind = concern(item)
    return kind == 'backend' or not (presentation_cleared if kind == 'presentation' else cleared)(item, segment, verdicts)

def review_items(segment, resolutions, verdicts=None):
    return [item for item in resolutions if item.get('status') == 'review' and overlaps(item, segment) and item_open(item, segment, verdicts)]

def displayed_speaker(parts):
    if any(part.get('speaker') == 'unknown' for part in parts):
        return 'unknown'
    weights = {}
    for part in parts:
        if part.get('speaker') != 'narrator':
            weights[part['speaker']] = weights.get(part['speaker'], 0) + len(part.get('text', ''))
    return max(weights, key=lambda cid: (weights[cid], -next(index for index, part in enumerate(parts) if part['speaker'] == cid))) if weights else 'narrator'

def trusted_inputs(inputs, resolutions, segments=(), verdicts=None):
    """Trusted sub-ranges of casting inputs; only these may become accepted-speaker hints.

    A range under an open identity review stays untrusted unless human identity verdicts cover that exact range with
    the same speaker, segment by segment and part by part; a verified prefix does not promote a held suffix.
    Presentation and backend holds do not concern identity. Pending proposals never count as verified continuity.
    """
    open_items = [item for item in resolutions if item.get('status') == 'review' and concern(item) == 'identity']
    def verified_spans(entry, start, end):
        spans = []
        for segment in segments:
            if segment.get('source_id') != entry['source_id'] or not (segment['start'] < end and segment['end'] > start):
                continue
            parts = mixed_parts(segment)
            if not parts:
                if verified(segment, verdicts) and segment['speaker'] == entry['speaker']:
                    spans.append((segment['start'], segment['end']))
                continue
            spans.extend((part.get('start', 0), part.get('end', 0)) for index, part in enumerate(parts)
                         if part.get('speaker') == entry['speaker'] and part_verified(segment, index, part, verdicts))
        return spans
    def subtract(ranges, spans):
        for left, right in spans:
            ranges = [piece for lo, hi in ranges for piece in ((lo, min(hi, left)), (max(lo, right), hi)) if piece[0] < piece[1]]
        return ranges
    output = []
    for entry in inputs:
        start, end = entry['start'], entry['start'] + entry.get('characters', 0)
        overlapping = [item for item in open_items if item.get('source_id') == entry['source_id'] and item.get('start', end) < end and item.get('end', start) > start]
        if not overlapping:
            output.append(entry)
            continue
        spans = verified_spans(entry, start, end)
        untrusted = [piece for item in overlapping for piece in subtract([(max(start, item['start']), min(end, item['end']))], spans)]
        output.extend({**entry, 'start': lo, 'characters': hi - lo} for lo, hi in subtract([(start, end)], untrusted))
    return output

def speaker_review_holds(segments, resolutions, verdicts=None):
    items = {segment['id']: review_items(segment, resolutions, verdicts) for segment in segments}
    flagged = {sid for sid, open_items in items.items() if open_items}
    # A missing critic is a capacity failure, not evidence that adjacent narration is
    # uncertain. Keep the affected speech visible for review without holding its
    # neighbours. Identity and presentation concerns still retain their context hold.
    spread = {sid for sid, open_items in items.items()
              if any(not (set(item.get('triggers') or ()) and set(item.get('triggers')) <= UNSPREAD) for item in open_items)}
    held = set(flagged)
    for index, segment in enumerate(segments):
        if segment['id'] in spread:
            for other in (*segments[max(0, index - 1):index], *segments[index + 1:index + 2]):
                if other.get('chapter_id') == segment.get('chapter_id') and not fully_verified(other, verdicts):
                    held.add(other['id'])
    return flagged, held

def hold_codes(segment, resolutions, verdicts=None):
    return list(dict.fromkeys(next((trigger for trigger in item.get('triggers') or () if trigger in HOLD_CODES), 'speaker_review')
                              for item in review_items(segment, resolutions, verdicts)))

def hold_summary(segments, resolutions, verdicts=None):
    flagged, held = speaker_review_holds(segments, resolutions, verdicts)
    reasons = {}
    for segment in segments:
        for code in hold_codes(segment, resolutions, verdicts) if segment['id'] in flagged else ['neighbour'] if segment['id'] in held else []:
            reasons[code] = reasons.get(code, 0) + 1
    return {'flagged_segments': len(flagged), 'held_with_neighbours': len(held), 'reasons': reasons, 'flagged_ids': sorted(flagged), 'held_ids': sorted(held)}

def hold_warning(warning, notice=''):
    for held in (HOLD_NOTICE, NEIGHBOUR_NOTICE, PRESENTATION_NOTICE, PREVIEW_NOTICE):
        warning = warning.replace(held, ' ')
    text = ' '.join(warning.split())
    return ' '.join(part for part in (text, notice) if part)

def revision_name(name, maximum=200):
    match = re.fullmatch(r'(.*?) · revision(?: (\d{1,9}))?', name, re.S)
    base, number = (match.group(1), int(match.group(2) or 1) + 1) if match else (name, 1)
    suffix = ' · revision' + (f' {number}' if number > 1 else '')
    return base[:maximum - len(suffix)].rstrip() + suffix

class Store:
    def __init__(self, root: Path, *, recover_interrupted=False, resources: Path | None = None, correction_profile: Path | None = None):
        self.correction_profile=Path(correction_profile) if correction_profile is not None else Path(root)/'user-stress-corrections.sqlite3'
        self.root = root.resolve()
        self.resources = resources.resolve() if resources is not None else None
        for folder in ('books', 'audio', 'exports', 'visuals', 'work'):
            (self.root / folder).mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.root / 'library.sqlite3', check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        try:
            with self.lock:
                version = self.db.execute('PRAGMA user_version').fetchone()[0]
                if version > 5:
                    raise AppError('This library was created by a newer application. Its data was not changed.')
                self.db.executescript(SCHEMA)
                report_columns = {row['name'] for row in self.db.execute('PRAGMA table_info(stress_reports)')}
                if 'kind' not in report_columns:
                    self.db.execute("ALTER TABLE stress_reports ADD COLUMN kind TEXT NOT NULL DEFAULT 'stress'")
                if 'comment' not in report_columns:
                    self.db.execute("ALTER TABLE stress_reports ADD COLUMN comment TEXT NOT NULL DEFAULT ''")
                if 'intended_stress' not in report_columns:
                    self.db.execute("ALTER TABLE stress_reports ADD COLUMN intended_stress TEXT")
                if 'synthesis_snapshot' not in report_columns:
                    self.db.execute("ALTER TABLE stress_reports ADD COLUMN synthesis_snapshot TEXT")
                if 'heard_binding' not in report_columns:
                    self.db.execute("ALTER TABLE stress_reports ADD COLUMN heard_binding TEXT NOT NULL DEFAULT 'legacy_unknown'")
                if 'heard_revision' not in report_columns:self.db.execute('ALTER TABLE stress_reports ADD COLUMN heard_revision INTEGER')
                segment_columns = {row['name'] for row in self.db.execute('PRAGMA table_info(segments)')}
                if 'performance' not in segment_columns:
                    self.db.execute("ALTER TABLE segments ADD COLUMN performance TEXT NOT NULL DEFAULT '{}'")
                from .current_audiobook import install
                install(self.db)
                generation_columns = {row['name'] for row in self.db.execute('PRAGMA table_info(generations)')}
                if 'resume_on_launch' not in generation_columns:
                    self.db.execute('ALTER TABLE generations ADD COLUMN resume_on_launch INTEGER NOT NULL DEFAULT 0')
                for operation, row in (('INSERT', 'NEW'), ('UPDATE', 'NEW'), ('DELETE', 'OLD')):
                    name=f'segments_version_{operation.lower()}'
                    deleted=1 if operation == 'DELETE' else 0
                    self.db.execute(f'DROP TRIGGER IF EXISTS {name}')
                    self.db.execute(f'''CREATE TRIGGER {name} AFTER {operation} ON segments BEGIN
                        UPDATE generation_versions SET revision=revision+1 WHERE id={row}.generation_id;
                        INSERT INTO generation_segment_changes(generation_id,segment_id,revision,deleted)
                        SELECT {row}.generation_id,{row}.id,generation_versions.revision,{deleted}
                        FROM generation_versions JOIN generations ON generations.id=generation_versions.id
                        WHERE generation_versions.id={row}.generation_id
                        ON CONFLICT(generation_id,segment_id) DO UPDATE SET revision=excluded.revision,deleted=excluded.deleted;
                    END''')
                for operation, row in (('INSERT', 'NEW'), ('UPDATE', 'NEW'), ('DELETE', 'OLD')):
                    self.db.execute(f'CREATE TRIGGER IF NOT EXISTS exports_version_{operation.lower()} AFTER {operation} ON exports BEGIN UPDATE generation_versions SET revision=revision+1 WHERE id={row}.generation_id; END')
                for operation, row in (('INSERT', 'NEW'), ('UPDATE', 'NEW'), ('DELETE', 'OLD')):
                    name=f'visual_version_{operation.lower()}'
                    deleted=1 if operation == 'DELETE' else 0
                    self.db.execute(f'DROP TRIGGER IF EXISTS {name}')
                    self.db.execute(f'''CREATE TRIGGER {name} AFTER {operation} ON visuals BEGIN
                        UPDATE book_visual_versions SET revision=revision+1 WHERE id={row}.book_id;
                        INSERT INTO book_visual_changes(book_id,visual_id,revision,deleted)
                        SELECT {row}.book_id,{row}.id,book_visual_versions.revision,{deleted}
                        FROM book_visual_versions JOIN books ON books.id=book_visual_versions.id
                        WHERE book_visual_versions.id={row}.book_id
                        ON CONFLICT(book_id,visual_id) DO UPDATE SET revision=excluded.revision,deleted=excluded.deleted;
                    END''')
                self.db.execute('INSERT OR IGNORE INTO settings VALUES(1,?)', (json.dumps(DEFAULTS),))
                if recover_interrupted:
                    self.db.execute("UPDATE generations SET error='' WHERE status='paused' AND error='Preparation paused.'")
                    self.db.execute("UPDATE generations SET status='paused',error='Preparation was interrupted. Resume to reuse completed audio.' WHERE status IN ('queued','analyzing','casting','rendering','aligning') AND config NOT LIKE '%higgs_audio_tts_q8_external%'")
                    self.db.execute("UPDATE segments SET status='pending' WHERE status='rendering'")
                self.db.commit()
        except Exception:
            self.db.rollback()
            self.db.close()
            raise
        if recover_interrupted:
            self.remove_leftovers()

    def remove_leftovers(self):
        try:
            stale = [*(path for path in (self.root / 'books').iterdir() if path.name.startswith(('.import-', '.deleted-'))),
                     *(self.root / 'work').iterdir(), *(self.root / 'audio').glob('.pending-*')]
        except OSError:
            return
        for path in stale:
            with contextlib.suppress(OSError):
                discard(path)

    def execute(self, query, parameters=()):
        with self.lock, self.db:
            return self.db.execute(query, parameters)

    def rows(self, query, parameters=()):
        with self.lock:
            return [dict(x) for x in self.db.execute(query, parameters).fetchall()]

    @contextlib.contextmanager
    def transaction(self):
        """Participate in the caller's transaction without committing it early."""
        if self.db.in_transaction:yield
        else:
            with self.db:
                self.db.execute('BEGIN IMMEDIATE')
                yield

    def audio_path(self, audio_hash):
        if not isinstance(audio_hash, str) or not re.fullmatch('[a-f0-9]{64}', audio_hash):
            raise AppError('Invalid saved audio hash.')
        return self.root / 'audio' / (audio_hash + '.wav')

    def settings(self):
        saved = json.loads(self.rows('SELECT value FROM settings WHERE id=1')[0]['value'])
        settings = {**DEFAULTS, **{key: value for key, value in saved.items() if key in DEFAULTS}}
        # A genre retired from the chip list must not make every later settings save fail validation.
        genres = settings['recommendation_genres']
        settings['recommendation_genres'] = list(dict.fromkeys(genre for genre in genres if isinstance(genre, str) and genre in GENRE_IDS)) if isinstance(genres, list) else []
        return settings

    def update_settings(self, changes):
        if not isinstance(changes, dict) or set(changes) - set(DEFAULTS):
            raise AppError('Unknown setting.')
        settings = {**self.settings(), **changes}
        if settings['device'] not in ('auto', 'cpu', 'cuda', 'mps') or settings['theme'] not in ('light', 'dark', 'sepia'):
            raise AppError('Invalid device or theme.')
        if settings['language'] not in ('en', 'ru') or not isinstance(settings['pack_path'], str) or not isinstance(settings['music_pack_path'], str):
            raise AppError('Invalid language or pack path.')
        for key, minimum, maximum in [('font_size', 14, 32), ('line_height', 1.3, 2.5), ('playback_rate', 0.5, 2.5), ('volume', 0, 1), ('music_volume', 0, 1)]:
            if isinstance(settings[key], bool) or not isinstance(settings[key], (int, float)) or not math.isfinite(settings[key]) or not minimum <= settings[key] <= maximum:
                raise AppError(f'Invalid {key}.')
        for key in ('follow', 'online_images', 'align_words', 'cast_review', 'music', 'online_recommendations'):
            if not isinstance(settings[key], bool):
                raise AppError(f'Invalid {key}.')
        genres = settings['recommendation_genres']
        if not isinstance(genres, list) or not all(isinstance(genre, str) and genre in GENRE_IDS for genre in genres) or len(set(genres)) != len(genres):
            raise AppError('Invalid recommendation genres.')
        if settings['image_model'] not in ('gpt-image-1', 'gpt-image-1.5'):
            raise AppError('Unsupported image model.')
        if settings['character_analysis'] not in ('auto', 'local', 'codex_cli', 'claude_cli'):
            raise AppError('Unsupported character analysis provider.')
        if settings['speaker_tie_breaker'] not in ('none', 'codex_cli', 'claude_cli'):
            raise AppError('Unsupported speaker tie-breaker.')
        if settings['unresolved_policy'] not in ('hold_flagged_segments',
                                                 'render_with_warning'):
            raise AppError('Unsupported unresolved-speaker policy.')
        self.execute('UPDATE settings SET value=? WHERE id=1', (json.dumps(settings),))
        return settings

    def library(self):
        books = self.rows('SELECT id,title,author,added,manifest FROM books ORDER BY added DESC')
        for book in books:
            manifest = json.loads(book.pop('manifest'))
            book.update(chapter_count=len(visible_chapters(manifest)), characters=manifest['characters'], cover=manifest.get('cover'), format=manifest['format'])
            book['generations'] = self.generation_list(book['id'])
            book['current_generation_id'] = self.current_generation_id(book['id'])
            position = self.rows('SELECT value FROM positions WHERE book_id=?', (book['id'],))
            book['position'] = json.loads(position[0]['value']) if position else None
            reading = self.rows('SELECT value FROM reading_positions WHERE book_id=?', (book['id'],))
            book['reading_position'] = json.loads(reading[0]['value']) if reading else None
        return books

    def _book_manifest(self, book_id):
        rows = self.rows('SELECT manifest FROM books WHERE id=?', (require_id(book_id),))
        if not rows:
            raise AppError('Book not found.')
        return json.loads(rows[0]['manifest'])

    def book(self, book_id):
        with self.lock:
            book = self._book_manifest(book_id)
            original_chapter_count = len(book['chapters'])
            book['chapters'] = visible_chapters(book)
            visible_sources = {block['id'] for chapter in book['chapters'] for block in chapter['blocks']}
            book['navigation'] = [item for item in book.get('navigation', []) if item.get('source_id') in visible_sources]
            if 'preparation' not in book or len(book['chapters']) != original_chapter_count:
                book['preparation'] = preparation_report(book)
            book['generations'] = self.generation_list(book_id)
            book['current_generation_id'] = self.current_generation_id(book_id)
            book['bookmarks'] = self.rows('SELECT * FROM bookmarks WHERE book_id=? ORDER BY created', (book_id,))
            book['visuals'] = self.visuals(book_id)
            book['visual_revision'] = self.rows('SELECT revision FROM book_visual_versions WHERE id=?', (book_id,))[0]['revision']
            saved = self.rows('SELECT value FROM positions WHERE book_id=?', (book_id,))
            book['position'] = json.loads(saved[0]['value']) if saved else None
            reading = self.rows('SELECT value FROM reading_positions WHERE book_id=?', (book_id,))
            book['reading_position'] = json.loads(reading[0]['value']) if reading else None
            visible_chapter_ids = {chapter['id'] for chapter in book['chapters']}
            if book['position'] and book['position'].get('chapter_id') not in visible_chapter_ids:
                book['position'] = None
            if book['reading_position'] and book['reading_position'].get('chapter_id') not in visible_chapter_ids:
                book['reading_position'] = None
            return book

    def import_data(self, raw, filename):
        book = import_book(raw, filename, self.root / 'books')
        book_id = book['id']
        with self.lock, self.db:
            if self.db.execute('SELECT 1 FROM generations WHERE book_id=? UNION ALL SELECT 1 FROM bookmarks WHERE book_id=? UNION ALL SELECT 1 FROM visuals WHERE book_id=? UNION ALL SELECT 1 FROM illustration_runs WHERE book_id=? LIMIT 1', (book_id,) * 4).fetchone():
                return self.book(book_id)
            self.db.execute('INSERT INTO books VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET title=excluded.title,author=excluded.author,manifest=excluded.manifest',
                            (book_id, book['title'], book['author'], now(), json.dumps(book, ensure_ascii=False)))
            sources = {chapter['id']: {block['id'] for block in chapter['blocks']} for chapter in book['chapters']}
            for table in ('positions', 'reading_positions'):
                for row in self.db.execute(f'SELECT value FROM {table} WHERE book_id=?', (book_id,)).fetchall():
                    value = json.loads(row['value'])
                    if value.get('chapter_id') not in sources or value.get('source_id') and value['source_id'] not in sources[value['chapter_id']]:
                        self.db.execute(f'DELETE FROM {table} WHERE book_id=?', (book_id,))
        return self.book(book_id)

    def generation_list(self, book_id):
        items = self.rows("SELECT id,book_id,name,created,updated,status,error,parent_id,config FROM generations WHERE book_id=? AND status!='retired' ORDER BY created DESC,rowid DESC", (book_id,))
        for item in items:
            item['config'] = json.loads(item['config'])
            item['progress'] = self.rows("SELECT count(*) total,coalesce(sum(status='ready'),0) ready,coalesce(sum(status='review'),0) held,coalesce(sum(duration),0) duration FROM segments WHERE generation_id=?", (item['id'],))[0]
        return items

    def _generation_metadata(self, generation_id):
        rows = self.rows('SELECT * FROM generations WHERE id=?', (require_id(generation_id),))
        if not rows:
            raise AppError('Generation not found.')
        item = rows[0]
        for key in ('config', 'cast', 'analysis'):
            item[key] = json.loads(item[key])
        item['exports'] = self.rows('SELECT * FROM exports WHERE generation_id=? ORDER BY created DESC', (generation_id,))
        return item

    def _generation_segments(self, generation_id, revision=None):
        if revision is None:
            segments = self.rows('SELECT * FROM segments WHERE generation_id=? ORDER BY ordinal', (generation_id,))
        else:
            segments = self.rows('SELECT segments.* FROM generation_segment_changes AS changes CROSS JOIN segments ON segments.generation_id=changes.generation_id AND segments.id=changes.segment_id WHERE changes.generation_id=? AND changes.revision>? AND changes.deleted=0 ORDER BY segments.ordinal', (generation_id, revision))
        for segment in segments:
            segment['cues'] = json.loads(segment['cues'])
            segment['performance'] = json.loads(segment['performance'])
        return segments

    def generation(self, generation_id):
        with self.lock:
            item = self._generation_metadata(generation_id)
            item['segments'] = self._generation_segments(generation_id)
            item['revision'] = self.rows('SELECT revision FROM generation_versions WHERE id=?', (generation_id,))[0]['revision']
            return item

    def current_generation_id(self, book_id):
        rows=self.rows('SELECT generation_id FROM current_audiobooks WHERE book_id=?',(book_id,))
        if rows:return rows[0]['generation_id']
        rows=self.rows("SELECT id FROM generations WHERE book_id=? AND status!='retired' ORDER BY created DESC,rowid DESC LIMIT 1",(book_id,))
        return rows[0]['id']if rows else None

    def current_generation(self, book_id):
        self._book_manifest(book_id)
        identifier=self.current_generation_id(book_id)
        return self.generation(identifier)if identifier else None

    def set_current_generation(self, book_id, generation_id):
        with self.lock,self.transaction():
            generation=self.generation(generation_id)
            if generation['book_id']!=book_id:raise AppError('The audiobook belongs to a different book.')
            if generation['analysis'].get('retired'):raise AppError('A retired output cannot replace the current audiobook.')
            self.db.execute('INSERT INTO current_audiobooks(book_id,generation_id) VALUES(?,?) ON CONFLICT(book_id) DO UPDATE SET generation_id=excluded.generation_id',(book_id,generation_id))
        return self.generation(generation_id)

    def playback_generation(self, generation_id):
        from .current_audiobook import playable
        with self.lock:
            item=self.generation(generation_id)
            if item['analysis'].get('retired'):
                current=self.current_generation_id(item['book_id'])
                if current and current!=generation_id:
                    return {**self.playback_generation(current),'replaces_generation_id':generation_id}
            working=item['segments']
            item['preparation_progress']={'total':len(working),'ready':sum(row['status']=='ready'for row in working),
                                          'held':sum(row['status']=='review'for row in working)}
            item['segments']=playable(self,generation_id,working)
            item['pending_replacements']=sum('replacement_status'in row for row in item['segments'])
            return item

    def playback_generation_poll(self, generation_id, revision=None):
        # Replacement topology can split one previous part into several new ones.
        # Send a coherent full view when any requested revision changed.
        with self.lock:
            metadata=self.rows('SELECT status FROM generations WHERE id=?',(require_id(generation_id),))
            if metadata and metadata[0]['status']=='retired':
                current=self.playback_generation(generation_id)
                return {'id':current['id'],'revision':current['revision'],'generation':current,'full':True,'removed_segment_ids':[]}
            result=self.generation_poll(generation_id,revision)
            if result['generation'] is not None:
                view=self.playback_generation(generation_id)
                view['analysis']=result['generation']['analysis']
                result.update(generation=view,full=True,removed_segment_ids=[])
            return result

    def complete_replacements(self, generation_id):
        from .current_audiobook import release_completed
        with self.lock,self.transaction():return release_completed(self,generation_id)

    def retire_previous_outputs(self, book_id):
        """Compact old output rows; actual file collection is a separate step."""
        from .current_audiobook import archive_part,previous_parts
        with self.lock,self.transaction():
            current=self.current_generation_id(book_id)
            if not current:raise AppError('Choose the current audiobook before retiring previous outputs.')
            previous=[g for g in self.generation_list(book_id)if g['id']!=current]
            if any(g['status']in ACTIVE for g in previous):raise AppError('An earlier audiobook is still active.')
            previous_ids={g['id']for g in previous}
            if any(row['generation_id']in previous_ids for row in self.rows("SELECT generation_id FROM listener_correction_jobs WHERE status IN ('queued','running','retrying')")):
                raise AppError('A correction for an earlier output is still active.')
            retired=[]
            for saved in previous:
                generation=self.generation(saved['id'])
                if generation['analysis'].get('retired'):continue
                for part in [*generation['segments'],*previous_parts(self,saved['id'])]:archive_part(self,saved['id'],part)
                self.db.execute('DELETE FROM segments WHERE generation_id=?',(saved['id'],))
                self.db.execute('DELETE FROM audiobook_replacements WHERE generation_id=?',(saved['id'],))
                compact={'retired':True,'current_generation_id':current,'previous_analysis_sha256':digest(generation['analysis']),
                         'previous_cast_sha256':digest(generation['cast']),'takes':generation['analysis'].get('takes',{}),'retired_at':now()}
                self.db.execute("UPDATE generations SET status='retired',analysis=?,cast='[]',resume_on_launch=0 WHERE id=?",(json.dumps(compact),saved['id']))
                retired.append(saved['id'])
            return {'current_generation_id':current,'retired_generation_ids':retired}

    def audio_lease(self,hashes):
        from .current_audiobook import audio_lease
        return audio_lease(self,hashes)

    def active_audio_leases(self):
        from .current_audiobook import active_leases
        with self.lock,self.transaction():return active_leases(self)

    def publish_current_parts(self, generation_id, parts, *, expected_revision=None, metadata=None):
        """Validate and atomically adopt ready parts under an optimistic revision."""
        from .current_audiobook import FIELDS
        from .integrity import accepted_audio
        if not isinstance(parts,list)or not parts:raise AppError('Supply ready audiobook parts.')
        with self.lock,self.transaction():
            generation=self.generation(generation_id)
            if expected_revision is not None and generation['revision']!=expected_revision:
                raise AppError('The audiobook changed before these parts could be published. Refresh the current revision.')
            book=self._book_manifest(generation['book_id'])
            blocks={b['id']:b for c in book['chapters']for b in c['blocks']}
            chapters={b['id']:c['id']for c in book['chapters']for b in c['blocks']}
            seen=set();built=[]
            for part in parts:
                if (not isinstance(part,dict)or set(part)-set(FIELDS)or part.get('id')in seen
                    or not isinstance(part.get('id'),str)or not re.fullmatch(r'[A-Za-z0-9_-]{1,300}',part['id'])
                    or part.get('generation_id',generation_id)!=generation_id):
                    raise AppError('Invalid or duplicate audiobook part.')
                seen.add(part['id']);row={**part,'generation_id':generation_id}
                block=blocks.get(row.get('source_id'))
                if (not block or type(row.get('start'))is not int or type(row.get('end'))is not int
                    or not 0<=row['start']<row['end']<=len(block['text'])
                    or block['text'][row['start']:row['end']]!=row.get('text')or row.get('status')!='ready'
                    or row.get('chapter_id')!=chapters[row['source_id']]or type(row.get('ordinal'))is not int):
                    raise AppError('A published audio part must match its exact source range.')
                info=accepted_audio(self.audio_path(row.get('audio_hash')))
                if abs(info['duration']-row.get('duration',-1))>1/info['sample_rate']:
                    raise AppError('A published audio part has inconsistent duration.')
                for key in ('cues','performance'):
                    if not isinstance(row.get(key),str):row[key]=json.dumps(row.get(key,[]if key=='cues'else{}),ensure_ascii=False)
                built.append(tuple(row[key]for key in FIELDS))
            if metadata is not None and (not isinstance(metadata,dict)or set(metadata)-{'status','config','analysis','name','cast','error'}):
                raise AppError('Invalid current audiobook metadata.')
            from .current_audiobook import capture_parts
            capture_parts(self,generation,seen)
            columns=','.join(FIELDS);updates=','.join(f'{key}=excluded.{key}'for key in FIELDS if key not in ('generation_id','id'))
            self.db.executemany(f'INSERT INTO segments({columns}) VALUES({",".join("?"for _ in FIELDS)}) ON CONFLICT(generation_id,id) DO UPDATE SET {updates}',built)
            for key,value in (metadata or {}).items():
                if key in ('config','analysis','cast'):value=json.dumps(value,ensure_ascii=False)
                self.db.execute(f'UPDATE generations SET {key}=? WHERE id=?',(value,generation_id))
            self.db.execute('UPDATE generations SET updated=? WHERE id=?',(now(),generation_id))
            self.complete_replacements(generation_id)
            self.set_current_generation(generation['book_id'],generation_id)
            return self.generation(generation_id)

    def report_stress(self, generation_id, segment_id, source_id, selected_text, text_offset=None, audio_offset=None, kind='stress', comment='', intended_stress=None, audio_hash=None, audio_revision=None):
        from .current_audiobook import heard_part
        generation = self.playback_generation(generation_id)
        segment = next((s for s in generation['segments'] if s['id'] == segment_id), None)
        if audio_revision is not None and(type(audio_revision)is not int or audio_revision<1):raise AppError('Invalid heard audio revision.')
        if audio_hash is not None:
            require_id(audio_hash)
            segment=heard_part(self,generation_id,segment_id,audio_hash)
        if not segment or segment['source_id'] != source_id:
            raise AppError('Selected passage does not match this narration segment.')
        if segment.get('status','ready') != 'ready' or not segment['audio_hash']:
            raise AppError('Generate this narration passage before reporting its stress.')
        book = self.book(generation['book_id'])
        chapter = next((c for c in book['chapters'] if c['id'] == segment['chapter_id']), None)
        block = next((b for b in chapter['blocks'] if b['id'] == source_id), None) if chapter else None
        if not block:
            raise AppError('Source passage not found.')
        selected_text = bounded_text(selected_text, 200).strip()
        normalized = lambda value: ' '.join(value.split()).casefold()
        if not selected_text or normalized(selected_text) not in normalized(block['text']) or normalized(selected_text) not in normalized(segment['text']):
            raise AppError('Select words from one passage of the book.')
        if text_offset is not None and (type(text_offset) is not int or text_offset < 0 or text_offset > len(block['text'])):
            raise AppError('Invalid text position.')
        if text_offset is not None and not (segment['start'] <= text_offset and text_offset + len(selected_text) <= segment['end']):
            raise AppError('Selected words cross narration segments.')
        if text_offset is not None and block['text'][text_offset:text_offset + len(selected_text)] != selected_text:
            raise AppError('Selected words do not match this text position.')
        if audio_offset is not None and (not isinstance(audio_offset, (float, int)) or not math.isfinite(audio_offset) or audio_offset < 0 or audio_offset > segment['duration'] + 1):
            raise AppError('Invalid audio position.')
        if kind not in ('stress', 'pronunciation', 'pacing', 'voice', 'other'):
            raise AppError('Invalid feedback category.')
        if not isinstance(comment, str) or len(comment) > 2000:
            raise AppError('Feedback comment must be at most 2000 characters.')
        comment = comment.strip()
        if kind != 'stress' and not comment:
            raise AppError('Describe the narration issue in the comment.')
        if kind == 'stress':
            if not isinstance(intended_stress, str):
                raise AppError('Choose the intended stress for this word.')
            intended_stress = unicodedata.normalize('NFC', bounded_text(intended_stress, 200).strip())
            selected_word = unicodedata.normalize('NFC', selected_text).replace('\u0301', '')
            # The intended form may also respell е as э/ё (Мозес -> Мо́зэс); nothing else may change (rimsky.respelling).
            try:
                if not re.fullmatch(r'[А-Яа-яЁё]+(?:[-‑][А-Яа-яЁё]+)*', selected_word):
                    raise ValueError('not a Russian word')
                validate_respelled_stress(selected_word, intended_stress)
            except ValueError:
                raise AppError('Intended stress must mark one vowel in the selected Russian word.') from None
        elif intended_stress not in (None, ''):
            raise AppError('Intended stress is only valid for a stress report.')
        else:
            intended_stress = None
        report = {'id': uid(), 'book_id': generation['book_id'], 'generation_id': generation_id,
                  'segment_id': segment_id, 'chapter_id': segment['chapter_id'], 'source_id': source_id,
                  'selected_text': selected_text, 'text_offset': text_offset, 'audio_offset': audio_offset,
                  'audio_hash': audio_hash, 'passage_text': block['text'],
                  'spoken_text': segment['spoken']if audio_hash else '', 'created': now(), 'kind': kind, 'comment': comment,
                  'intended_stress': intended_stress,
                  'synthesis_snapshot': (segment.get('performance') or {}).get('synthesis_snapshot')if audio_hash else None,
                  'heard_binding':'current'if audio_hash and segment.get('status')=='ready'else 'retired_receipt'if audio_hash else 'unknown',
                  'heard_revision':audio_revision}
        with self.lock, self.db:
            columns=('id','book_id','generation_id','segment_id','chapter_id','source_id','selected_text','text_offset','audio_offset','audio_hash','passage_text','spoken_text','created','kind','comment','intended_stress','synthesis_snapshot','heard_binding','heard_revision')
            values=[json.dumps(report[key],ensure_ascii=False)if key=='synthesis_snapshot'and report[key]is not None else report[key]for key in columns]
            self.db.execute(f'INSERT INTO stress_reports ({",".join(columns)}) VALUES({",".join("?"for _ in columns)})',values)
        if kind == 'stress':
            from .series_characters import record_listener_stress
            try:
                report['character_state'] = record_listener_stress(self, report)
            except Exception as error:
                # Raw notes are already committed; failure is explicit and can
                # be reconciled later without requiring another listener report.
                report['character_state'] = {'status': 'held', 'error': str(error)}
            from .listener_stress import record
            canonical=record(self,book,report,report.get('character_state'))
            if canonical:report['character_state']=canonical
        else:
            from .listener_stress import record
            record(self,book,report)
        return report

    def stress_reports(self, book_id):
        self._book_manifest(book_id)
        reports = self.rows('SELECT * FROM stress_reports WHERE book_id=? ORDER BY created DESC,rowid DESC', (book_id,))
        for report in reports:
            report['synthesis_snapshot'] = json.loads(report['synthesis_snapshot']) if report.get('synthesis_snapshot') else None
            # ALTER TABLE defaults are schema metadata, not new human evidence.
            # Preserve the exact pre-migration raw journal shape on replay.
            if report.get('heard_binding')=='legacy_unknown'and report.get('heard_revision')is None:
                report.pop('heard_binding');report.pop('heard_revision')
        return reports

    def visuals(self, book_id):
        return self.rows('SELECT * FROM visuals WHERE book_id=? ORDER BY created,rowid', (require_id(book_id),))

    def visuals_poll(self, book_id, revision=None):
        if revision is not None and (type(revision) is not int or revision < 1):
            raise AppError('Invalid illustration revision.')
        with self.lock:
            rows = self.rows('SELECT revision FROM book_visual_versions WHERE id=?', (require_id(book_id),))
            if not rows:
                raise AppError('Book not found.')
            current = rows[0]['revision']
            full = revision is None or revision > current
            visuals = None
            removed = []
            if revision != current:
                if full:
                    visuals = self.visuals(book_id)
                else:
                    visuals = self.rows('SELECT visuals.* FROM book_visual_changes AS changes CROSS JOIN visuals ON visuals.book_id=changes.book_id AND visuals.id=changes.visual_id WHERE changes.book_id=? AND changes.revision>? AND changes.deleted=0 ORDER BY visuals.created,visuals.rowid', (book_id, revision))
                    removed = [item['visual_id'] for item in self.rows('SELECT visual_id FROM book_visual_changes WHERE book_id=? AND revision>? AND deleted=1', (book_id, revision))]
            return {'id': book_id, 'revision': current, 'visuals': visuals, 'full': full, 'removed_visual_ids': removed}

    def generation_poll(self, generation_id, revision=None):
        if revision is not None and (type(revision) is not int or revision < 1):
            raise AppError('Invalid generation revision.')
        with self.lock:
            rows = self.rows('SELECT revision FROM generation_versions WHERE id=?', (require_id(generation_id),))
            if not rows:
                raise AppError('Generation not found.')
            current = rows[0]['revision']
            snapshot = None
            full = revision is None or revision > current
            removed = []
            if current != revision:
                snapshot = self._generation_metadata(generation_id)
                snapshot['revision'] = current
                snapshot['analysis'] = {key: snapshot['analysis'][key] for key in POLLED_ANALYSIS if key in snapshot['analysis']}
                snapshot['segments'] = self._generation_segments(generation_id, None if full else revision)
                if not full:
                    removed = [item['segment_id'] for item in self.rows('SELECT segment_id FROM generation_segment_changes WHERE generation_id=? AND revision>? AND deleted=1', (generation_id, revision))]
            return {'id': generation_id, 'revision': current, 'generation': snapshot, 'full': full, 'removed_segment_ids': removed}

    def create_generation(self, book_id, config, name='', chapters=None):
        book = self.book(book_id)
        selected = chapter_selection(book, chapters)
        if not any(narratable(b) for c in book['chapters'] if c['id'] in selected for b in c['blocks']):
            raise AppError('The selected chapters contain no narratable text.')
        original=self.current_generation(book_id)
        if original and original['status']in ACTIVE:raise AppError('Pause the audiobook before regenerating it.')
        generation_id = original['id']if original else uid()
        from .opening import attach_opening
        previous=dict(original['config'])if original else {}
        from .current_audiobook import previous_parts
        retained=[*original['segments'],*previous_parts(self,original['id'])]if original else []
        combined=set(selected)|{s['chapter_id']for s in retained if s.get('audio_hash')}
        config = {**previous,**config, 'chapters': [c['id']for c in book['chapters']if c['id']in combined], 'schema': 1}
        config.pop('engine',None);config.pop('external_music',None)
        title = bounded_text(name or (original['name']if original else book['title']))
        with self.lock, self.db:
            config['opening'] = previous.get('opening')or attach_opening(self, book)
            reusable=bool(original and original['analysis'].get('complete')and previous.get('pack_fingerprint')==config.get('pack_fingerprint')
                          and previous.get('engine')!='higgs_audio_tts_q8_external_preview')
            analysis=dict(original['analysis'])if reusable else {}
            if config.get('russian_preparation') and analysis.get('pronunciation'):
                # An explicit new generation request refreshes selected
                # preparation under the released recipe. Resume never comes
                # through this method and keeps its frozen decisions.
                pronunciation=dict(analysis['pronunciation'])
                pronunciation['prepared_chapters']={key:value for key,value in pronunciation.get('prepared_chapters',{}).items()if key not in selected}
                pronunciation.pop('proper_noun_preparation',None)
                analysis['pronunciation']=pronunciation
            if original:
                takes=self.takes(book_id);targets=[s for s in original['segments']if s['chapter_id']in selected]
                from .current_audiobook import capture_parts
                capture_parts(self,original,[s['id']for s in (targets if reusable else original['segments'])])
                analysis['takes']={**analysis.get('takes',{}),**{s['id']:takes.get(s['id'],0)+1 for s in targets}}
                self.db.execute('UPDATE generations SET name=?,updated=?,status=?,config=?,analysis=?,cast=?,error=?,resume_on_launch=0 WHERE id=?',
                    (title,now(),'paused',json.dumps(config,ensure_ascii=False),json.dumps(analysis,ensure_ascii=False),
                     json.dumps(original['cast']if reusable else [],ensure_ascii=False),'',generation_id))
                self.db.executemany("UPDATE segments SET status='pending',audio_hash=NULL,duration=0,cues='[]',warning='' WHERE generation_id=? AND id=?",
                    [(generation_id,s['id'])for s in targets])
                if not reusable:self.db.execute('DELETE FROM segments WHERE generation_id=?',(generation_id,))
            else:
                self.db.execute('INSERT INTO generations(id,book_id,name,created,updated,status,config) VALUES(?,?,?,?,?,?,?)', (generation_id, book_id, title, now(), now(), 'paused', json.dumps(config, ensure_ascii=False)))
            sources=speech_sources(book,selected if reusable else config['chapters'])
            known={s['source_id']for s in self._generation_segments(generation_id)}
            for source in sources:
                if source['source_id']in known:continue
                self.db.execute('INSERT INTO segments(generation_id,id,ordinal,chapter_id,source_id,start,end,text,spoken) VALUES(?,?,?,?,?,?,?,?,?)',
                                (generation_id, source['id'], source['ordinal'] * 100 + 1, source['chapter_id'], source['source_id'],
                                 source['start'], source['end'], source['text'], source['text']))
            self.set_current_generation(book_id,generation_id)
        return self.generation(generation_id)

    def status(self, generation_id, status, error=''):
        self.execute('UPDATE generations SET status=?,error=?,updated=? WHERE id=?', (status, error[:4000], now(), generation_id))

    def revise(self, generation_id, changes, name=''):
        original = self.generation(generation_id)
        if original['status'] in ACTIVE:
            raise AppError('Pause this generation before creating a corrected version.')
        if not original['analysis'].get('complete'):
            raise AppError('Finish character analysis before making passage corrections.')
        if not isinstance(changes, list) or not changes or len(changes) > 500:
            raise AppError('Supply between 1 and 500 passage corrections.')
        if any(isinstance(change, dict) and 'part' in change and any(isinstance(other, dict) and other.get('id') == change.get('id') and 'part' not in other for other in changes) for change in changes):
            raise AppError('Correct a mixed sentence either as a whole or part by part, not both at once.')
        by_id = {s['id']: s for s in original['segments']}
        verdicts_before = dict(original['analysis'].get('speaker_verdicts') or {})
        flagged_before, held_before = speaker_review_holds(original['segments'], original['analysis'].get('speaker_resolution', []), verdicts_before)
        cast_ids = {c['id'] for c in original['cast']} | {'narrator', 'unknown'}
        from .analysis import DELIVERIES
        seen_changes = set()
        part_changes = [change for change in changes if isinstance(change, dict) and 'part' in change]
        for change in part_changes:
            old = by_id.get(change.get('id')) if isinstance(change.get('id'), str) else None
            parts = mixed_parts(old) if old else []
            presenting = 'confirm_presentation' in change
            key = (change.get('id'), change.get('part'), presenting)
            if (old is None or type(change['part']) is not int or key in seen_changes
                    or not presenting and not 0 <= change['part'] < len(parts)
                    or set(change) not in ({'id', 'part', 'speaker'}, {'id', 'part', 'confirm_speaker'}, {'id', 'part', 'confirm_presentation'})
                    or any(change.get(flag, True) is not True for flag in ('confirm_speaker', 'confirm_presentation'))):
                raise AppError('Invalid passage correction.')
            seen_changes.add(key)
            if presenting:
                if (parts[change['part']] if 0 <= change['part'] < len(parts) else old).get('speaker') == 'unknown':
                    raise AppError('Choose a speaker before confirming this voice.', 'unknown_speaker')
                if not any(entry.get('part') == change['part'] for entry in bindings(old)):
                    raise AppError('Only a voice held for presentation review can be confirmed.', 'not_held')
                continue
            if 'speaker' in change and change['speaker'] not in cast_ids:
                raise AppError('Unknown speaker.')
            if change.get('confirm_speaker'):
                if parts[change['part']].get('speaker') == 'unknown':
                    raise AppError('Choose a speaker before confirming this passage.', 'unknown_speaker')
                if old['status'] != 'review' and old['id'] not in held_before:
                    raise AppError('Only a held passage can be confirmed.', 'not_held')
        presented = [change for change in changes if isinstance(change, dict) and 'part' not in change and 'confirm_presentation' in change]
        for change in presented:
            old = by_id.get(change.get('id')) if isinstance(change.get('id'), str) else None
            if old is None or set(change) != {'id', 'confirm_presentation'} or change['confirm_presentation'] is not True or (change['id'], None, True) in seen_changes:
                raise AppError('Invalid passage correction.')
            seen_changes.add((change['id'], None, True))
            if old['speaker'] == 'unknown':
                raise AppError('Choose a speaker before confirming this voice.', 'unknown_speaker')
            if not bindings(old):
                raise AppError('Only a voice held for presentation review can be confirmed.', 'not_held')
        presentation_verdicts = {presentation_key(change['id'], entry.get('part')): {'tier': 'presentation_confirmation', 'binding': entry}
                                 for change in presented for entry in bindings(by_id[change['id']])}
        presentation_verdicts.update({presentation_key(change['id'], entry.get('part')): {'tier': 'presentation_confirmation', 'binding': entry}
                                      for change in part_changes if 'confirm_presentation' in change
                                      for entry in bindings(by_id[change['id']]) if entry.get('part') == change['part']})
        part_changes = [change for change in part_changes if 'confirm_presentation' not in change]
        changes = [change for change in changes if not (isinstance(change, dict) and ('part' in change or 'confirm_presentation' in change))]
        for change in changes:
            if (not isinstance(change, dict) or not isinstance(change.get('id'), str) or change['id'] in seen_changes or change.get('id') not in by_id
                    or set(change) - {'id','spoken','speaker','delivery','retake','confirm_speaker'} or change.get('retake', True) is not True
                    or 'confirm_speaker' in change and (change['confirm_speaker'] is not True or set(change) != {'id', 'confirm_speaker'})):
                raise AppError('Invalid passage correction.')
            seen_changes.add(change['id'])
            old = by_id[change['id']]
            if change.get('confirm_speaker'):
                if old['speaker'] == 'unknown':
                    raise AppError('Choose a speaker before confirming this passage.', 'unknown_speaker')
                if old['status'] != 'review' and old['id'] not in held_before:
                    raise AppError('Only a held passage can be confirmed.', 'not_held')
            if 'speaker' in change and change['speaker'] not in cast_ids:
                raise AppError('Unknown speaker.')
            if 'delivery' in change and change['delivery'] not in DELIVERIES:
                raise AppError('Unknown delivery.')
            if ('delivery' in change and change['delivery'] != old['delivery'] and mixed_parts(old)):
                raise AppError('This passage changes delivery inside the sentence; correct its parts instead.', 'mixed_delivery_edit')
            if 'spoken' in change:
                bounded_text(change['spoken'], 1000)
                if change['spoken'] not in (old['spoken'], old['text']) and (old.get('performance') or {}).get('internal_transition') is True:
                    raise AppError('This passage changes voice inside the sentence; its spoken text cannot be edited as one passage.', 'mixed_spoken_edit')
        edits = []
        for change in changes:
            old = by_id[change['id']]
            edit = tuple(change.get(key, old[key]) for key in ('spoken', 'speaker', 'delivery'))
            if change.get('retake') or edit != (old['spoken'], old['speaker'], old['delivery']):
                edits.append((*edit, change['id']))
        part_performances, part_verdicts, part_unknown = {}, {}, []
        for change in part_changes:
            old = by_id[change['id']]
            parts = [dict(part) for part in part_performances.get(old['id'], mixed_parts(old))]
            part = parts[change['part']]
            if 'speaker' in change and change['speaker'] != part['speaker']:
                part['speaker'] = change['speaker']
                part_performances[old['id']] = parts
            chosen = change.get('speaker', part['speaker'])
            if chosen == 'unknown':
                part_unknown.append((old, part))
            else:
                part_verdicts[f'{old["id"]}#{change["part"]}'] = {'speaker': chosen, 'tier': 'human_correction' if 'speaker' in change else 'human_confirmation',
                                                                   'source_id': old['source_id'], 'start': part['start'], 'end': part['end']}
            if 'speaker' in change and change['speaker'] != mixed_parts(old)[change['part']]['speaker'] and not any(sid == old['id'] for *_, sid in edits):
                edits.append((old['spoken'], old['speaker'], old['delivery'], old['id']))
        edits = [(spoken, displayed_speaker(part_performances[sid]) if sid in part_performances else speaker, delivery, sid) for spoken, speaker, delivery, sid in edits]
        confirmed = [change['id'] for change in changes if change.get('confirm_speaker')]
        if not edits and not confirmed and not part_verdicts and not presentation_verdicts:
            raise AppError('The correction matches the saved passage. Change the spoken text, speaker or delivery first.')
        analysis = dict(original['analysis'])
        retakes = [change['id'] for change in changes if change.get('retake')]
        if retakes:
            used = self.takes(original['book_id'])
            analysis['takes'] = {**analysis.get('takes', {}), **{sid: used.get(sid, 0) + 1 for sid in retakes}}
        renamed = {sid: (by_id[sid]['speaker'], speaker) for _, speaker, _, sid in edits if speaker != by_id[sid]['speaker'] and sid not in part_performances}
        if part_performances and analysis.get('casting_inputs'):
            def repart(entry):
                for sid, parts in part_performances.items():
                    for part in parts:
                        if entry.get('source_id') == by_id[sid]['source_id'] and part['start'] <= entry.get('start', -1) < part['end']:
                            return {**entry, 'speaker': part['speaker']}
                return entry
            analysis['casting_inputs'] = [repart(entry) for entry in analysis['casting_inputs']]
        if renamed and analysis.get('casting_inputs'):
            def recast(entry):
                for sid, (before, after) in renamed.items():
                    old = by_id[sid]
                    if (entry.get('source_id') == old['source_id'] and old['start'] <= entry.get('start', -1) < old['end']
                            and (entry.get('speaker') == before or not (old.get('performance') or {}).get('internal_transition'))):
                        return {**entry, 'speaker': after}
                return entry
            analysis['casting_inputs'] = [recast(entry) for entry in analysis['casting_inputs']]
        performances = {}
        for sid, (before, after) in renamed.items():
            performance = by_id[sid].get('performance') or {}
            if isinstance(performance.get('analysis_parts'), list):
                performances[sid] = {**performance, 'analysis_parts': [{**part, 'speaker': after} if isinstance(part, dict) and part.get('speaker') == before else part
                                                                        for part in performance['analysis_parts']]}
        for sid, parts in part_performances.items():
            performances[sid] = {**(by_id[sid].get('performance') or {}), 'analysis_parts': parts}
        # Delivery and spoken-text corrections must rebuild the synthesis plan.
        # Keeping the old plan silently kept its old pace and cues.
        from .performance import performance_plan
        for spoken, speaker, delivery, sid in edits:
            old = by_id[sid]
            if mixed_parts(old) or (spoken, speaker, delivery) == (old['spoken'], old['speaker'], old['delivery']):
                continue
            previous = performances.get(sid, old.get('performance') or {})
            context = {'mood': previous.get('mood', 'neutral'),
                       'channel': previous.get('presentation_channel', 'direct'),
                       'content_register': previous.get('content_register', 'prose'),
                       'confidence': previous.get('context_confidence', 'low'),
                       'evidence': previous.get('context_evidence', [])}
            plan = performance_plan({'text': spoken, 'delivery': delivery, 'speaker': speaker, 'context': context,
                                     'kind': 'heading' if 'section_heading' in previous.get('cues', []) else None,
                                     'source_id': old['source_id']})
            performances[sid] = {**previous, **plan}
        edited_values = {sid: (spoken, speaker, delivery) for spoken, speaker, delivery, sid in edits}
        revised = [{**segment,
                    **(dict(zip(('spoken', 'speaker', 'delivery'), edited_values[segment['id']])) if segment['id'] in edited_values else {}),
                    **({'performance': performances[segment['id']]} if segment['id'] in performances else {})} for segment in original['segments']]
        # A correction discards the edited passages' takes, and with them any
        # hold on how those takes were rendered.
        resolutions = [dict(item) for item in analysis.get('speaker_resolution', [])
                       if not (RENDER_HOLD in (item.get('triggers') or ()) and item.get('target_id') in edited_values)]
        reparted ={(sid, index) for sid, parts in part_performances.items() for index, part in enumerate(parts) if part['speaker'] != mixed_parts(by_id[sid])[index]['speaker']}
        def stale(key):
            sid, _, part = key.removesuffix('@presentation').partition('#')
            if sid in renamed:
                before = mixed_parts(by_id[sid])
                return not part or not before or before[int(part)].get('speaker') == renamed[sid][0]
            return bool(part) and (sid, int(part)) in reparted
        if any(stale(key) for key in presentation_verdicts):
            raise AppError('Confirm the presentation voice after the speaker change has been rendered.', 'stale_presentation')
        verdicts = {key: verdict for key, verdict in verdicts_before.items()
                    if (not key.endswith('@presentation') or not stale(key)) and (key.endswith('@presentation') or key not in renamed)}
        verdicts.update(presentation_verdicts)
        corrected = {sid for sid, (_, after) in renamed.items() if after != 'unknown'}
        for segment in revised:
            if segment['id'] in corrected or segment['id'] in confirmed:
                verdicts[segment['id']] = {'speaker': segment['speaker'], 'tier': 'human_correction' if segment['id'] in corrected else 'human_confirmation',
                                           'source_id': segment['source_id'], 'start': segment['start'], 'end': segment['end']}
            elif segment['id'] in renamed and not any(item.get('status') == 'review' and 'unresolved_speaker' in (item.get('triggers') or ()) and overlaps(item, segment) for item in resolutions):
                resolutions.append({'target_id': segment['id'], 'source_id': segment['source_id'], 'start': segment['start'], 'end': segment['end'],
                                    'speaker': 'unknown', 'primary_speaker': by_id[segment['id']]['speaker'], 'tier': 'human_correction',
                                    'triggers': ['unresolved_speaker'], 'status': 'review'})
        verdicts.update(part_verdicts)
        for segment, part in part_unknown:
            resolutions.append({'target_id': segment['id'], 'source_id': segment['source_id'], 'start': part['start'], 'end': part['end'],
                                'speaker': 'unknown', 'primary_speaker': part.get('speaker'), 'tier': 'human_correction',
                                'triggers': ['unresolved_speaker'], 'status': 'review'})
        def item_speakers(item, segment):
            parts = mixed_parts(segment)
            return [part['speaker'] for part in parts if part.get('start', 0) < item.get('end', 0) and item.get('start', 0) < part.get('end', 0)] if parts else [segment['speaker']]
        for item in resolutions:
            covered = [segment for segment in revised if overlaps(item, segment)]
            if (item.get('status') == 'review' and covered and concern(item) == 'identity'
                    and all(cleared(item, segment, verdicts) for segment in covered)):
                speakers = list(dict.fromkeys(speaker for segment in covered for speaker in item_speakers(item, segment)))
                item.update(status='resolved', tier='human_correction' if any(verdict.get('tier') == 'human_correction' for key, verdict in verdicts.items()
                                                                              if key.split('#')[0] in {segment['id'] for segment in covered}) else 'human_confirmation',
                            **({'speaker': speakers[0]} if len(speakers) == 1 else {'human_speakers': speakers}))
        flagged, held = speaker_review_holds(revised, resolutions, verdicts)
        if resolutions or 'speaker_resolution' in analysis:
            from .speaker_resolution import resolution_metrics
            analysis['speaker_resolution'] = resolutions
            analysis['speaker_resolution_metrics'] = resolution_metrics(resolutions)
            analysis['speaker_review_holds'] = hold_summary(revised, resolutions, verdicts)
        if verdicts:
            analysis['speaker_verdicts'] = verdicts
        else:
            analysis.pop('speaker_verdicts', None)
        holding = original['config'].get('unresolved_policy', 'hold_flagged_segments') == 'hold_flagged_segments'
        edited = {sid for *_, sid in edits}
        backend = set(analysis.get('backend_holds') or ())
        holds, releases, revised_by_id = [], [], {segment['id']: segment for segment in revised}
        # A take held only for how it was rendered stays playable; its open hold
        # still blocks export until a new take replaces it.
        held_otherwise = speaker_review_holds(
            revised, [item for item in resolutions if RENDER_HOLD not in (item.get('triggers') or ())], verdicts)[1]
        for segment in original['segments']:
            sid = segment['id']
            if (sid in held and sid not in held_otherwise and sid not in edited
                    and segment['status'] == 'ready' and segment['audio_hash']):
                continue
            warning = ('Unresolved speaker; held for review.' if revised_by_id[sid]['speaker'] == 'unknown' else '') if sid in edited else segment['warning']
            if sid in held and (holding or segment['status'] == 'review'):
                notice = (PRESENTATION_NOTICE if hold_codes(revised_by_id[sid], resolutions, verdicts) == ['presentation_review'] else HOLD_NOTICE) if sid in flagged else NEIGHBOUR_NOTICE
                holds.append((hold_warning(warning, notice), sid))
            elif sid in held_before and sid not in backend:
                releases.append((hold_warning(warning), sid))
        new_id = generation_id
        status = 'review' if analysis.get('approved') is False else 'paused'
        config = dict(original['config'])
        if 'opening' in config:
            from .opening import validate_opening
            validate_opening(config['opening'])
        columns = 'id,ordinal,chapter_id,source_id,start,end,text,spoken,speaker,delivery,status,audio_hash,duration,cues,warning,performance'
        with self.lock, self.db:
            if 'opening' not in config:
                from .opening import attach_opening
                config['opening'] = attach_opening(self, self._book_manifest(original['book_id']))
            from .current_audiobook import capture_parts
            # An explicit confirmation can approve the unchanged old speaker or
            # presentation. Apply those verdicts to the old resolution records;
            # discarded render holds must still block the retained old take.
            approval=verdicts if confirmed or part_verdicts or presentation_verdicts else None
            capture_parts(self,original,{sid for *_,sid in edits}|set(performances)|{sid for _,sid in holds}|{sid for _,sid in releases},verdicts=approval)
            self.db.execute('UPDATE generations SET updated=?,status=?,config=?,analysis=?,error=? WHERE id=?',
                (now(),status,json.dumps(config),json.dumps(analysis,ensure_ascii=False),'',new_id))
            self.db.executemany("UPDATE segments SET spoken=?,speaker=?,delivery=?,status='pending',audio_hash=NULL,duration=0,cues='[]',warning='' WHERE generation_id=? AND id=?",
                                [(spoken, speaker, delivery, new_id, segment_id) for spoken, speaker, delivery, segment_id in edits])
            self.db.executemany('UPDATE segments SET performance=? WHERE generation_id=? AND id=?',
                                [(json.dumps(performance, ensure_ascii=False), new_id, sid) for sid, performance in performances.items()])
            self.db.executemany("UPDATE segments SET status='review',warning=? WHERE generation_id=? AND id=?", [(warning, new_id, sid) for warning, sid in holds])
            self.db.executemany("UPDATE segments SET status='pending',audio_hash=NULL,duration=0,cues='[]',warning=? WHERE generation_id=? AND id=?",
                                [(warning, new_id, sid) for warning, sid in releases])
            self.set_current_generation(original['book_id'],new_id)
        return self.generation(new_id)

    def takes(self, book_id):
        used = {}
        for row in self.rows("SELECT analysis FROM generations WHERE book_id=? AND instr(analysis,'\"takes\"')>0", (book_id,)):
            for sid, take in (json.loads(row['analysis']).get('takes') or {}).items():
                if type(take) is int:
                    used[sid] = max(used.get(sid, 0), take)
        return used

    def delete_generation(self, generation_id):
        with self.lock, self.db:
            generation = self.generation(generation_id)
            if generation['status'] in ACTIVE:
                raise AppError('Pause generation before deleting it.')
            self.db.execute('UPDATE generations SET parent_id=NULL WHERE parent_id=?', (generation_id,))
            self.db.execute('DELETE FROM generations WHERE id=?', (generation_id,))
            positions = self.db.execute('SELECT book_id,value FROM positions').fetchall()
            for row in positions:
                position = json.loads(row['value'])
                if position.get('generation_id') == generation_id:
                    position = {k: v for k, v in position.items() if k not in ('generation_id', 'segment_id')}
                    position['offset'] = 0
                    self.db.execute('UPDATE positions SET value=? WHERE book_id=?', (json.dumps(position), row['book_id']))
        warnings = self.remove_files(self.root / 'exports' / export['file'] for export in generation['exports'])
        warnings.extend(self.collect_audio())
        return {'deleted': True, 'cleanup_warnings': warnings}

    def delete_export(self, export_id):
        rows = self.rows('SELECT file FROM exports WHERE id=?', (require_id(export_id),))
        if not rows:
            raise AppError('Export not found.')
        with self.lock, self.db:
            self.db.execute('DELETE FROM exports WHERE id=?', (export_id,))
        warnings = self.remove_files([self.root / 'exports' / rows[0]['file']])
        return {'deleted': True, 'cleanup_warnings': warnings}

    def delete_book(self, book_id):
        with self.lock, self.db:
            book = self.book(book_id)
            if any(g['status'] in ACTIVE for g in book['generations']):
                raise AppError('Pause preparation before deleting a book.')
            exports = self.rows('SELECT exports.file FROM exports JOIN generations ON generations.id=exports.generation_id WHERE generations.book_id=?', (book_id,))
            self.db.execute('DELETE FROM books WHERE id=?', (book_id,))
        warnings = self.remove_files(self.root / 'exports' / item['file'] for item in exports)
        folder = self.root / 'books' / book_id
        trash = folder.with_name(f'.deleted-{uid()}')
        try:
            folder.rename(trash)
            shutil.rmtree(trash)
        except FileNotFoundError:
            pass
        except OSError as error:
            warnings.append(f'Book was removed from the library, but its stored source could not be removed: {error}')
        with self.lock:
            for visual in book['visuals']:
                if not self.rows('SELECT id FROM visuals WHERE file=?', (visual['file'],)):
                    warnings.extend(self.remove_files([self.root / 'visuals' / visual['file']]))
        warnings.extend(self.collect_audio())
        return {'deleted': True, 'cleanup_warnings': warnings}

    def remove_files(self, paths):
        warnings = []
        expanded = []
        for path in paths:
            expanded.append(path)
            if path.parent == self.root / 'visuals' and path.suffix == '.png':
                expanded.append(path.parent / 'previews' / path.name)
        for path in expanded:
            try:
                path.unlink(missing_ok=True)
            except OSError as error:
                warnings.append(f'Library entry removed, but local file {path.name} could not be removed: {error}')
        return warnings

    def collect_audio(self):
        with self.lock,self.transaction():
            if self.rows("SELECT id FROM generations WHERE status IN ('queued','analyzing','casting','rendering','aligning') LIMIT 1"):
                return ['Audio cleanup was deferred because preparation is active. Completed narration is kept.']
            used = {r['audio_hash'] for r in self.rows('SELECT DISTINCT audio_hash FROM segments WHERE audio_hash IS NOT NULL')}
            from .current_audiobook import previous_parts
            for row in self.rows('SELECT id FROM generations'):
                for part in previous_parts(self,row['id']):
                    used.add(part['audio_hash'])
                    used.update(part.get('performance',{}).get('render_parts')or[])
            used.update(self.active_audio_leases())
            from .opening import opening_hashes
            for row in self.rows("SELECT config FROM generations WHERE status!='retired'"):
                used.update(opening_hashes({'config': json.loads(row['config'])}))
            for row in self.rows("SELECT performance FROM segments WHERE audio_hash IS NOT NULL AND instr(performance,'render_parts')>0"):
                parts = json.loads(row['performance']).get('render_parts')
                used.update(key for key in parts if isinstance(key, str)) if isinstance(parts, list) else None
            try:
                unused = [path for path in (self.root / 'audio').iterdir()
                          if path.suffix in ('.wav', '.json') and re.fullmatch(r'[0-9a-f]{64}', path.stem)
                          and path.stem not in used]
            except OSError as error:
                return [f'Library entry removed, but unused audio could not be inspected for cleanup: {error}']
            return self.remove_files(unused)

    def save_position(self, book_id, value):
        with self.lock:
            book = self._book_manifest(book_id)
            if not isinstance(value, dict) or set(value) - {'generation_id','chapter_id','source_id','segment_id','offset','opening'}:
                raise AppError('Invalid playback position.')
            if value.get('chapter_id') not in {c['id'] for c in book['chapters']}:
                raise AppError('Unknown chapter.')
            offset = value.get('offset', 0)
            if not isinstance(offset, (float,int)) or not math.isfinite(offset) or offset < 0 or offset > 36000:
                raise AppError('Invalid playback offset.')
            chapter = next(c for c in book['chapters'] if c['id'] == value['chapter_id'])
            if value.get('source_id') and value['source_id'] not in {b['id'] for b in chapter['blocks']}:
                raise AppError('Source passage does not belong to the selected chapter.')
            if value.get('generation_id'):
                generations = self.rows('SELECT book_id,config FROM generations WHERE id=?', (require_id(value['generation_id']),))
                if not generations:
                    raise AppError('Generation not found.')
                if generations[0]['book_id'] != book_id:
                    raise AppError('Generation does not belong to this book.')
                if 'opening' in value:
                    from .opening import validate_opening_position
                    validate_opening_position(value['opening'], {'config': json.loads(generations[0]['config'])})
                if value.get('segment_id'):
                    segments = self.rows('SELECT chapter_id,source_id,status,duration FROM segments WHERE generation_id=? AND id=?', (value['generation_id'], value['segment_id'])) if isinstance(value['segment_id'], str) else []
                    segment = segments[0] if segments else None
                    if not segment or segment['chapter_id'] != value['chapter_id'] or segment['source_id'] != value.get('source_id'):
                        raise AppError('Playback segment does not match its source position.')
                    if segment['status'] == 'ready' and offset > segment['duration'] + .1:
                        raise AppError('Playback offset exceeds the saved segment.')
            elif value.get('segment_id') or 'opening' in value:
                raise AppError('A segment or branded opening position requires its generation.')
            self.execute('INSERT INTO positions VALUES(?,?) ON CONFLICT(book_id) DO UPDATE SET value=excluded.value', (book_id,json.dumps(value)))
            return value

    def save_reading_position(self, book_id, value):
        with self.lock:
            book = self._book_manifest(book_id)
            if not isinstance(value, dict) or set(value) - {'chapter_id', 'source_id', 'offset', 'source_fraction'}:
                raise AppError('Invalid reading position.')
            if value.get('chapter_id') not in {chapter['id'] for chapter in book['chapters']}:
                raise AppError('Unknown chapter.')
            chapter = next(chapter for chapter in book['chapters'] if chapter['id'] == value['chapter_id'])
            if value.get('source_id') and value['source_id'] not in {block['id'] for block in chapter['blocks']}:
                raise AppError('Source passage does not belong to the selected chapter.')
            offset = value.get('offset', 0)
            if not isinstance(offset, (float, int)) or not math.isfinite(offset) or offset < 0:
                raise AppError('Invalid reading offset.')
            saved = {'chapter_id': value['chapter_id'], 'offset': offset}
            if value.get('source_id'):
                saved['source_id'] = value['source_id']
            if 'source_fraction' in value:
                fraction = value['source_fraction']
                if (not value.get('source_id') or type(fraction) not in (int, float)
                        or not math.isfinite(fraction) or not 0 <= fraction <= 1):
                    raise AppError('Invalid source-relative reading offset.')
                saved['source_fraction'] = fraction
            self.execute('INSERT INTO reading_positions VALUES(?,?) ON CONFLICT(book_id) DO UPDATE SET value=excluded.value',
                         (book_id, json.dumps(saved)))
            return saved

    def bookmark(self, book_id, source_id, note=''):
        book = self.book(book_id)
        if source_id not in {b['id'] for c in book['chapters'] for b in c['blocks']}:
            raise AppError('Unknown source passage.')
        if not isinstance(note, str) or len(note) > 2000:
            raise AppError('Bookmark note is too long.')
        existing = self.rows('SELECT * FROM bookmarks WHERE book_id=? AND source_id=? ORDER BY created LIMIT 1', (book_id, source_id))
        if existing:
            return existing[0]
        item = {'id':uid(),'book_id':book_id,'source_id':source_id,'note':note,'created':now()}
        self.execute('INSERT INTO bookmarks VALUES(?,?,?,?,?)', tuple(item.values()))
        return item

    def update_bookmark(self, bookmark_id, note):
        bookmark_id = require_id(bookmark_id)
        if not isinstance(note, str) or len(note) > 2000:
            raise AppError('Bookmark note is too long.')
        if not self.rows('SELECT id FROM bookmarks WHERE id=?', (bookmark_id,)):
            raise AppError('Bookmark not found.')
        self.execute('UPDATE bookmarks SET note=? WHERE id=?', (note, bookmark_id))
        return self.rows('SELECT * FROM bookmarks WHERE id=?', (bookmark_id,))[0]

    def close(self):
        with self.lock:
            self.db.close()
