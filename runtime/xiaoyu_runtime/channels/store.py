"""Durable channel inbox and outbox. The Runtime remains the chat authority."""
import base64
import hashlib
import json
import math
import re
import os
import sqlite3
import time
from pathlib import Path

# Think concurrently, but leave a brief opening for a follow-up message.
INPUT_PAUSE = 2.0


def client_id(channel, account, message_id):
    raw = json.dumps([channel, account, str(message_id)], ensure_ascii=False,
                     separators=(',', ':')).encode('utf-8')
    # URL-safe base64 retains the complete SHA-256 digest within the Runtime's
    # 64-character client ID limit, without slash or padding characters.
    digest = base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).decode('ascii').rstrip('=')
    return 'channel-' + digest


def public_blocks(message):
    """Only complete public assistant blocks in the WeChat conversation."""
    p = message.get('presentation') or {}
    author = p.get('author') or {}
    if (p.get('version') != 1 or p.get('conversation') != 'weixin'
            or p.get('placement') != 'chat' or author.get('kind') != 'xiaoyou'
            or author.get('name') != 'xiaoyou' or p.get('kind') == 'activity'):
        return []
    return [(block['id'], block['text']) for block in p.get('blocks', [])
            if isinstance(block, dict) and block.get('complete') is True
            and isinstance(block.get('id'), str) and block['id']
            and isinstance(block.get('text'), str) and block['text'].strip()]


def public_attachments(message):
    """Completed assistant attachments only; owner inputs are never echoed."""
    p = message.get('presentation') or {}
    author = p.get('author') or {}
    if (p.get('version') != 1 or p.get('conversation') != 'weixin'
            or p.get('placement') != 'chat' or author.get('kind') != 'xiaoyou' or author.get('name') != 'xiaoyou'
            or p.get('kind') == 'activity' or message.get('status') not in ('done', 'final')):
        return []
    result = []
    for item in message.get('attachments') or []:
        if (isinstance(item, dict) and item.get('status') == 'ready' and item.get('source') != 'owner'
                and isinstance(item.get('id'), str) and re.fullmatch(r'a[0-9a-f]{32}', item['id'])):
            result.append(item['id'])
    return list(dict.fromkeys(result))


def reading_pause(text, kind='text'):
    """Give the preceding bubble time to be read; never delay a first reply."""
    characters = len(text.strip()) if kind == 'text' and isinstance(text, str) else 0
    return max(0.6, min(2.0, 0.6 + characters * 0.02))


