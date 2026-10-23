"""Mark existing Higgs listening-draft takes whose render was degraded.

Dry run (default): survey every external Higgs render folder that has a
plan.json, link each ready take to the output its render wrote, and report the
retry variants, current audio hashes, issues and proposed state changes.

--apply REPORT: re-check the evidence files and every target against that dry
run, snapshot the library with SQLite's backup API, apply all changes in one
transaction and write an audit report. A target already in its proposed state is
skipped, so reruns are idempotent. Audio, listening positions and labels are not
touched; the segment triggers notify an open player of each change.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / 'runtime'))
from higgs_render_holds import HOLD, render_issues, retry_variant, warning_with, with_holds, with_provenance  # noqa: E402
import render_book1_higgs_half as render  # noqa: E402
from rimsky.util import now  # noqa: E402

REPORTS = render.PILOT / 'degraded-takes'
ENGINE = 'higgs_audio_tts_q8_external'


def sha(path: Path) -> str | None:
    return render.file_sha256(path) if path.is_file() else None


def evidence(folder: Path, chapter: str, segment_id: str) -> dict:
    base = folder / chapter
    return {'variant': sha(base / f'{segment_id}.render-variant.txt'),
            'render_prompt': sha(base / f'{segment_id}.render-prompt.txt'),
            'input_prompt': sha(base / 'input' / f'{segment_id}.txt'),
            'output': sha(base / 'output' / f'{segment_id}.wav'),
            'padded_output': sha(folder / 'boundary-audio' / f'v{render.BOUNDARY_VERSION}' / f'{segment_id}.wav')}


def route_hashes(routes: dict) -> dict:
    return {speaker: render.file_sha256(audio) for speaker, (audio, _) in sorted(routes.items())}


def own_holds(analysis: dict, segment_id: str) -> list[dict]:
    return [item for item in analysis.get('speaker_resolution', [])
            if HOLD in (item.get('triggers') or ()) and item.get('target_id') == segment_id]


def survey(db: sqlite3.Connection, routes: dict, folders: list[Path]) -> list[dict]:
    generations = []
    for folder in folders:
        plan = json.loads((folder / 'plan.json').read_text(encoding='utf-8'))
        row = db.execute('SELECT id,name,status,config,analysis FROM generations WHERE id=?',
                         (plan['generation_id'],)).fetchone()
        if row is None or not json.loads(row['config']).get('engine', '').startswith(ENGINE):
            generations.append({'generation_id': plan['generation_id'], 'folder': str(folder),
                                'skipped': 'generation missing or not an external Higgs render'})
            continue
        analysis = json.loads(row['analysis'] or '{}')
        variants, counts, targets, unlinked, unverifiable = Counter(), Counter(), [], [], []
        for segment in db.execute(
                "SELECT id,chapter_id,source_id,start,end,speaker,status,audio_hash,warning,performance "
                "FROM segments WHERE generation_id=? AND status='ready' ORDER BY ordinal", (row['id'],)):
            segment = dict(segment)
            counts['ready'] += 1
            variant = retry_variant(folder / segment['chapter_id'], segment['id'])
            if variant:
                variants[variant] += 1
            files = evidence(folder, segment['chapter_id'], segment['id'])
            issues, complete = render_issues(segment, folder / segment['chapter_id'], routes)
            if not complete:
                unverifiable.append({'segment_id': segment['id'], 'variant': variant})
            if segment['audio_hash'] not in {files['output'], files['padded_output']} - {None}:
                # The stored take did not come from this render's output (for
                # example a later retake), so this evidence does not describe it.
                if variant or issues:
                    unlinked.append({'segment_id': segment['id'], 'variant': variant,
                                     'audio_hash': segment['audio_hash']})
                continue
            performance = json.loads(segment['performance'] or '{}')
            warning = warning_with(segment['warning'], issues)
            provenance = with_provenance(performance, issues, segment['audio_hash'])
            holds = with_holds({'speaker_resolution': own_holds(analysis, segment['id'])}, segment, issues,
                               segment['audio_hash'])['speaker_resolution']
            if warning == segment['warning'] and provenance == performance and holds == own_holds(analysis, segment['id']):
                if issues:
                    counts['already_applied'] += 1
                continue
            counts['proposed'] += 1
            counts.update(item['issue'] for item in issues)
            targets.append({'segment_id': segment['id'], 'chapter_id': segment['chapter_id'],
                            'source_id': segment['source_id'], 'start': segment['start'], 'end': segment['end'],
                            'speaker': segment['speaker'], 'status': segment['status'],
                            'audio_hash': segment['audio_hash'], 'variant': variant, 'issues': issues,
                            'evidence': files, 'warning_before': segment['warning'], 'warning_after': warning,
                            'performance_before': performance, 'performance_after': provenance})
        counts['unlinked'] = len(unlinked)
        counts['unverifiable'] = len(unverifiable)
        generations.append({'generation_id': row['id'], 'name': row['name'], 'status': row['status'],
                            'folder': str(folder), 'plan_sha256': sha(folder / 'plan.json'),
                            'retry_variants': dict(variants), 'counts': dict(counts), 'targets': targets,
                            'unlinked': unlinked, 'unverifiable': unverifiable})
    return generations


def connect(library: Path, readonly: bool) -> sqlite3.Connection:
    db = (sqlite3.connect(f'file:{library}?mode=ro', uri=True) if readonly
          else sqlite3.connect(library, isolation_level=None, timeout=30))
    db.row_factory = sqlite3.Row
    return db


def dry_run(library: Path, folders: list[Path], output: Path) -> dict:
    routes = render.reference_routes()
    db = connect(library, readonly=True)
    try:
        generations = survey(db, routes, folders)
    finally:
        db.close()
    report = {'schema': 1, 'mode': 'dry_run', 'created': time.strftime('%Y-%m-%dT%H:%M:%S'),
              'library': str(library), 'routes': route_hashes(routes), 'generations': generations}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    return report


def revision(db: sqlite3.Connection, generation_id: str) -> int | None:
    row = db.execute('SELECT revision FROM generation_versions WHERE id=?', (generation_id,)).fetchone()
    return row['revision'] if row else None


def apply(report_path: Path, library: Path) -> dict:
    report = json.loads(report_path.read_text(encoding='utf-8'))
    if report.get('schema') != 1 or report.get('mode') != 'dry_run' or report.get('library') != str(library):
        raise SystemExit('Apply needs a dry-run report made for this library.')
    routes = render.reference_routes()
    if route_hashes(routes) != report['routes']:
        raise SystemExit('Voice reference routes changed since the dry run; run a new dry run.')
    changed = [f"{target['segment_id']}: {name}"
               for generation in report['generations'] for target in generation.get('targets', [])
               for name, value in evidence(Path(generation['folder']), target['chapter_id'],
                                           target['segment_id']).items() if target['evidence'][name] != value]
    if changed:
        raise SystemExit('Render evidence changed since the dry run; run a new dry run.\n' + '\n'.join(changed[:20]))
    stamp = time.strftime('%Y%m%d-%H%M%S')
    snapshot = REPORTS / f'library-before-{stamp}.sqlite3'
    REPORTS.mkdir(parents=True, exist_ok=True)
    source, copy = sqlite3.connect(library), sqlite3.connect(snapshot)
    try:
        source.backup(copy)
    finally:
        copy.close()
        source.close()
    db = connect(library, readonly=False)
    audit = {'schema': 1, 'mode': 'apply', 'created': time.strftime('%Y-%m-%dT%H:%M:%S'), 'library': str(library),
             'dry_run_report': str(report_path), 'dry_run_sha256': sha(report_path),
             'snapshot': str(snapshot), 'snapshot_sha256': sha(snapshot), 'generations': []}
    try:
        db.execute('BEGIN IMMEDIATE')
        for generation in report['generations']:
            targets = generation.get('targets', [])
            if not targets:
                continue
            gid = generation['generation_id']
            before = revision(db, gid)
            applied, skipped = [], []
            original = json.loads(db.execute('SELECT analysis FROM generations WHERE id=?', (gid,)).fetchone()['analysis'] or '{}')
            analysis = original
            for target in targets:
                row = db.execute('SELECT id,source_id,start,end,speaker,status,audio_hash,warning,performance '
                                 'FROM segments WHERE generation_id=? AND id=?', (gid, target['segment_id'])).fetchone()
                if row is None or (row['status'], row['audio_hash']) != (target['status'], target['audio_hash']):
                    raise RuntimeError(f"{target['segment_id']}: its take changed since the dry run.")
                segment = dict(row)
                performance = json.loads(row['performance'] or '{}')
                if (row['warning'], performance) == (target['warning_before'], target['performance_before']):
                    db.execute('UPDATE segments SET warning=?,performance=? WHERE generation_id=? AND id=?',
                               (target['warning_after'], json.dumps(target['performance_after'], ensure_ascii=False),
                                gid, target['segment_id']))
                    applied.append(target['segment_id'])
                elif (row['warning'], performance) == (target['warning_after'], target['performance_after']):
                    skipped.append(target['segment_id'])
                else:
                    raise RuntimeError(f"{target['segment_id']}: its warning or performance changed since the dry run.")
                analysis = with_holds(analysis, segment, target['issues'], target['audio_hash'])
            if analysis != original:
                db.execute("UPDATE generations SET analysis=?,updated=? WHERE id=?",
                           (json.dumps(analysis, ensure_ascii=False), now(), gid))
            audit['generations'].append({'generation_id': gid, 'applied': applied, 'already_applied': skipped,
                                         'counts': Counter(issue['issue'] for target in targets for issue in target['issues']),
                                         'revision_before': before, 'revision_after': revision(db, gid)})
        db.execute('COMMIT')
    except BaseException:
        db.execute('ROLLBACK')
        raise
    finally:
        db.close()
    path = REPORTS / f'apply-{stamp}.json'
    path.write_text(json.dumps(audit, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    audit['report'] = str(path)
    return audit


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--library', type=Path, default=render.DATA / 'library.sqlite3')
    parser.add_argument('--folder', type=Path, action='append',
                        help='Render folder with plan.json (default: every folder under the Higgs pilot)')
    parser.add_argument('--report', type=Path, help='Dry-run report path')
    parser.add_argument('--apply', type=Path, metavar='REPORT', help='Apply a reviewed dry-run report')
    args = parser.parse_args()
    library = args.library.resolve()
    if args.apply:
        audit = apply(args.apply.resolve(), library)
        print(json.dumps({'report': audit['report'], 'snapshot': audit['snapshot'],
                          'generations': [{key: (len(value) if isinstance(value, list) else value)
                                           for key, value in item.items()} for item in audit['generations']]},
                         ensure_ascii=False, indent=1))
        return
    folders = [folder.resolve() for folder in args.folder] if args.folder else sorted(
        path.parent for path in render.PILOT.glob('*/plan.json'))
    output = args.report or REPORTS / f"dry-run-{time.strftime('%Y%m%d-%H%M%S')}.json"
    report = dry_run(library, folders, output)
    print(json.dumps({'report': str(output), 'generations': [
        {key: item.get(key) for key in ('generation_id', 'name', 'status', 'retry_variants', 'counts', 'skipped')}
        for item in report['generations']]}, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
