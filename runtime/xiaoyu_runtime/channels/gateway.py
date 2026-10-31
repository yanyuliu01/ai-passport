"""Main-chat ingress for an explicitly bound external account.

The caller is responsible for obtaining and protecting the binding. Nothing in
this module starts a listener or binds a user automatically.
"""
from .store import client_id


class Gateway:
    def __init__(self, store, service, channel, account, owner):
        if not all(isinstance(value, str) and value for value in (channel, account, owner)):
            raise ValueError('explicit channel, account and owner binding required')
        self.store = store
        self.service = service
        self.channel = channel
        self.account = account
        self.owner = owner

    def ingest(self, messages, cursor):
        # Do not allow an adapter to route a different sender into the owner's
        # conversation, even if its own filtering was wrong.
        for message in messages:
            if message.get('sender') != self.owner:
                raise ValueError('sender does not match binding')
            if not isinstance(message.get('payload'), str) or not message['payload'].strip():
                raise ValueError('only nonempty text is supported')
        self.store.ingest(self.channel, self.account, messages, cursor)

    def submit_pending(self):
        submitted = 0
        for channel, account, mid, sender, text in self.store.pending_inbox():
            if (channel, account, sender) != (self.channel, self.account, self.owner):
                continue
            accepted = self.service.submit(text, conversation='default',
                                           client_id=client_id(channel, account, mid))
            # If a crash happens before this write, Runtime deduplicates the
            # identical retry by client_id and returns the original identity.
            self.store.accepted(channel, account, mid, accepted['id'])
            submitted += 1
        return submitted