class ChannelStore:
    def __init__(self, path, *, timeout=5.0):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        # The database contains private messages. Create with restrictive mode,
        # without changing permissions on a pre-existing file.
        # Input and output workers can open the database concurrently on the
        # first start. A competing exclusive create is a normal race, not an
        # initialization failure.
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            fd = None
        if fd is not None:
            os.close(fd)
        self.db = sqlite3.connect(str(path), timeout=timeout)
        try:
            self._initialize()
        except BaseException:
            self.db.close()
            raise

    def _initialize(self):
        # Serialize migration across the separate input/output connections.
        # Otherwise both can observe a missing legacy column and race on ALTER.
        self.db.execute('BEGIN IMMEDIATE')
        for statement in ('''CREATE TABLE IF NOT EXISTS cursor (name TEXT PRIMARY KEY, value TEXT NOT NULL)''',
            '''CREATE TABLE IF NOT EXISTS inbox (
              channel TEXT NOT NULL, account TEXT NOT NULL, message_id TEXT NOT NULL,
              sender TEXT NOT NULL, payload TEXT NOT NULL, runtime_id TEXT, context_token TEXT,
              PRIMARY KEY(channel, account, message_id))''',
            '''CREATE TABLE IF NOT EXISTS outbox (
              destination TEXT NOT NULL, block_id TEXT NOT NULL, text TEXT NOT NULL,
              state TEXT NOT NULL DEFAULT 'pending', request_id TEXT,
              delivery_revision INTEGER NOT NULL DEFAULT 1, projected_revision INTEGER NOT NULL DEFAULT 0,
              last_outcome TEXT,
              PRIMARY KEY(destination, block_id))''',
            '''CREATE TABLE IF NOT EXISTS task_delivery (
              destination TEXT NOT NULL, task_id TEXT NOT NULL, run_id TEXT NOT NULL,
              request_id TEXT NOT NULL, PRIMARY KEY(destination, task_id, run_id))''',
            '''CREATE TABLE IF NOT EXISTS delivery_pacing (
              destination TEXT PRIMARY KEY, request_id TEXT NOT NULL, next_at REAL NOT NULL)''',
            '''CREATE TABLE IF NOT EXISTS delivery_confirmation (
              destination TEXT NOT NULL, block_id TEXT NOT NULL, confirmed_at REAL NOT NULL,
              PRIMARY KEY(destination, block_id))''',
            '''CREATE TABLE IF NOT EXISTS proactive_share (
              share_id TEXT PRIMARY KEY, text TEXT NOT NULL, created_at REAL NOT NULL,
              account TEXT NOT NULL, owner TEXT NOT NULL,
              state TEXT NOT NULL DEFAULT 'pending', outcome TEXT)''',
            '''CREATE TABLE IF NOT EXISTS proactive_link (
              share_id TEXT PRIMARY KEY, url TEXT NOT NULL, created_at REAL NOT NULL,
              account TEXT NOT NULL, owner TEXT NOT NULL,
              state TEXT NOT NULL DEFAULT 'pending', outcome TEXT)''',
            '''CREATE TABLE IF NOT EXISTS retry_command (
              operation TEXT PRIMARY KEY, destination TEXT NOT NULL, block_id TEXT NOT NULL,
              confirmed INTEGER NOT NULL, result TEXT NOT NULL, expected_revision INTEGER NOT NULL DEFAULT 0)'''):
            self.db.execute(statement)
        if 'context_token' not in {row[1] for row in self.db.execute('PRAGMA table_info(inbox)')}:
            self.db.execute('ALTER TABLE inbox ADD COLUMN context_token TEXT')
        if 'media_json' not in {row[1] for row in self.db.execute('PRAGMA table_info(inbox)')}:
            self.db.execute('ALTER TABLE inbox ADD COLUMN media_json TEXT')
        if 'quotes_json' not in {row[1] for row in self.db.execute('PRAGMA table_info(inbox)')}:
            self.db.execute('ALTER TABLE inbox ADD COLUMN quotes_json TEXT')
        if 'received_at' not in {row[1] for row in self.db.execute('PRAGMA table_info(inbox)')}:
            self.db.execute('ALTER TABLE inbox ADD COLUMN received_at REAL')
        if 'request_id' not in {row[1] for row in self.db.execute('PRAGMA table_info(outbox)')}:
            self.db.execute('ALTER TABLE outbox ADD COLUMN request_id TEXT')
        columns = {row[1] for row in self.db.execute('PRAGMA table_info(outbox)')}
        if 'delivery_revision' not in columns:
            self.db.execute('ALTER TABLE outbox ADD COLUMN delivery_revision INTEGER NOT NULL DEFAULT 1')
        if 'projected_revision' not in columns:
            self.db.execute('ALTER TABLE outbox ADD COLUMN projected_revision INTEGER NOT NULL DEFAULT 0')
        if 'last_outcome' not in columns:
            self.db.execute('ALTER TABLE outbox ADD COLUMN last_outcome TEXT')
        if 'kind' not in columns:
            self.db.execute("ALTER TABLE outbox ADD COLUMN kind TEXT NOT NULL DEFAULT 'text'")
        if 'interruptible' not in columns:
            self.db.execute('ALTER TABLE outbox ADD COLUMN interruptible INTEGER NOT NULL DEFAULT 0')
        if 'sent_at' not in columns:
            self.db.execute('ALTER TABLE outbox ADD COLUMN sent_at REAL')
        if 'server_message_id' not in columns:
            self.db.execute('ALTER TABLE outbox ADD COLUMN server_message_id TEXT')
        self.db.execute('CREATE INDEX IF NOT EXISTS outbox_server_message ON outbox(server_message_id)')
        if 'last_sent_at' not in {row[1] for row in self.db.execute('PRAGMA table_info(delivery_pacing)')}:
            self.db.execute('ALTER TABLE delivery_pacing ADD COLUMN last_sent_at REAL')
        if 'expected_revision' not in {row[1] for row in self.db.execute('PRAGMA table_info(retry_command)')}:
            self.db.execute('ALTER TABLE retry_command ADD COLUMN expected_revision INTEGER NOT NULL DEFAULT 0')
        # Enabling media delivery must never replay attachments on historical
        # messages that happen to be revised or re-synchronized later.
        self.db.execute('INSERT OR IGNORE INTO cursor(name,value) VALUES(?,?)',
                        ('media:activation', repr(time.time())))
        self.db.execute('CREATE INDEX IF NOT EXISTS outbox_delivery_dirty ON outbox(destination) '
                        'WHERE projected_revision < delivery_revision')
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
                identity = item['id']
                if isinstance(identity, bool) or not isinstance(identity, (str, int)):
                    raise ValueError('invalid incoming message ID')
                mid, sender, payload = str(identity), item['sender'], item['payload']
                context = item.get('context_token')
                media = item.get('media')
                quotes = item.get('quotes')
                if media is not None and (not isinstance(media, list) or not 1 <= len(media) <= 4):
                    raise ValueError('invalid incoming attachment')
                if quotes is not None and (not isinstance(quotes, list) or not 1 <= len(quotes) <= 4
                        or any(not isinstance(q, dict) for q in quotes)):
                    raise ValueError('invalid incoming reference')
                media_json = json.dumps(media, ensure_ascii=False, sort_keys=True, separators=(',', ':')) if media else None
                if media_json is not None and len(media_json) > 65536:
                    raise ValueError('invalid incoming attachment')
                if not mid or not sender or not isinstance(payload, str) or (context is not None and not isinstance(context, str)):
                    raise ValueError('invalid incoming message')
                existing = self.db.execute(
                    'SELECT sender,payload,context_token,media_json,quotes_json FROM inbox WHERE channel=? AND account=? AND message_id=?',
                    (channel, account, mid)).fetchone()
                if existing and (existing[:2] != (sender, payload) or
                                 (existing[2] and context and existing[2] != context) or existing[3] != media_json):
                    raise ValueError('conflicting incoming message ID')
                if existing and existing[4]:
                    saved = json.loads(existing[4])
                    if [entry['source'] for entry in saved] != (quotes or []):
                        raise ValueError('conflicting incoming reference')
                quote_entries = []
                if not existing and quotes:
                    for quote in quotes:
                        resolved = dict(quote)
                        if quote.get('kind') in ('text', 'summary', 'unavailable') and quote.get('id'):
                            found = self._quote_text(channel, account, sender, quote['id'])
                            if found is not None:
                                if quote.get('kind') == 'unavailable':
                                    resolved.update(found)
                                else:
                                    # Preserve the original inline quote or its
                                    # partial selection; cache only attribution.
                                    resolved.update({k: found[k] for k in ('author', 'reply_to') if k in found})
                        quote_entries.append({'source': quote, 'resolved': resolved})
                quotes_json = json.dumps(quote_entries, ensure_ascii=False, sort_keys=True, separators=(',', ':')) if quote_entries else None
                if quotes_json is not None and len(quotes_json) > 30000:
                    raise ValueError('invalid incoming reference')
                self.db.execute('INSERT OR IGNORE INTO inbox(channel,account,message_id,sender,payload,context_token,media_json,quotes_json,received_at) VALUES(?,?,?,?,?,?,?,?,?)',
                                (channel, account, mid, sender, payload, context, media_json, quotes_json, time.time()))
                if context:
                    self.db.execute('UPDATE inbox SET context_token=? WHERE channel=? AND account=? AND message_id=? AND context_token IS NULL',
                                    (context, channel, account, mid))
            if cursor:
                self.db.execute('INSERT OR REPLACE INTO cursor VALUES(?,?)',
                                ('in:' + channel + ':' + account, cursor))

    def _quote_text(self, channel, account, sender, identity):
        """Resolve only exact messages belonging to this bound conversation."""
        row = self.db.execute('SELECT payload FROM inbox WHERE channel=? AND account=? AND sender=? AND message_id=?',
                              (channel, account, sender, str(identity))).fetchone()
        rows = self.db.execute("SELECT o.text,i.payload FROM outbox o JOIN inbox i ON o.request_id=i.runtime_id "
                               "WHERE i.channel=? AND i.account=? AND i.sender=? AND o.state='sent' "
                               "AND o.kind='text' AND o.server_message_id=? LIMIT 2",
                               (channel, account, sender, str(identity))).fetchall()
        candidates = ([{'text': row[0], 'author': 'owner'}] if row and row[0] else [])
        candidates += [{'text': text, 'author': 'xiaoyou', 'reply_to': original[:1000]}
                       for text, original in rows]
        if len(candidates) != 1:
            return None
        found = candidates[0]
        text = found['text']
        found.update(kind='text', text=text[:3000], truncated=len(text) > 3000)
        return found

    def pending_inbox(self):
        return self.db.execute('SELECT channel,account,message_id,sender,payload FROM inbox WHERE runtime_id IS NULL ORDER BY rowid').fetchall()

    def inbox_media(self, channel, account, message_id):
        row = self.db.execute('SELECT media_json FROM inbox WHERE channel=? AND account=? AND message_id=?',
                              (channel, account, message_id)).fetchone()
        return json.loads(row[0]) if row and row[0] else []

    def reply_address(self, channel, account, runtime_id):
        """Return a private destination and reply context for one accepted turn."""
        rows = self.db.execute('SELECT sender,context_token FROM inbox WHERE channel=? AND account=? AND runtime_id=?',
                               (channel, account, runtime_id)).fetchall()
        if len(rows) != 1 or not rows[0][1]:
            return None
        return rows[0]

    def enqueue_proactive_share(self, share_id, text, account, owner):
        """Persist one explicitly enabled autonomous share, with its own identity."""
        if not all(isinstance(value, str) and value.strip() for value in (share_id, text, account, owner)):
            raise ValueError('invalid proactive share')
        with self.db:
            old = self.db.execute('SELECT text,account,owner FROM proactive_share WHERE share_id=?', (share_id,)).fetchone()
            if old and old != (text, account, owner):
                raise ValueError('proactive share identity changed')
            self.db.execute('INSERT OR IGNORE INTO proactive_share(share_id,text,created_at,account,owner) VALUES(?,?,?,?,?)',
                            (share_id, text, time.time(), account, owner))

    def recover_proactive_shares(self):
        with self.db:
            self.db.execute("UPDATE proactive_share SET state='unknown',outcome='interrupted' WHERE state='sending'")
            self.db.execute("UPDATE proactive_link SET state='unknown',outcome='interrupted' WHERE state='sending'")

    def enqueue_proactive_video(self, share_id, recommendation, url, account, owner):
        """Queue a recommendation and its video link as two independent sends."""
        if not all(isinstance(value, str) and value.strip() for value in (share_id, recommendation, url, account, owner)):
            raise ValueError('invalid proactive video share')
        from ..entertainment.share import split_video_share
        if split_video_share(recommendation + '\n' + url) != (recommendation, url):
            raise ValueError('invalid video share content')
        now = time.time()
        self.db.execute('BEGIN IMMEDIATE')
        try:
            caption = self.db.execute('SELECT text,account,owner FROM proactive_share WHERE share_id=?', (share_id,)).fetchone()
            link = self.db.execute('SELECT url,account,owner FROM proactive_link WHERE share_id=?', (share_id,)).fetchone()
            if caption or link:
                if caption != (recommendation, account, owner) or link != (url, account, owner):
                    raise ValueError('proactive video identity changed')
            else:
                self.db.execute('INSERT INTO proactive_share(share_id,text,created_at,account,owner) VALUES(?,?,?,?,?)',
                                (share_id, recommendation, now, account, owner))
                self.db.execute('INSERT INTO proactive_link(share_id,url,created_at,account,owner) VALUES(?,?,?,?,?)',
                                (share_id, url, now, account, owner))
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    def claim_proactive_link(self, channel, account, owner, destination):
        """Claim the link only after its matching recommendation was accepted."""
        self.db.execute('BEGIN IMMEDIATE')
        try:
            self.db.execute("UPDATE proactive_link SET state='expired',outcome='expired' "
                            "WHERE state='pending' AND created_at<?", (time.time() - 21600,))
            self.db.execute("UPDATE proactive_link SET state='cancelled',outcome='text_not_confirmed' "
                            "WHERE state='pending' AND share_id IN "
                            "(SELECT share_id FROM proactive_share WHERE state IN ('failed','unknown','expired'))")
            if self.db.execute("SELECT 1 FROM outbox WHERE destination=? AND state NOT IN ('sent','cancelled') LIMIT 1", (destination,)).fetchone():
                self.db.commit()
                return None
            row = self.db.execute("SELECT l.share_id,l.url FROM proactive_link l "
                                  "JOIN proactive_share s ON s.share_id=l.share_id "
                                  "WHERE l.state='pending' AND s.state='sent' AND l.account=? AND l.owner=? "
                                  "AND s.account=l.account AND s.owner=l.owner ORDER BY l.rowid LIMIT 1", (account, owner)).fetchone()
            if not row:
                self.db.commit()
                return None
            address = self.db.execute("SELECT context_token FROM inbox WHERE channel=? AND account=? AND sender=? "
                                      "AND runtime_id IS NOT NULL AND context_token IS NOT NULL AND context_token!='' "
                                      "ORDER BY rowid DESC LIMIT 1", (channel, account, owner)).fetchone()
            if not address:
                self.db.commit()
                return None
            self.db.execute("UPDATE proactive_link SET state='sending' WHERE share_id=? AND state='pending'", (row[0],))
            self.db.commit()
            return row[0], row[1], address[0]
        except BaseException:
            self.db.rollback()
            raise

    def finish_proactive_link(self, share_id, state, outcome):
        if state not in ('sent', 'failed', 'unknown'):
            raise ValueError('invalid proactive link status')
        with self.db:
            self.db.execute("UPDATE proactive_link SET state=?,outcome=? WHERE share_id=? AND state='sending'",
                            (state, outcome, share_id))

    def claim_proactive_share(self, channel, account, owner, destination):
        """Claim only a fresh share and the latest context for the bound owner."""
        self.db.execute('BEGIN IMMEDIATE')
        try:
            if self.db.execute("SELECT 1 FROM outbox WHERE destination=? AND state NOT IN ('sent','cancelled') LIMIT 1", (destination,)).fetchone():
                self.db.commit()
                return None
            self.db.execute("UPDATE proactive_share SET state='expired',outcome='expired' "
                            "WHERE state='pending' AND created_at<?", (time.time() - 21600,))
            row = self.db.execute("SELECT share_id,text FROM proactive_share WHERE state='pending' AND account=? AND owner=? ORDER BY rowid LIMIT 1", (account, owner)).fetchone()
            if not row:
                self.db.commit()
                return None
            address = self.db.execute("SELECT context_token FROM inbox WHERE channel=? AND account=? AND sender=? "
                                      "AND runtime_id IS NOT NULL AND context_token IS NOT NULL AND context_token!='' "
                                      "ORDER BY rowid DESC LIMIT 1", (channel, account, owner)).fetchone()
            if not address:
                self.db.commit()
                return None
            self.db.execute("UPDATE proactive_share SET state='sending' WHERE share_id=? AND state='pending'", (row[0],))
            self.db.commit()
            return row[0], row[1], address[0]
        except BaseException:
            self.db.rollback()
            raise

    def finish_proactive_share(self, share_id, state, outcome):
        if state not in ('sent', 'failed', 'unknown'):
            raise ValueError('invalid proactive share status')
        with self.db:
            self.db.execute("UPDATE proactive_share SET state=?,outcome=? WHERE share_id=? AND state='sending'",
                            (state, outcome, share_id))

    def accepted(self, channel, account, message_id, runtime_id):
        if not runtime_id:
            raise ValueError('missing runtime identity')
        with self.db:
            self.db.execute('UPDATE inbox SET runtime_id=? WHERE channel=? AND account=? AND message_id=? AND runtime_id IS NULL',
                            (runtime_id, channel, account, str(message_id)))

    def bootstrap_sync(self, destination, epoch, seq, *, runtime_id=None):
        """Start at the current snapshot boundary without delivering history."""
        if not epoch or not isinstance(seq, int) or isinstance(seq, bool) or seq < 0:
            raise ValueError('invalid Runtime cursor')
        with self.db:
            position = [runtime_id, epoch, seq] if runtime_id else [epoch, seq]
            self.db.execute('INSERT INTO cursor VALUES(?,?)',
                            ('sync:' + destination, json.dumps(position)))

    def apply_sync(self, destination, epoch, seq, messages, reset=False, reply_turns=None, runtime_id=None):
        """Queue only explicitly associated channel turns and advance the cursor."""
        if not epoch or not isinstance(seq, int) or isinstance(seq, bool) or seq < 0:
            raise ValueError('invalid Runtime cursor')
        with self.db:
            previous = self.cursor('sync:' + destination)
            if not previous or reset:
                raise ValueError('sync requires bootstrap or gap reconciliation')
            position = json.loads(previous)
            if runtime_id:
                if len(position) != 3 or position[0] != runtime_id:
                    raise ValueError('sync Runtime changed; reconciliation required')
                _, old_epoch, old_seq = position
            else:
                if len(position) != 2:
                    raise ValueError('sync Runtime identity required')
                old_epoch, old_seq = position
            if epoch != old_epoch or seq < old_seq:
                raise ValueError('sync cursor changed; reconciliation required')
            if not isinstance(reply_turns, dict) or any(not isinstance(key, str) or not key
                    or not isinstance(value, str) or not value for key, value in reply_turns.items()):
                raise ValueError('channel turn mapping required')
            for message in messages:
                turn = (message.get('presentation') or {}).get('turn_id')
                if turn not in reply_turns:
                    continue
                request_id = reply_turns[turn]
                direct_reply = (isinstance(runtime_id, str) and turn == runtime_id + '/' + request_id
                                and message.get('kind') == 'reply')
                for block_id, text in public_blocks(message):
                    old = self.db.execute('SELECT text,request_id FROM outbox WHERE destination=? AND block_id=?',
                                          (destination, block_id)).fetchone()
                    if old and (old[0] != text or (old[1] and old[1] != request_id)):
                        raise ValueError('published block changed; reconciliation required')
                    self.db.execute('INSERT OR IGNORE INTO outbox(destination,block_id,text,request_id,interruptible) VALUES(?,?,?,?,?)',
                                    (destination, block_id, text, request_id, int(direct_reply)))
                    self.db.execute('UPDATE outbox SET request_id=? WHERE destination=? AND block_id=? AND request_id IS NULL',
                                    (request_id, destination, block_id))
                    if direct_reply:
                        self.db.execute('UPDATE outbox SET interruptible=1 WHERE destination=? AND block_id=? AND kind=\'text\'',
                                        (destination, block_id))
                created_at = message.get('created_at')
                try:
                    activation = float(self.cursor('media:activation'))
                except (TypeError, ValueError):
                    raise ValueError('channel media activation boundary unavailable') from None
                if not math.isfinite(activation):
                    raise ValueError('channel media activation boundary unavailable')
                # Fail closed for unknown timestamps. An old task can be
                # explicitly requested again; its old update is not a send.
                eligible = type(created_at) in (int, float) and math.isfinite(created_at) and created_at >= activation
                for artifact_id in public_attachments(message) if eligible else []:
                    message_key = (message.get('presentation') or {}).get('id')
                    if not isinstance(message_key, str) or not message_key:
                        raise ValueError('invalid channel attachment message identity')
                    block_id = message_key + '#media:' + artifact_id
                    existing = self.db.execute("SELECT block_id FROM outbox WHERE destination=? AND request_id=? AND kind='media' AND text=?",
                                               (destination, request_id, artifact_id)).fetchone()
                    if not existing:
                        self.db.execute("INSERT OR IGNORE INTO outbox(destination,block_id,text,request_id,kind) VALUES(?,?,?,?,'media')",
                                        (destination, block_id, artifact_id, request_id))
            position = [runtime_id, epoch, seq] if runtime_id else [epoch, seq]
            self.db.execute('INSERT OR REPLACE INTO cursor VALUES(?,?)',
                            ('sync:' + destination, json.dumps(position)))

    def next_outgoing(self, destination):
        # An uncertain earlier delivery blocks later sends to preserve ordering.
        row = self.db.execute("SELECT block_id,text,state FROM outbox WHERE destination=? AND state NOT IN ('sent','cancelled') ORDER BY rowid LIMIT 1", (destination,)).fetchone()
        return (row[0], row[1]) if row and row[2] == 'pending' else None

    def outgoing_request(self, destination, block_id):
        row = self.db.execute('SELECT request_id FROM outbox WHERE destination=? AND block_id=?',
                              (destination, block_id)).fetchone()
        return row[0] if row else None

    def outgoing_kind(self, destination, block_id):
        row = self.db.execute('SELECT kind FROM outbox WHERE destination=? AND block_id=?',
                              (destination, block_id)).fetchone()
        return row[0] if row else None

    def _newer_owner_input(self, request_id, channel, account, sender):
        origin = self.db.execute('SELECT rowid FROM inbox WHERE channel=? AND account=? AND sender=? AND runtime_id=?',
                                 (channel, account, sender, request_id)).fetchone()
        return bool(origin and self.db.execute('SELECT 1 FROM inbox WHERE rowid>? AND channel=? AND account=? AND sender=? LIMIT 1',
                                               (origin[0], channel, account, sender)).fetchone())

    def send_delay(self, destination, request_id, block_id=None, *, channel=None, account=None, sender=None):
        row_block = self.db.execute('SELECT interruptible,text,kind FROM outbox WHERE destination=? AND block_id=?',
                                    (destination, block_id)).fetchone() if block_id else None
        if row_block and row_block[0] and channel and account and sender:
            if self._newer_owner_input(request_id, channel, account, sender):
                return 0.0
            received = self.db.execute('SELECT received_at FROM inbox WHERE channel=? AND account=? AND sender=? AND runtime_id=?',
                                       (channel, account, sender, request_id)).fetchone()
            if received and received[0] is not None:
                remaining = received[0] + INPUT_PAUSE - time.time()
                if 0 < remaining <= INPUT_PAUSE:
                    return remaining
        row = self.db.execute('SELECT request_id,next_at,last_sent_at FROM delivery_pacing WHERE destination=?',
                              (destination,)).fetchone()
        if not row or not request_id or row[0] != request_id:
            return 0.0
        # Wake immediately to cancel a superseded direct reply; never use this
        # shortcut for files or background task results.
        if (row_block and row_block[0] and channel and account and sender
                and self._newer_owner_input(request_id, channel, account, sender)):
            return 0.0
        # A backward wall-clock correction cannot delay a message for hours.
        now = time.time()
        # The reader has only seen the preceding bubble. Its saved deadline is
        # set at delivery, so a long result does not delay itself after an ack.
        deadline = row[1]
        remaining = deadline - now
        if remaining > 12.0:
            with self.db:
                self.db.execute('UPDATE delivery_pacing SET next_at=?,last_sent_at=NULL WHERE destination=? AND request_id=? AND next_at=?',
                                (now + 12.0, destination, request_id, row[1]))
            return 12.0
        return max(0.0, remaining)

    def begin_send(self, destination, block_id, *, channel, account, sender):
        """Atomically cancel superseded direct text or claim it for network send."""
        self.db.execute('BEGIN IMMEDIATE')
        try:
            row = self.db.execute('SELECT state,request_id,interruptible FROM outbox WHERE destination=? AND block_id=?',
                                  (destination, block_id)).fetchone()
            if not row or row[0] != 'pending':
                self.db.commit()
                return 'idle'
            superseded = bool(row[2] and row[1] and self._newer_owner_input(row[1], channel, account, sender))
            state = 'cancelled' if superseded else 'sending'
            self.db.execute('UPDATE outbox SET state=?,delivery_revision=delivery_revision+1 '
                            'WHERE destination=? AND block_id=? AND state=\'pending\'',
                            (state, destination, block_id))
            self.db.commit()
            return state
        except BaseException:
            self.db.rollback()
            raise

    def remember_task(self, destination, task_id, run_id, request_id):
        if not all(isinstance(value, str) and value for value in (destination, task_id, run_id, request_id)):
            raise ValueError('invalid task delivery identity')
        with self.db:
            row = self.db.execute('SELECT request_id FROM task_delivery WHERE destination=? AND task_id=? AND run_id=?',
                                  (destination, task_id, run_id)).fetchone()
            if row and row[0] != request_id:
                raise ValueError('conflicting task delivery identity')
            self.db.execute('INSERT OR IGNORE INTO task_delivery VALUES(?,?,?,?)',
                            (destination, task_id, run_id, request_id))

    def task_request(self, destination, task_id, run_id):
        row = self.db.execute('SELECT request_id FROM task_delivery WHERE destination=? AND task_id=? AND run_id=?',
                              (destination, task_id, run_id)).fetchone()
        return row[0] if row else None

    def mark(self, destination, block_id, old, new, *, outcome=None, server_message_id=None):
        # 'sending' is durable before touching the network. After a crash it is
        # uncertain, not a retryable pending send.
        allowed = {('pending', 'sending'), ('sending', 'sent'), ('sending', 'unknown'),
                   ('sending', 'failed'), ('pending', 'cancelled')}
        if (old, new) not in allowed:
            raise ValueError('invalid delivery transition')
        if outcome is not None and outcome not in ('api_accepted', 'api_accepted_no_ret',
                                                    'transport_uncertain', 'upstream_error',
                                                    'invalid_response', 'unexpected_error'):
            raise ValueError('invalid delivery outcome')
        if (server_message_id is not None and (new != 'sent' or isinstance(server_message_id, bool)
                or not isinstance(server_message_id, (str, int)) or not 0 < len(str(server_message_id)) <= 128)):
            raise ValueError('invalid server message identity')
        with self.db:
            now = time.time() if new == 'sent' else None
            changed = self.db.execute('UPDATE outbox SET state=?,delivery_revision=delivery_revision+1, '
                                      'last_outcome=COALESCE(?,last_outcome),sent_at=COALESCE(?,sent_at), '
                                      'server_message_id=COALESCE(?,server_message_id) '
                                      'WHERE destination=? AND block_id=? AND state=?',
                                      (new, outcome, now, str(server_message_id) if server_message_id is not None else None,
                                       destination, block_id, old)).rowcount
            if changed and new == 'sent':
                row = self.db.execute('SELECT request_id,text,kind FROM outbox WHERE destination=? AND block_id=?',
                                      (destination, block_id)).fetchone()
                if row and row[0]:
                    self.db.execute('INSERT OR REPLACE INTO delivery_pacing(destination,request_id,next_at,last_sent_at) VALUES(?,?,?,?)',
                                    (destination, row[0], now + reading_pause(row[1], row[2]), now))
        return changed == 1

    def retry(self, destination, block_id, confirm_unknown=False):
        """Retry a definite failure, or an uncertain send after explicit consent."""
        with self.db:
            row = self.db.execute('SELECT state FROM outbox WHERE destination=? AND block_id=?',
                                  (destination, block_id)).fetchone()
            if not row:
                return 'missing'
            state = row[0]
            if state == 'unknown' and not confirm_unknown:
                return 'confirm_required'
            if state not in ('failed', 'unknown'):
                return 'noop'
            self.db.execute("UPDATE outbox SET state='pending',delivery_revision=delivery_revision+1 "
                            "WHERE destination=? AND block_id=? AND state=?",
                            (destination, block_id, state))
            return 'queued'

    def retry_once(self, destination, block_id, operation, expected_revision, *, confirm_unknown=False):
        """Durably deduplicate a user retry, even after a later uncertain send."""
        if (not isinstance(operation, str) or not re.fullmatch(r'[a-f0-9]{32}', operation)
                or not isinstance(destination, str) or not destination
                or not isinstance(block_id, str) or not block_id
                or type(confirm_unknown) is not bool or type(expected_revision) is not int or expected_revision < 1):
            raise ValueError('invalid channel retry operation')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            previous = self.db.execute(
                'SELECT destination,block_id,confirmed,result,expected_revision FROM retry_command WHERE operation=?',
                (operation,)).fetchone()
            if previous:
                if previous[:3] != (destination, block_id, int(confirm_unknown)) or previous[4] != expected_revision:
                    raise ValueError('channel retry operation identity changed')
                self.db.commit()
                return previous[3]
            row = self.db.execute('SELECT state,delivery_revision FROM outbox WHERE destination=? AND block_id=?',
                                  (destination, block_id)).fetchone()
            if row is None:
                result = 'missing'
            elif row[1] != expected_revision:
                result = 'stale'
            elif row[0] == 'unknown' and not confirm_unknown:
                result = 'confirm_required'
            elif row[0] in ('unknown', 'failed'):
                self.db.execute("UPDATE outbox SET state='pending',delivery_revision=delivery_revision+1 "
                                "WHERE destination=? AND block_id=?", (destination, block_id))
                result = 'queued'
            else:
                result = 'noop'
            self.db.execute('INSERT INTO retry_command(operation,destination,block_id,confirmed,result,expected_revision) VALUES(?,?,?,?,?,?)',
                            (operation, destination, block_id, int(confirm_unknown), result, expected_revision))
            self.db.commit()
            return result
        except BaseException:
            self.db.rollback()
            raise

    def confirm_received(self, destination, block_id, *, confirmed=False):
        """Record the owner's explicit confirmation without sending again."""
        if confirmed is not True:
            raise ValueError('explicit receipt confirmation required')
        with self.db:
            changed = self.db.execute("UPDATE outbox SET state='sent',delivery_revision=delivery_revision+1 "
                                      "WHERE destination=? AND block_id=? AND state='unknown'",
                                      (destination, block_id)).rowcount
            if changed:
                self.db.execute('INSERT OR REPLACE INTO delivery_confirmation VALUES(?,?,?)',
                                (destination, block_id, time.time()))
            return changed == 1

    def recover_interrupted(self, destination):
        """After exclusive ownership is acquired, mark prior sends uncertain."""
        with self.db:
            return self.db.execute("UPDATE outbox SET state='unknown',delivery_revision=delivery_revision+1 "
                                   "WHERE destination=? AND state='sending'",
                                   (destination,)).rowcount

    def dirty_delivery(self, destination, limit=32):
        """Return bounded status projections without exposing message text or reply addresses."""
        return [(block, state, revision) for _, block, state, revision in
                self.dirty_delivery_page(destination, 0, limit)]

    def dirty_delivery_page(self, destination, after=0, limit=32):
        """Seek through dirty records without letting one bad record starve others."""
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('invalid delivery projection limit')
        if type(after) is not int or after < 0:
            raise ValueError('invalid delivery projection cursor')
        rows = self.db.execute('SELECT rowid,block_id,state,delivery_revision FROM outbox '
                               'WHERE destination=? AND rowid>? AND projected_revision < delivery_revision '
                               'ORDER BY rowid LIMIT ?', (destination, after, limit)).fetchall()
        return [(rowid, block, 'waiting' if state in ('pending', 'sending') else state, revision)
                for rowid, block, state, revision in rows]

    def projected_delivery(self, destination, block, revision):
        """Acknowledge only the exact version just published, never a later transition."""
        if type(revision) is not int or revision < 1:
            raise ValueError('invalid delivery projection revision')
        with self.db:
            return self.db.execute('UPDATE outbox SET projected_revision=? '
                                   'WHERE destination=? AND block_id=? AND delivery_revision=? '
                                   'AND projected_revision < delivery_revision',
                                   (revision, destination, block, revision)).rowcount == 1

    def delivery_text(self, destination, block):
        row = self.db.execute('SELECT text FROM outbox WHERE destination=? AND block_id=?',
                              (destination, block)).fetchone()
        if row is None:
            raise ValueError('channel delivery block missing')
        return row[0]
