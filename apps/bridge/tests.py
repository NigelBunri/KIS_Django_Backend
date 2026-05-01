from django.test import TestCase
from django.contrib.auth import get_user_model
from rest_framework.test import APIRequestFactory, force_authenticate
from .models import BridgeAccount, BridgeThread, BridgeMessage
from .views import BridgeAccountViewSet, BridgeThreadViewSet

class BridgeSmokeTest(TestCase):
    def test_create_message_and_thread(self):
        a = BridgeAccount.objects.create(user_id='00000000-0000-0000-0000-000000000000', external_app='TEST', external_user_id='ext-1', access_token='tok')
        t = BridgeThread.objects.create(external_app='TEST', external_thread_id='thread-1')
        m = BridgeMessage.objects.create(bridge_thread=t, direction='INBOUND', message_type='TEXT', payload={'text':'hi'})
        self.assertEqual(t.messages.count(), 1)


class BridgeAccessBoundaryTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user_a = User.objects.create_user(phone="+237670001001", password="TestPass123!", country="CM")
        self.user_b = User.objects.create_user(phone="+237670001002", password="TestPass123!", country="CM")
        self.factory = APIRequestFactory()

    def test_bridge_accounts_are_limited_to_request_user(self):
        own = BridgeAccount.objects.create(
            user_id=self.user_a.id,
            external_app="SLACK",
            external_user_id="own",
            access_token="own-token",
        )
        BridgeAccount.objects.create(
            user_id=self.user_b.id,
            external_app="SLACK",
            external_user_id="other",
            access_token="other-token",
        )

        request = self.factory.get("/bridge/accounts/")
        force_authenticate(request, user=self.user_a)
        response = BridgeAccountViewSet.as_view({"get": "list"})(request)

        self.assertEqual(response.status_code, 200)
        rows = response.data.get("results", response.data)
        self.assertEqual(len(rows), 1)
        self.assertEqual(str(rows[0]["id"]), str(own.id))
        self.assertNotIn("access_token", rows[0])
        self.assertNotIn("refresh_token", rows[0])

    def test_bridge_threads_require_owned_bridge_account_link(self):
        account = BridgeAccount.objects.create(
            user_id=self.user_a.id,
            external_app="SLACK",
            external_user_id="own",
            access_token="own-token",
        )
        own_thread = BridgeThread.objects.create(
            external_app="SLACK",
            external_thread_id="thread-own",
            linked_thread_id=account.id,
        )
        BridgeThread.objects.create(
            external_app="SLACK",
            external_thread_id="thread-unlinked",
        )

        request = self.factory.get("/bridge/threads/")
        force_authenticate(request, user=self.user_a)
        response = BridgeThreadViewSet.as_view({"get": "list"})(request)

        self.assertEqual(response.status_code, 200)
        rows = response.data.get("results", response.data)
        self.assertEqual(len(rows), 1)
        self.assertEqual(str(rows[0]["id"]), str(own_thread.id))
