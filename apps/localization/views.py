from pathlib import Path

from django.http import HttpResponse
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

# Language codes with a translation file in LOCALES_DIR. Kept as an explicit
# allow-list (rather than trusting the URL param) so the <str:code> path
# segment can never be used to read arbitrary files off disk.
KNOWN_LANGUAGE_CODES = {"es"}

LOCALES_DIR = Path(__file__).resolve().parent / "locales"


class LanguageFileView(APIView):
    """Serves a language's translation dictionary as-is from disk.

    Public/unauthenticated: translation strings aren't sensitive, and the
    app needs them available before a user is logged in (e.g. onboarding).
    """

    permission_classes = [AllowAny]

    def get(self, request, code: str):
        normalized = code.strip().lower()
        if normalized not in KNOWN_LANGUAGE_CODES:
            return Response({"detail": "Unknown language code."}, status=404)

        file_path = LOCALES_DIR / f"{normalized}.json"
        try:
            raw_bytes = file_path.read_bytes()
        except FileNotFoundError:
            return Response({"detail": "Language file not found."}, status=404)

        response = HttpResponse(raw_bytes, content_type="application/json")
        response["Cache-Control"] = "public, max-age=86400"
        return response
