#!/usr/bin/env python3
"""Aggregate local Claude/Codex usage only; no messages, credentials or network calls."""
import argparse
import json
import os
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--from', dest='start', required=True, type=date.fromisoformat)
parser.add_argument('--to', dest='end', required=True, type=date.fromisoformat)
parser.add_argument('--timezone', default='Europe/Moscow')
parser.add_argument('--output', required=True, type=Path)
parser.add_argument('--claude-dir', type=Path, default=Path.home() / '.claude/projects', help='Claude project logs directory')
parser.add_argument('--codex-dir', type=Path, default=Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))), help='Codex home containing sessions and archived_sessions')
args = parser.parse_args()
if args.end < args.start:
    parser.error('--to must be on or after --from (both dates are inclusive)')
zone = ZoneInfo(args.timezone)
start = datetime.combine(args.start, time(), zone).astimezone(timezone.utc)
end = datetime.combine(args.end + timedelta(days=1), time(), zone).astimezone(timezone.utc)
def in_period(timestamp):
    try:
        instant = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
        return instant.tzinfo is not None and start <= instant < end
    except (ValueError, TypeError):
        return False

def empty():
    return dict(input=0, output=0, cache_read=0, cache_write_5m=0, cache_write_1h=0, responses=0)
usage = defaultdict(empty)
messages = {}
claude_files = list(args.claude_dir.rglob('*.jsonl'))
for path in claude_files:
    for line in path.open(errors='replace'):
        if '"usage"' not in line or '"model"' not in line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        message = event.get('message') or {}
        tokens, identifier, model = message.get('usage'), message.get('id'), message.get('model')
        if not tokens or not identifier or not model or model.startswith('<'):
            continue
        score = (tokens.get('output_tokens', 0), tokens.get('input_tokens', 0))
        if identifier in messages and messages[identifier]['score'] >= score:
            continue
        messages[identifier] = dict(score=score, date=event.get('timestamp', ''), model=model, tokens=tokens)
for message in messages.values():
    if not in_period(message['date']):
        continue
    tokens = message['tokens']
    row = usage[('Claude', message['model'])]
    creation = tokens.get('cache_creation') or {}
    row['input'] += tokens.get('input_tokens', 0)
    row['output'] += tokens.get('output_tokens', 0)
    row['cache_read'] += tokens.get('cache_read_input_tokens', 0)
    row['cache_write_5m'] += creation.get('ephemeral_5m_input_tokens', tokens.get('cache_creation_input_tokens', 0) - creation.get('ephemeral_1h_input_tokens', 0))
    row['cache_write_1h'] += creation.get('ephemeral_1h_input_tokens', 0)
    row['responses'] += 1

codex_home = args.codex_dir
codex_files = list((codex_home / 'sessions').rglob('*.jsonl')) + list((codex_home / 'archived_sessions').rglob('*.jsonl'))
seen_events = set()
for path in codex_files:
    model = None
    previous_total = None
    for line in path.open(errors='replace'):
        if '"turn_context"' not in line and '"token_count"' not in line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        payload = event.get('payload') or {}
        if event.get('type') == 'turn_context':
            model = payload.get('model') or model
            continue
        if payload.get('type') != 'token_count':
            continue
        info = payload.get('info') or {}
        tokens, total = info.get('last_token_usage'), info.get('total_token_usage')
        if not tokens or not total:
            continue
        signature = json.dumps(total, sort_keys=True)
        if signature == previous_total:
            continue
        previous_total = signature
        stamp = event.get('timestamp', '')
        if not in_period(stamp):
            continue
        identity = (stamp, model, signature, json.dumps(tokens, sort_keys=True))
        if identity in seen_events:
            continue
        seen_events.add(identity)
        row = usage[('Codex', model or 'unattributed')]
        cached = tokens.get('cached_input_tokens', 0)
        row['input'] += max(tokens.get('input_tokens', 0) - cached, 0)
        row['output'] += tokens.get('output_tokens', 0)
        row['cache_read'] += cached
        row['cache_write_5m'] += tokens.get('cache_write_input_tokens', 0)
        row['responses'] += 1
report = dict(period=dict(start=str(args.start), end=str(args.end), timezone=args.timezone, days=(args.end-args.start).days+1), source_files=dict(claude=len(claude_files), codex=len(codex_files)), models=[dict(source=source, model=model, **tokens) for (source, model), tokens in sorted(usage.items())], money='Token counters only; no paid subscription invoice or assumed API tariff.')
args.output.parent.mkdir(parents=True, exist_ok=True)
args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
args.output.chmod(0o600)
print(json.dumps(report, ensure_ascii=False))
