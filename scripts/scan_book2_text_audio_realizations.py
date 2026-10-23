from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import zipfile
from collections import Counter, defaultdict
from pathlib import Path


SPACE = re.compile(r"\s+")
TAG = re.compile(r"<[^>]+>")
ROUND_BRACKET = re.compile(r"\(([^()]*)\)")
SQUARE_BRACKET = re.compile(r"\[([^\[\]]*)\]")
WORD = re.compile(r"[0-9A-Za-zА-Яа-яЁё]+(?:[-‑][0-9A-Za-zА-Яа-яЁё]+)*")

PLACEHOLDER = re.compile(
    r"\b(?:нецензур\w*|неразборчив\w*|неперевод\w*|"
    r"удален\w*|удалён\w*|засекреч\w*|редактир\w*|redacted|"
    r"мат|ругатель\w*)\b",
    re.IGNORECASE,
)
PERFORMANCE_DIRECTION = re.compile(
    r"\b(?:ш[её]пот\w*|ш[её]потом|сме\w*|смех|вздых\w*|вздох\w*|"
    r"кашл\w*|крик\w*|крич\w*|пауза|в сторону|тихо|громко|"
    r"заика\w*|плачет|плач\w*|рыда\w*)\b",
    re.IGNORECASE,
)
NARRATOR_COMMENT = re.compile(
    r"\b(?:непонят\w*|я\s+(?:вообще\s+)?не\s+(?:понял|разобрал)|похоже|"
    r"како(?:й|е|я)-то|длинная\s+фраза|слишком\s+быстро|"
    r"уничижитель\w*)\b",
    re.IGNORECASE,
)
GAME_PARENTHETICAL = re.compile(
    r"\b(?:трофей|суммарно|накоплено|очк\w*|уров\w*|навык\w*|"
    r"оборудован\w*|модифицирован\w*)\b",
    re.IGNORECASE,
)


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def clean_text(value: str) -> str:
    return SPACE.sub(" ", html.unescape(TAG.sub(" ", value))).strip()


def normalized(value: str) -> str:
    return " ".join(word.casefold().replace("ё", "е") for word in WORD.findall(value))


def raw_epub_formatting(epub_path: Path) -> tuple[dict[str, set[str]], Counter]:
    formatted: dict[str, set[str]] = defaultdict(set)
    occurrences = Counter()
    pattern = re.compile(
        r"<(p|span)\b[^>]*class=[\"'](strong|emphasis|cite|text-author)[\"'][^>]*>"
        r"(.*?)</\1>",
        re.IGNORECASE | re.DOTALL,
    )
    with zipfile.ZipFile(epub_path) as epub:
        for name in epub.namelist():
            if not name.lower().endswith((".xhtml", ".html", ".htm")):
                continue
            source = epub.read(name).decode("utf-8", "replace")
            for match in pattern.finditer(source):
                text = normalized(clean_text(match.group(3)))
                if text:
                    style = match.group(2).casefold()
                    formatted[text].add(style)
                    occurrences[style] += 1
    return formatted, occurrences


def bracket_kind(content: str) -> str:
    if PERFORMANCE_DIRECTION.search(content):
        return "performance_direction"
    if NARRATOR_COMMENT.search(content):
        return "speech_internal_narrator_comment"
    if PLACEHOLDER.search(content):
        return "placeholder_spoken"
    if GAME_PARENTHETICAL.search(content):
        return "game_parenthetical"
    return "ordinary_parenthetical"


def text_features(text: str, paragraph: str, formats: set[str]) -> set[str]:
    features: set[str] = set()
    round_contents = ROUND_BRACKET.findall(text)
    if round_contents:
        features.add("round_brackets")
        features.update(bracket_kind(content) for content in round_contents)
    if SQUARE_BRACKET.search(text):
        features.add("square_brackets")
    if re.search(r"(?:\*{2,}|#{2,}|█{2,}|X{3,}|x{3,}|_{3,})", text):
        features.add("masking_symbols")
    if re.search(r"\d", text):
        features.add("digits")
    if re.search(r"[A-Za-z]", text):
        features.add("latin_script")
    if re.search(r"(?:^|\s)[IVXLCDM]{2,}(?=[\s.,;:!?)]|$)", text):
        features.add("roman_numerals")
    if re.search(r"(?:\+|%|№|\*|=|\d\s*/\s*\d)", text):
        features.add("game_or_math_notation")
    if re.search(r"(?:…|\.\.\.)", text):
        features.add("ellipsis_or_fragment")
    if re.search(r"\b(?:т\.\s*е\.|т\.\s*к\.|нпс|гг|hp|pvp|aoe|dps)\b", text, re.I):
        features.add("abbreviation_or_game_term")
    if re.search(r"\b[А-ЯЁ]{3,}(?:\s+[А-ЯЁ]{3,})+\b", text):
        features.add("all_caps")
    if re.match(r"\s*(?:\[[^\]]+\]\s*)?(?:[А-ЯЁA-Z][^:]{0,30}|\d{1,2}:\d{2})\s*:\s+", text):
        features.add("line_initial_colon_prefix_candidate")
    if re.search(r"\b([А-Яа-яЁё])-\1[А-Яа-яЁё]+", text):
        features.add("stutter_notation")
    if "strong" in formats:
        features.add("epub_strong_register")
    if "emphasis" in formats:
        features.add("epub_emphasis_register")
    # A bracketed comment embedded in a dialogue paragraph is structurally relevant
    # even when a short clip no longer carries the opening dialogue marker.
    if round_contents and re.match(r"\s*[—–-]", paragraph):
        features.add("brackets_inside_dialogue")
    return features


