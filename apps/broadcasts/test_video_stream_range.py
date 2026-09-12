import os
import tempfile
import uuid
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.broadcasts import views as broadcasts_views
from apps.broadcasts.models import BroadcastVideo

User = get_user_model()

# Regression coverage for a real production bug: BroadcastVideoStreamView's
# range-request branch used to do `chunk = fh.read(length)` - a single
# blocking read of the ENTIRE requested range into memory before returning
# anything, non-streaming. For an open-ended `Range: bytes=0-` request (what
# a player commonly sends first, before it knows the file size), that reads
# the whole file. A long video reported this as "the video looks cut short
# after about a minute" - consistent with a reverse-proxy or WSGI worker
# read/write timeout killing the connection mid-buffer, which the player
# reads as end-of-stream rather than an error. Fixed to stream fixed-size
# chunks via StreamingHttpResponse instead.


@override_settings(
    MEDIA_ROOT=tempfile.mkdtemp(),
    # This view's range-streaming code path (the one under test) only runs
    # for local FileSystemStorage - dev/test environments with
    # OBJECT_STORAGE_PROVIDER=s3 set (this one included) otherwise get the
    # S3-redirect branch instead, which would 404 here since the test file
    # below is written to the local filesystem, not actually uploaded to S3.
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    },
)
class BroadcastVideoStreamRangeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            phone="5557000099",
            username="streamer",
            password="pw12345!",
            country="NG",
        )
        # Deliberately larger than a couple of internal chunk sizes (forced
        # small below) so a real multi-chunk read path is exercised, not
        # just a single-chunk happy path that would pass even with a bug
        # that dropped bytes after the first chunk.
        self.content = bytes((i % 256 for i in range(50_000)))
        self.storage_path = f"broadcast_videos/{uuid.uuid4()}.mp4"
        full_path = os.path.join(broadcasts_views.settings.MEDIA_ROOT, self.storage_path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "wb") as fh:
            fh.write(self.content)

        self.video = BroadcastVideo.objects.create(
            title="Long sermon",
            creator=self.user,
            video_url="http://testserver/media/" + self.storage_path,
            mime_type="video/mp4",
            storage_path=self.storage_path,
            duration_seconds=3600,
            # Range-request streaming is what this suite covers, not the
            # moderation gate (apps.broadcasts.moderation_gate) - pre-passed
            # so BroadcastVideoStreamView's eligibility check doesn't 404
            # every request here before the code under test ever runs.
            moderation_status=BroadcastVideo.ModerationStatus.PASSED,
            moderation_passed_at=timezone.now(),
            moderation_expires_at=timezone.now() + timedelta(days=90),
        )
        self.url = f"/api/v1/broadcasts/videos/{self.video.id}/stream/"

        # Force multiple internal read/yield cycles per request with a
        # small chunk size, instead of relying on the real 256KB default
        # (which this 50KB test file wouldn't even exceed) to prove the
        # generator's loop boundary doesn't drop or duplicate bytes.
        self._original_chunk_size = broadcasts_views._VIDEO_STREAM_CHUNK_SIZE
        broadcasts_views._VIDEO_STREAM_CHUNK_SIZE = 4096

    def tearDown(self):
        broadcasts_views._VIDEO_STREAM_CHUNK_SIZE = self._original_chunk_size

    def test_no_range_header_streams_full_file(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        body = b"".join(response.streaming_content) if response.streaming else response.content
        self.assertEqual(body, self.content)
        self.assertEqual(response["Content-Length"], str(len(self.content)))

    def test_open_ended_range_request_returns_the_entire_file_uncut(self):
        # This is the exact request shape (Range: bytes=0-) that triggered
        # the bug - an open-ended range asking for everything from byte 0.
        response = self.client.get(self.url, HTTP_RANGE="bytes=0-")
        self.assertEqual(response.status_code, 206)
        body = b"".join(response.streaming_content)
        self.assertEqual(len(body), len(self.content))
        self.assertEqual(body, self.content)
        self.assertEqual(response["Content-Length"], str(len(self.content)))
        self.assertEqual(response["Content-Range"], f"bytes 0-{len(self.content) - 1}/{len(self.content)}")

    def test_bounded_range_request_returns_exact_byte_slice(self):
        response = self.client.get(self.url, HTTP_RANGE="bytes=10000-19999")
        self.assertEqual(response.status_code, 206)
        body = b"".join(response.streaming_content)
        self.assertEqual(body, self.content[10000:20000])
        self.assertEqual(response["Content-Length"], "10000")
        self.assertEqual(response["Content-Range"], f"bytes 10000-19999/{len(self.content)}")

    def test_range_request_past_end_is_clamped_not_truncated_early(self):
        # A client asking for more than exists (common: "give me the last
        # megabyte" against a smaller file) should get everything available,
        # not an error and not silently fewer bytes than the clamped range
        # promises in its own Content-Range header.
        far_end = len(self.content) + 5000
        response = self.client.get(self.url, HTTP_RANGE=f"bytes=0-{far_end}")
        self.assertEqual(response.status_code, 206)
        body = b"".join(response.streaming_content)
        self.assertEqual(body, self.content)
        self.assertEqual(response["Content-Range"], f"bytes 0-{len(self.content) - 1}/{len(self.content)}")
