#!/usr/bin/env python3
"""Check production recovery receipts without reading credentials or customer data."""
from datetime import datetime,timezone
import json
from pathlib import Path


def check(path, *, ignore_failure=None):
    state=json.loads(Path(path).read_text())
    for action,hours in [('backup',36),('check',8*24),('restore_verify',35*24),('sanitize_refresh',8*24)]:
        age=(datetime.now(timezone.utc)-datetime.fromisoformat(state[action]['at'])).total_seconds()
        if age < -300 or age>hours*3600:raise ValueError('Production recovery receipt missing or stale: '+action)
    if state['sanitize_refresh'].get('source')!='production-backup':raise ValueError('Only synthetic rehearsal has run')
    if state['testing_smoke']['sanitized_at']!=state['sanitize_refresh']['at']:raise ValueError('Latest dataset has no matching smoke receipt')
    if state['testing_realtime']['sanitized_at']!=state['sanitize_refresh']['at']:raise ValueError('Latest dataset has no matching Realtime receipt')
    failure=state.get('failure',{});action=failure.get('action','').replace('-','_')
    if action != ignore_failure and failure.get('at','')>state.get(action,{}).get('at',''):raise ValueError('Latest production recovery action failed')
    return {'production_recovery':'healthy'}


if __name__=='__main__':
    try:print(json.dumps(check('/var/lib/dealpipe-recovery/status.json')))
    except Exception:raise SystemExit('Production recovery health failed; inspect receipts privately') from None
