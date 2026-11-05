import unittest

from xiaoyu_runtime.channels.normalize import text_batch


class NormalizeTest(unittest.TestCase):
    def message(self, **values):
        result = {'message_type': 1, 'from_user_id': 'owner', 'message_id': 123,
                  'context_token': 'secret-context',
                  'item_list': [{'type': 1, 'text_item': {'text': '你好'}}]}
        result.update(values)
        return result

    def test_only_bound_owner_direct_messages_are_accepted(self):
        data = {'msgs': [self.message(from_user_id='stranger'), self.message(message_type=2),
                         self.message(group_id='group'), self.message()], 'get_updates_buf': 'next'}
        messages, cursor = text_batch(data, 'owner')
        self.assertEqual(cursor, 'next')
        self.assertEqual(messages, [{'id': '123', 'sender': 'owner', 'payload': '你好',
                                     'context_token': 'secret-context'}])

    def test_owner_unsupported_or_missing_context_is_not_silently_dropped(self):
        for message in (self.message(item_list=[{'type': 2}]), self.message(context_token=''),
                        self.message(message_id=None), self.message(message_type=None), self.message(from_user_id=None)):
            with self.assertRaises(ValueError) as error:
                text_batch({'msgs': [message], 'get_updates_buf': 'next'}, 'owner')
            self.assertNotIn('secret-context', str(error.exception))

    def test_owner_image_and_file_are_preserved_with_private_media_metadata(self):
        items = [{'type': 2, 'image_item': {'media': {'encrypt_query_param': 'private'}}},
                 {'type': 4, 'file_item': {'media': {'encrypt_query_param': 'private-file'}}}]
        messages, cursor = text_batch({'msgs': [self.message(item_list=items)], 'get_updates_buf': 'next'}, 'owner')
        self.assertEqual(cursor, 'next')
        self.assertEqual(messages[0]['media'], items)
        self.assertEqual(messages[0]['payload'], '')

    def test_quote_text_summary_missing_and_partial_are_preserved(self):
        for reference, kind in [
            ({'message_item': {'text_item': {'text': '原文'}}, 'svr_id': 42,
              'partial_text': {'start': 0}}, 'text'),
            ({'title': '摘要'}, 'summary'), ({'svr_id': 42}, 'unavailable'),
            ({'message_item': {'type': 2, 'image_item': {'secret': 'key'}}}, 'media')]:
            item = {'type': 1, 'text_item': {'text': '这个'}, 'ref_msg': reference}
            messages, _ = text_batch({'msgs': [self.message(item_list=[item])]}, 'owner')
            quote = messages[0]['quotes'][0]
            self.assertEqual(quote['kind'], kind)
            self.assertNotIn('secret', str(quote))
        self.assertEqual(messages[0]['payload'], '这个')
