"""Human chronology without promoting copied legacy files to new decisions."""
import json
from datetime import datetime,timezone

UNKNOWN_EXPORT_TIME='legacy_preparation_export_unknown_time'


def priority(raw):
    origin=raw.get('preparation_origin')or{}
    unknown=origin.get('created_basis')in ('original_preparation_export_mtime',UNKNOWN_EXPORT_TIME)
    return 0 if raw.get('feedback_source')=='imported_human_preparation'and unknown else 1


def chronology(raw,journal_order=0,created=None):
    value=created if created is not None else raw.get('created','')
    try:
        stamp=datetime.fromisoformat(value.replace('Z','+00:00'))
        if stamp.tzinfo is not None:value=stamp.astimezone(timezone.utc).isoformat(timespec='microseconds')
    except (ValueError,TypeError,AttributeError):pass
    return [priority(raw),value,journal_order]


def ordered(rows):
    """Retain original journal row IDs and timestamps; derive only read order."""
    def key(item):
        index,row=item;keys=row.keys()
        ordinal=row['journal_order']if 'journal_order'in keys else index
        return chronology(json.loads(row['raw']),ordinal,row['created'])
    return [row for _,row in sorted(enumerate(rows),key=key)]
