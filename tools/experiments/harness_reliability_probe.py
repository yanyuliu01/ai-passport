"""Audit baseline probe: temporary state, no model/network/device calls.

This characterizes reviewed defects, not an acceptance gate. When implementing
fixes, replace these baseline assertions with the desired regression invariants.
"""
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'runtime'))
from xiaoyou_runtime.cards import Cards
from xiaoyou_runtime.service import Service
from xiaoyou_runtime.store import Store
from xiaoyou_runtime.tasks import Tasks, Task, MAX_PENDING
from xiaoyou_runtime.desktop_sync import Session, DesktopSync

results = {}
with tempfile.TemporaryDirectory() as folder:
    root = Path(folder)
    store = Store(root)
    def service():
        return Service(SimpleNamespace(cards=Cards(root/'cards.json'), agents=lambda: []), store)
    with patch.object(Service, '_line_up', lambda *args: None):
        first = service()
        original = first.submit('audit A', 'A', 'request-1')
        duplicate = first.submit('audit A', 'A', 'request-1')
        collision = first.submit('audit B', 'B', 'request-1')
        restarted = service().submit('audit A', 'A', 'request-1')
    results['same_instance_dedup'] = duplicate['id'] == original['id']
    results['restart_creates_new_id'] = restarted['id'] != original['id']
    results['cross_conversation_returns_A'] = collision['conversation'] == 'A'
    results['accepted_text_persisted'] = any('audit A' in p.read_text() for p in root.glob('*.json'))

    tasks = Tasks(store, lambda *args: None)
    task = Task('c-test', 'A', SimpleNamespace(name='fake'), 'audit task')
    tasks._active[task.card] = task
    returns = [tasks.amend(task.card, 'after', 'followup-%d' % i) for i in range(MAX_PENDING+1)]
    results['amend_after'] = {'accepted_results': len([r for r in returns if r == 'after']),
                              'queued': len(task.after), 'last_missing': 'followup-8' not in task.after}

    cards = Cards(root/'observed.json')
    sync = DesktopSync(cards, root, home=root, clock=lambda:100)
    session = Session('codex', 'audit-session', cwd=str(root), status='idle', observed_at=100)
    sync._publish(session, 100)
    results['idle_projection'] = cards.get(session.card)['state']
    results['minor_publish'] = {}
    session.transition('running',101); sync._publish(session,101)
    seq = cards.get(session.card)['seq']
    session.tool='read_file'; session.observed_at=102; sync._publish(session,102)
    results['minor_publish']['at_1s_unchanged'] = cards.get(session.card)['seq'] == seq
    sync._publish(session,111)
    results['minor_publish']['at_10s_changed'] = cards.get(session.card)['seq'] > seq

print(json.dumps(results, indent=2))
assert results['same_instance_dedup']
assert results['restart_creates_new_id'] and results['cross_conversation_returns_A']
assert not results['accepted_text_persisted']
assert results['amend_after']['accepted_results'] == 9 and results['amend_after']['queued'] == 8
assert results['idle_projection'] == 'done'
assert all(results['minor_publish'].values())
