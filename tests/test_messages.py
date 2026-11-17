import unittest
from unittest.mock import Mock

from sdo_reborn.client import SDOClient, SDOError


class MessageTests(unittest.TestCase):
    def setUp(self):
        self.client = SDOClient()
        self.client._message_context = Mock(return_value=("page", 42))

    def test_self_conversation_appears_when_regular_list_is_empty(self):
        self.client._ajax = Mock(side_effect=[{"conversations": []}, {"id": 7, "name": ""}])
        self.assertEqual(self.client.conversations(), [{"id": 7, "name": "Сообщения себе", "unread": 0}])

    def test_send_requires_server_confirmation(self):
        self.client._ajax = Mock(return_value=None)
        with self.assertRaises(SDOError):
            self.client.send_message(7, "Привет")
        self.client._ajax.assert_called_once_with(
            "page", "core_message_send_messages_to_conversation",
            {"conversationid": 7, "messages": [{"text": "Привет", "textformat": 0}]},
        )

    def test_profile_uses_logged_in_users_name_and_picture(self):
        profile = self.client.profile('<div class="usermenu"><span class="usertext">Иван Иванов</span><img class="userpicture" src="/avatar.png"></div>')
        self.assertEqual(profile, {"name": "Иван Иванов", "avatar": "https://sdo.i-spo.ru/avatar.png"})