def compact_case(clip: dict, audit: dict, features: set[str], chapter_titles: dict[str, str]) -> dict:
    return {
        "id": clip["id"],
        "chapter_id": clip["chapter_id"],
        "chapter": chapter_titles.get(clip["chapter_id"], clip["chapter_id"]),
        "source_id": clip["source_id"],
        "status": audit["status"],
        "ratio": audit.get("ratio"),
        "features": sorted(features),
        "printed": clip["source_text"].strip(),
        "heard": audit.get("heard", "").strip(),
        "edits": audit.get("edits", []),
    }


def content_is_heard(content: str, heard: str) -> bool:
    expected_words = normalized(content).split()
    heard_normal = normalized(heard)
    if expected_words and " ".join(expected_words) in heard_normal:
        return True
    # Recognised placeholders may be inflected or split differently by ASR.
    stems = []
    lowered = content.casefold().replace("ё", "е")
    for stem in ("нецензурн", "непонятн", "неразборчив", "неперевод", "ругательств"):
        if stem in lowered:
            stems.append(stem)
    return bool(stems and all(stem in heard_normal.replace(" ", "") for stem in stems))


def main(argv=None, *, generic=False) -> None:
    parser = argparse.ArgumentParser(description="Read-only printed-text/audio realization scan.")
    parser.add_argument("--root", type=Path,
                        default=None if generic else Path("training-work/character-rb2/full"),
                        required=generic)
    parser.add_argument("--epub", type=Path,
                        default=None if generic else Path("content/reality-benders-2.epub"),
                        required=generic)
    parser.add_argument(
        "--output",
        type=Path,
        default=None if generic else Path("training-work/book2-text-audio-realization-scan"),
        required=generic,
    )
    parser.add_argument("--book-name", default="")
    args = parser.parse_args(argv)

    book_path = args.root / "source" / "book.json"
    clips_path = args.root / "clips.jsonl"
    audit_path = args.root / "clip-asr-audit.jsonl"
    book = json.loads(book_path.read_text(encoding="utf-8"))
    clips = read_jsonl(clips_path)
    audits = {row["id"]: row for row in read_jsonl(audit_path)}
    if len(clips) != len(audits) or {row["id"] for row in clips} != set(audits):
        raise SystemExit("Clip and ASR audit IDs do not match exactly.")

    chapter_titles = {chapter["id"]: chapter["title"] for chapter in book["chapters"]}
    blocks = {
        block["id"]: block
        for chapter in book["chapters"]
        for block in chapter.get("blocks", [])
    }
    raw_formats, raw_format_occurrences = raw_epub_formatting(args.epub)
    formats_by_source: dict[str, set[str]] = {}
    for source_id, block in blocks.items():
        formats_by_source[source_id] = set(raw_formats.get(normalized(block.get("text", "")), set()))

    source_feature_counts = Counter()
    block_kind_counts = Counter()
    for chapter in book["chapters"]:
        for block in chapter.get("blocks", []):
            block_kind_counts[block.get("kind", "unknown")] += 1
            for feature in text_features(
                block.get("text", ""),
                block.get("text", ""),
                formats_by_source.get(block["id"], set()),
            ):
                source_feature_counts[feature] += 1

    rows: list[dict] = []
    feature_stats: dict[str, Counter] = defaultdict(Counter)
    feature_ratios: dict[str, list[float]] = defaultdict(list)
    edit_pairs: dict[str, Counter] = defaultdict(Counter)
    status_counts = Counter()
    for clip in clips:
        audit = audits[clip["id"]]
        status_counts[audit["status"]] += 1
        features = text_features(
            clip.get("source_text", ""),
            clip.get("paragraph_text", ""),
            formats_by_source.get(clip["source_id"], set()),
        )
        case = compact_case(clip, audit, features, chapter_titles)
        rows.append(case)
        for feature in features:
            feature_stats[feature]["clips"] += 1
            feature_stats[feature][audit["status"]] += 1
            if audit["status"] != "exact_asr_match":
                feature_stats[feature]["mismatches"] += 1
            if isinstance(audit.get("ratio"), (int, float)):
                feature_ratios[feature].append(float(audit["ratio"]))
            for edit in audit.get("edits", []):
                expected = " ".join(edit.get("expected", [])) or "∅"
                heard = " ".join(edit.get("heard", [])) or "∅"
                edit_pairs[feature][f"{expected} → {heard}"] += 1

    features = []
    for feature, counts in sorted(feature_stats.items(), key=lambda item: (-item[1]["clips"], item[0])):
        total = counts["clips"]
        features.append(
            {
                "feature": feature,
                **dict(counts),
                "mismatch_rate": round(counts["mismatches"] / total, 4),
                "mean_match_ratio": round(sum(feature_ratios[feature]) / len(feature_ratios[feature]), 4),
                "common_edits": [
                    {"edit": edit, "count": count}
                    for edit, count in edit_pairs[feature].most_common(10)
                ],
            }
        )

    clips_by_source: dict[str, list[tuple[dict, dict]]] = defaultdict(list)
    for clip in clips:
        clips_by_source[clip["source_id"]].append((clip, audits[clip["id"]]))

    # Count bracket forms once per EPUB paragraph, rather than once per overlapping clip.
    bracket_spans: list[dict] = []
    for chapter in book["chapters"]:
        for block in chapter.get("blocks", []):
            text = block.get("text", "")
            for match in ROUND_BRACKET.finditer(text):
                content = match.group(1).strip()
                source_clips = clips_by_source.get(block["id"], [])
                supporting = [
                    audit["id"]
                    for _clip, audit in source_clips
                    if content_is_heard(content, audit.get("heard", ""))
                ]
                bracket_spans.append(
                    {
                        "kind": bracket_kind(content),
                        "content": content,
                        "chapter_id": chapter["id"],
                        "chapter": chapter["title"],
                        "source_id": block["id"],
                        "inside_dialogue_paragraph": bool(re.match(r"\s*[—–-]", text)),
                        "covered_by_clip": bool(source_clips),
                        "read_aloud_in_asr": bool(supporting),
                        "supporting_clip_ids": supporting,
                        "paragraph": text,
                    }
                )

    realization_patterns: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        printed = row["printed"]
        heard = row["heard"].casefold().replace("ё", "е")
        if "+" in printed and "плюс" in heard:
            realization_patterns["plus_sign_spoken_as_plus"].append(row["id"])
        if "%" in printed and "процент" in heard:
            realization_patterns["percent_sign_spoken_as_percent"].append(row["id"])
        if re.search(r"\b[IVXLCDM]{2,}\b", printed) and re.search(r"\b\d+\b", heard):
            realization_patterns["roman_numeral_recognised_as_arabic_number"].append(row["id"])
        if "(" in printed and ")" in printed:
            contents = ROUND_BRACKET.findall(printed)
            if contents and all(content_is_heard(content, row["heard"]) for content in contents):
                realization_patterns["round_bracket_content_read_aloud"].append(row["id"])
        if "[" in printed and "]" in printed:
            contents = SQUARE_BRACKET.findall(printed)
            if contents and all(content_is_heard(content, row["heard"]) for content in contents):
                realization_patterns["square_bracket_content_read_aloud"].append(row["id"])

    intrinsic = {
        "placeholder_spoken",
        "performance_direction",
        "speech_internal_narrator_comment",
        "game_parenthetical",
        "square_brackets",
        "masking_symbols",
        "game_or_math_notation",
        "line_initial_colon_prefix_candidate",
        "epub_strong_register",
        "epub_emphasis_register",
    }
    cases = [
        row
        for row in rows
        if row["status"] != "exact_asr_match" and row["features"]
        or intrinsic.intersection(row["features"])
    ]
    cases.sort(key=lambda row: (row["chapter_id"], row["source_id"], row["id"]))

    bracket_kinds = (
        "placeholder_spoken",
        "speech_internal_narrator_comment",
        "performance_direction",
        "game_parenthetical",
        "ordinary_parenthetical",
    )
    result = {
        "schema": 1,
        "scope": "read-only diagnostic; ASR differences are candidates, not narrator ground truth",
        "sources": {
            "book": str(book_path),
            "book_sha256": sha256(book_path),
            "clips": str(clips_path),
            "clips_sha256": sha256(clips_path),
            "asr_audit": str(audit_path),
            "asr_audit_sha256": sha256(audit_path),
            "epub": str(args.epub),
            "epub_sha256": sha256(args.epub),
        },
        "corpus": {
            "clips": len(clips),
            "statuses": dict(status_counts),
            "baseline_mismatch_rate": round(
                (len(clips) - status_counts["exact_asr_match"]) / len(clips), 4
            ),
            "source_block_kinds": dict(block_kind_counts),
            "source_feature_blocks": dict(source_feature_counts),
            "epub_formatted_paragraphs": dict(raw_format_occurrences),
        },
        "features": features,
        "bracket_summary": {
            kind: sum(row["kind"] == kind for row in bracket_spans)
            for kind in bracket_kinds
        },
        "bracket_audio_evidence": {
            kind: {
                "occurrences": len(items),
                "covered_by_clip": sum(bool(item["covered_by_clip"]) for item in items),
                "read_aloud_in_asr": sum(bool(item["read_aloud_in_asr"]) for item in items),
            }
            for kind, items in (
                (kind, [row for row in bracket_spans if row["kind"] == kind])
                for kind in bracket_kinds
            )
        },
        "bracket_spans": bracket_spans,
        "realization_patterns": {
            name: {"count": len(ids), "clip_ids": ids}
            for name, ids in sorted(realization_patterns.items())
        },
        "cases": cases,
    }

    args.output.mkdir(parents=True, exist_ok=True)
    json_path = args.output / "scan.json"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    book_name = args.book_name or book.get('title') or args.root.name
    prepared_formatting = Counter(
        class_name
        for block in blocks.values()
        for values in (block.get('formatting', {}).get('classes', {}) or {}).values()
        for class_name in values
    )
    lines = [
        f"# {book_name} — printed text vs narrated audio scan",
        "",
        "This is a read-only diagnostic. ASR disagreements identify candidates for listening; they do not prove how the narrator performed a passage.",
        "",
        f"- Clips joined to ASR audit: **{len(clips):,}**",
        f"- Exact ASR matches: **{status_counts['exact_asr_match']:,}**",
        f"- Review-only matches: **{status_counts['review_only']:,}**",
        f"- Held weak matches: **{status_counts['hold']:,}**",
        f"- Overall non-exact rate: **{result['corpus']['baseline_mismatch_rate']:.1%}**",
        "",
        "## Printed-form signals",
        "",
        "| Signal | Clips | Non-exact | Rate | Mean match ratio |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for feature in features:
        lines.append(
            f"| `{feature['feature']}` | {feature['clips']} | {feature.get('mismatches', 0)} | "
            f"{feature['mismatch_rate']:.1%} | {feature['mean_match_ratio']:.3f} |"
        )
    lines += [
        "",
        "## Bracket contents in the EPUB (unique paragraph occurrences)",
        "",
    ]
    for kind, count in sorted(result["bracket_summary"].items(), key=lambda item: (-item[1], item[0])):
        evidence = result["bracket_audio_evidence"][kind]
        lines.append(
            f"- `{kind}`: **{count}**; **{evidence['covered_by_clip']}** have clip coverage and "
            f"**{evidence['read_aloud_in_asr']}** are directly seen in ASR"
        )
    lines += ["", "## Directly observed realization patterns", ""]
    for name, evidence in result["realization_patterns"].items():
        lines.append(f"- `{name}`: **{evidence['count']}** clips")
    lines += [
        "",
        "## EPUB formatting coverage",
        "",
        f"- Explicit strong-register paragraphs in the raw EPUB: **{result['corpus']['epub_formatted_paragraphs'].get('strong', 0)}**",
        f"- Explicit emphasis-register paragraphs in the raw EPUB: **{result['corpus']['epub_formatted_paragraphs'].get('emphasis', 0)}**",
        f"- CSS/FB2 semantic class occurrences preserved in this prepared `book.json`: **{sum(prepared_formatting.values())}**. Re-import older snapshots to populate this field.",
        f"- Square-bracket text occurs in **{source_feature_counts.get('square_brackets', 0)}** source paragraphs.",
        f"- Masking runs occur in **{source_feature_counts.get('masking_symbols', 0)}** source paragraphs.",
        f"- Parenthesized performance directions occur **{result['bracket_summary']['performance_direction']}** times.",
        f"- The EPUB contains **{block_kind_counts.get('figure', 0)}** figure blocks; their extracted text is empty, so this book supplies no image captions to narrate.",
        "",
        "## Candidate cases",
        "",
        f"The machine-readable report contains **{len(cases)}** feature-bearing cases in `scan.json`. Listen before converting any pattern into a production rule.",
        "",
    ]
    (args.output / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({
        "output": str(args.output),
        "clips": len(clips),
        "statuses": dict(status_counts),
        "features": len(features),
        "bracket_summary": result["bracket_summary"],
        "candidate_cases": len(cases),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
