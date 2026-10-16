# apps/chat/test_sticker_library.py
#
# Sticker library (account-wide, reusable-across-every-conversation custom
# stickers): covers creation, listing (created/saved sections), save
# dedup, library-entry removal scoped to the caller only, and the
# ownership check on create. Lives alongside apps.media's own MediaAsset
# tests rather than duplicating them — this file only exercises the
# apps.chat layer (Sticker/StickerLibraryEntry + the four views), using a
# plain MediaAsset row as the already-uploaded/confirmed image a sticker
# is created from.

from django.contrib.auth import get_user_model
from rest_framework.test import APIClient, APITestCase

from apps.media.models import MediaAsset

from .models import Sticker, StickerLibraryEntry


class StickerLibraryTests(APITestCase):
    def setUp(self):
        User = get_user_model()
        self.alice = User.objects.create_user(phone="+237670004001", password="TestPass123!", country="CM")
        self.bob = User.objects.create_user(phone="+237670004002", password="TestPass123!", country="CM")

        self.alice_asset = MediaAsset.objects.create(
            owner=self.alice, type="image", bucket_key="uploads/alice/sticker.png",
            status="ready", mime_type="image/png",
        )
        self.bob_asset = MediaAsset.objects.create(
            owner=self.bob, type="image", bucket_key="uploads/bob/sticker.png",
            status="ready", mime_type="image/png",
        )

        self.client = APIClient()
        self.client.force_authenticate(self.alice)

    # --- create -----------------------------------------------------------

    def test_creating_a_sticker_from_your_own_asset_also_adds_it_to_your_own_library(self):
        res = self.client.post(
            "/api/v1/stickers/", {"assetId": str(self.alice_asset.id), "captionText": "Amen"}, format="json",
        )
        self.assertEqual(res.status_code, 201)
        self.assertTrue(Sticker.objects.filter(owner=self.alice, asset=self.alice_asset).exists())
        sticker = Sticker.objects.get(asset=self.alice_asset)
        self.assertTrue(StickerLibraryEntry.objects.filter(user=self.alice, sticker=sticker).exists())
        self.assertEqual(res.json()["sticker"]["caption_text"], "Amen")
        self.assertTrue(res.json()["is_mine"])

    def test_creating_a_sticker_stamps_the_asset_purpose_so_others_can_later_view_it(self):
        self.client.post("/api/v1/stickers/", {"assetId": str(self.alice_asset.id)}, format="json")
        self.alice_asset.refresh_from_db()
        self.assertEqual(self.alice_asset.purpose, "sticker")
        self.assertEqual(self.alice_asset.context, "sticker")

    def test_cannot_create_a_sticker_from_someone_elses_asset(self):
        res = self.client.post("/api/v1/stickers/", {"assetId": str(self.bob_asset.id)}, format="json")
        self.assertEqual(res.status_code, 403)
        self.assertFalse(Sticker.objects.filter(asset=self.bob_asset).exists())

    def test_create_requires_a_known_asset_id(self):
        res = self.client.post("/api/v1/stickers/", {"assetId": "00000000-0000-0000-0000-000000000000"}, format="json")
        self.assertEqual(res.status_code, 400)

    # --- saving -------------------------------------------------------------

    def test_a_user_can_save_a_sticker_created_by_someone_else(self):
        sticker = Sticker.objects.create(owner=self.bob, asset=self.bob_asset, caption_text="Bob's sticker")

        res = self.client.post(f"/api/v1/stickers/{sticker.id}/save/")

        self.assertEqual(res.status_code, 201)
        self.assertTrue(StickerLibraryEntry.objects.filter(user=self.alice, sticker=sticker).exists())
        self.assertFalse(res.json()["is_mine"])

    def test_saving_the_same_sticker_twice_does_not_duplicate(self):
        sticker = Sticker.objects.create(owner=self.bob, asset=self.bob_asset)
        self.client.post(f"/api/v1/stickers/{sticker.id}/save/")
        res = self.client.post(f"/api/v1/stickers/{sticker.id}/save/")

        self.assertEqual(res.status_code, 200)
        self.assertEqual(StickerLibraryEntry.objects.filter(user=self.alice, sticker=sticker).count(), 1)

    def test_saving_an_unknown_sticker_404s(self):
        res = self.client.post("/api/v1/stickers/00000000-0000-0000-0000-000000000000/save/")
        self.assertEqual(res.status_code, 404)

    def test_saving_does_not_transfer_ownership(self):
        sticker = Sticker.objects.create(owner=self.bob, asset=self.bob_asset)
        self.client.post(f"/api/v1/stickers/{sticker.id}/save/")
        sticker.refresh_from_db()
        self.assertEqual(sticker.owner_id, self.bob.id)

    # --- listing -------------------------------------------------------------

    def test_library_list_separates_created_from_saved(self):
        created = Sticker.objects.create(owner=self.alice, asset=self.alice_asset)
        StickerLibraryEntry.objects.create(user=self.alice, sticker=created)
        saved = Sticker.objects.create(owner=self.bob, asset=self.bob_asset)
        StickerLibraryEntry.objects.create(user=self.alice, sticker=saved)

        created_res = self.client.get("/api/v1/stickers/library/", {"section": "created"})
        saved_res = self.client.get("/api/v1/stickers/library/", {"section": "saved"})
        all_res = self.client.get("/api/v1/stickers/library/", {"section": "all"})

        self.assertEqual([r["sticker"]["id"] for r in created_res.json()["results"]], [str(created.id)])
        self.assertEqual([r["sticker"]["id"] for r in saved_res.json()["results"]], [str(saved.id)])
        self.assertEqual(len(all_res.json()["results"]), 2)

    def test_library_list_is_scoped_to_the_caller(self):
        bobs_sticker = Sticker.objects.create(owner=self.bob, asset=self.bob_asset)
        StickerLibraryEntry.objects.create(user=self.bob, sticker=bobs_sticker)

        res = self.client.get("/api/v1/stickers/library/")
        self.assertEqual(res.json()["results"], [])

    def test_library_list_excludes_removed_stickers(self):
        removed = Sticker.objects.create(owner=self.alice, asset=self.alice_asset, is_removed=True)
        StickerLibraryEntry.objects.create(user=self.alice, sticker=removed)

        res = self.client.get("/api/v1/stickers/library/")
        self.assertEqual(res.json()["results"], [])

    def test_library_list_pagination(self):
        for i in range(5):
            asset = MediaAsset.objects.create(
                owner=self.alice, type="image", bucket_key=f"uploads/alice/s{i}.png",
                status="ready", mime_type="image/png",
            )
            sticker = Sticker.objects.create(owner=self.alice, asset=asset)
            StickerLibraryEntry.objects.create(user=self.alice, sticker=sticker)

        res = self.client.get("/api/v1/stickers/library/", {"page": 1, "page_size": 2})
        body = res.json()
        self.assertEqual(len(body["results"]), 2)
        self.assertEqual(body["meta"]["total"], 5)
        self.assertEqual(body["meta"]["total_pages"], 3)

    # --- removal -------------------------------------------------------------

    def test_removing_your_own_library_entry_only_removes_your_entry(self):
        sticker = Sticker.objects.create(owner=self.bob, asset=self.bob_asset)
        bobs_entry = StickerLibraryEntry.objects.create(user=self.bob, sticker=sticker)
        alices_entry = StickerLibraryEntry.objects.create(user=self.alice, sticker=sticker)

        res = self.client.delete(f"/api/v1/stickers/library/{alices_entry.id}/")

        self.assertEqual(res.status_code, 204)
        self.assertFalse(StickerLibraryEntry.objects.filter(id=alices_entry.id).exists())
        self.assertTrue(StickerLibraryEntry.objects.filter(id=bobs_entry.id).exists())
        self.assertTrue(Sticker.objects.filter(id=sticker.id).exists())

    def test_removing_a_saved_copy_never_deletes_the_original_creators_sticker_or_entry(self):
        sticker = Sticker.objects.create(owner=self.bob, asset=self.bob_asset)
        bobs_entry = StickerLibraryEntry.objects.create(user=self.bob, sticker=sticker)
        alices_entry = StickerLibraryEntry.objects.create(user=self.alice, sticker=sticker)

        self.client.delete(f"/api/v1/stickers/library/{alices_entry.id}/")

        sticker.refresh_from_db()
        self.assertFalse(sticker.is_removed)
        self.assertTrue(StickerLibraryEntry.objects.filter(id=bobs_entry.id).exists())

    def test_cannot_remove_another_users_library_entry(self):
        sticker = Sticker.objects.create(owner=self.bob, asset=self.bob_asset)
        bobs_entry = StickerLibraryEntry.objects.create(user=self.bob, sticker=sticker)

        res = self.client.delete(f"/api/v1/stickers/library/{bobs_entry.id}/")

        self.assertEqual(res.status_code, 404)
        self.assertTrue(StickerLibraryEntry.objects.filter(id=bobs_entry.id).exists())

    # --- auth -------------------------------------------------------------

    def test_unauthenticated_requests_are_rejected(self):
        self.client.force_authenticate(None)
        res = self.client.get("/api/v1/stickers/library/")
        self.assertEqual(res.status_code, 401)
