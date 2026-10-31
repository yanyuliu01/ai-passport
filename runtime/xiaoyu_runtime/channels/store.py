"""Durable channel inbox and outbox. The Runtime remains the chat authority."""
import base64
import hashlib
import json
import os
import sqlite3
from pathlib import Path


def client_id(channel, account, message_id):
    raw = json.dumps([channel, account, str(message_id)], ensure_ascii=False,
                     separators=(',', ':')).encode('utf-8')
    # URL-safe base64 retains the complete SHA-256 digest within the Runtime's
    # 64-character client ID limit, without slash or padding characters.
    digest = base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).decode('ascii').rstrip('=')
    return 'channel-' + digest


def public_blocks(message):
    """Only complete public assistant blocks in the canonical main chat."""
    p = message.get('presentation') or {}
    author = p.get('author') or {}
    if (p.get('version') != 1 or p.get('conversation') != 'default'
            or p.get('placement') != 'chat' or author.get('kind') != 'xiaoyou'
            or author.get('name') != 'xiaoyou' or p.get('kind') == 'activity'):
        return []
    return [(block['id'], block['text']) for block in p.get('blocks', [])
            if isinstance(block, dict) and block.get('complete') is True
            and isinstance(block.get('id'), str) and block['id']
            and isinstance(block.get('text'), str) and block['text'].strip()]


class ChannelStore:
    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        # The database contains private messages. Create with restrictive mode,
        # without changing permissions on a pre-existing file.
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600) if not path.exists() else None
        if fd is not None:
            os.close(fd)
        self.db = sqlite3.connect(str(path))
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS cursor (name TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS inbox (
              channel TEXT NOT NULL, account TEXT NOT NULL, message_id TEXT NOT NULL,
              sender TEXT NOT NULL, payload TEXT NOT NULL, runtime_id TEXT,
              PRIMARY KEY(channel, account, message_id));
            CREATE TABLE IF NOT EXISTS outbox (
              destination TEXT NOT NULL, block_id TEXT NOT NULL, text TEXT NOT NULL,
              state TEXT NOT NULL DEFAULT 'pending',
              PRIMARY KEY(destination, block_id));
        ''')
        self.db.commit()

    def close(self):
        self.db.close()

    def cursor(self, name):
        row = self.db.execute('SELECT value FROM cursor WHERE name=?', (name,)).fetchone()
        return row[0] if row else ''

    def ingest(self, channel, account, messages, cursor):
        """Commit an entire channel batch and its cursor in one transaction.

        Callers validate identities and message types before admission. Reject a
        conflicting repeated ID instead of silently replacing accepted content.
        """
        with self.db:
            for item in messages:
                mid, sender, payload = str(item['id']), item['sender'], item['payload']
                if not mid or not sender or not isinstance(payload, str):
                    raise ValueError('invalid incoming message')
                existing = self.db.execute(
                    'SELECT sender,payload FROM inbox WHERE channel=? AND account=? AND message_id=?',
                    (channel, account, mid)).fetchone()
                if existing and existing != (sender, payload):
                    raise ValueError('conflicting incoming message ID')
                self.db.execute('INSERT OR IGNORE INTO inbox(channel,account,message_id,sender,payload) VALUES(?,?,?,?,?)',
                                (channel, account, mid, sender, payload))
            if cursor:
                self.db.execute('INSERT OR REPLACE INTO cursor VALUES(?,?)',
                                ('in:' + channel + ':' + account, cursor))

    def pending_inbox(self):
        return self.db.execute('SELECT channel,account,message_id,sender,payload FROM inbox WHERE runtime_id IS NULL ORDER BY rowid').fetchall()

    def accepted(self, channel, account, message_id, runtime_id):
        if not runtime_id:
            raise ValueError('missing runtime identity')
        with self.db:
            self.db.execute('UPDATE inbox SET runtime_id=? WHERE channel=? AND account=? AND message_id=? AND runtime_id IS NULL',
                            (runtime_id, channel, account, str(message_id)))

    def bootstrap_sync(self, destination, epoch, seq):
        """Start at the current snapshot boundary without delivering history."""
        if not epoch or not isinstance(seq, int) or isinstance(seq, bool) or seq < 0:
            raise ValueError('invalid Runtime cursor')
        with self.db:
            self.db.execute('INSERT INTO cursor VALUES(?,?)',
                            ('sync:' + destination, json.dumps([epoch, seq])))

    def apply_sync(self, destination, epoch, seq, messages, reset=False):
        """Atomically queue eligible blocks and advance the Runtime cursor."""
        if not epoch or not isinstance(seq, int) or isinstance(seq, bool) or seq < 0:
            raise ValueError('invalid Runtime cursor')
        with self.db:
            previous = self.cursor('sync:' + destination)
            if not previous or reset:
                raise ValueError('sync requires bootstrap or gap reconciliation')
            old_epoch, old_seq = json.loads(previous)
            if epoch != old_epoch or seq < old_seq:
                raise ValueError('sync cursor changed; reconciliation required')
            for message in messages:
                for block_id, text in public_blocks(message):
                    old = self.db.execute('SELECT text FROM outbox WHERE destination=? AND block_id=?',
                                          (destination, block_id)).fetchone()
                    if old and old[0] != text:
                        raise ValueError('published block changed; reconciliation required')
                    self.db.execute('INSERT OR IGNORE INTO outbox(destination,block_id,text) VALUES(?,?,?)',
                                    (destination, block_id, text))
            self.db.execute('INSERT OR REPLACE INTO cursor VALUES(?,?)',
                            ('sync:' + destination, json.dumps([epoch, seq])))

    def next_outgoing(self, destination):
        # An uncertain earlier delivery blocks later sends to preserve ordering.
        row = self.db.execute("SELECT block_id,text,state FROM outbox WHERE destination=? AND state NOT IN ('sent','cancelled') ORDER BY rowid LIMIT 1", (destination,)).fetchone()
        return (row[0], row[1]) if row and row[2] == 'pending' else None

    def mark(self, destination, block_id, old, new):
        # 'sending' is durable before touching the network. After a crash it is
        # uncertain, not a retryable pending send.
        allowed = {('pending', 'sending'), ('sending', 'sent'), ('sending', 'unknown'),
                   ('sending', 'pending'), ('pending', 'cancelled')}
        if (old, new) not in allowed:
            raise ValueError('invalid delivery transition')
        with self.db:
            changed = self.db.execute('UPDATE outbox SET state=? WHERE destination=? AND block_id=? AND state=?',
                                      (new, destination, block_id, old)).rowcount
        return changed == 1
