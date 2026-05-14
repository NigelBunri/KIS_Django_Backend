from datetime import date
from decimal import Decimal
import secrets

from django.db import models
from django.db.models import Prefetch
from django.http import HttpResponse
from django.utils import timezone
from django.shortcuts import get_object_or_404
from rest_framework import generics, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.exceptions import PermissionDenied
from rest_framework.exceptions import ValidationError
from apps.notifications.realtime import notify_main_tab_badges_updated

from .models import (
    BibleTranslation,
    BibleBook,
    BibleChapter,
    BibleVerse,
    BibleAudio,
    BibleTranslationMetadata,
    BibleTranslationLicenseReviewStatus,
    DailyDevotional,
    BibleDailyPassage,
    BibleMeditationPost,
    BiblePrayerMonth,
    BiblePrayerDay,
    PrayerRequest,
    MeditationTopic,
    MeditationSchedule,
    MeditationEntry,
    ReadingPlan,
    ReadingPlanEnrollment,
    ReadingHistory,
    BibleReadingPlanEvent,
    BibleBookmark,
    BibleNote,
    BibleHighlight,
    MemoryVerse,
    BiblePreference,
    BibleCrossReference,
    BibleContentAuditLog,
    BibleCourse,
    BibleCourseModule,
    BibleLesson,
    BibleCourseEnrollment,
    BibleLessonProgress,
    BibleLessonReaction,
    BibleLessonComment,
    BibleCourseReaction,
    BibleCourseComment,
    BibleCourseShare,
    BibleCourseTrack,
    BibleCourseTrackItem,
    BibleCoursePrerequisite,
    BibleQuiz,
    BibleQuizQuestion,
    BibleQuizChoice,
    BibleQuizAttempt,
    BibleAssignment,
    BibleAssignmentSubmission,
    BiblePeerReview,
    BibleCourseForum,
    BibleForumThread,
    BibleForumPost,
    BibleMentorAssignment,
    BibleLiveSession,
    BibleLiveAttendance,
    BibleLiveRecording,
    BibleCourseBundle,
    BibleCourseBundleItem,
    BibleCourseCoupon,
    BibleEnterpriseSeatPool,
    BibleRefundRequest,
    BibleCourseCredential,
)
from .serializers import (
    BibleTranslationSerializer,
    BibleTranslationMetadataSerializer,
    BibleBookSerializer,
    BibleChapterSerializer,
    BibleVerseSerializer,
    BibleAudioSerializer,
    DailyDevotionalSerializer,
    BibleDailyPassageSerializer,
    BibleMeditationPostSerializer,
    BiblePrayerMonthSerializer,
    BiblePrayerDaySerializer,
    PrayerRequestSerializer,
    MeditationTopicSerializer,
    MeditationScheduleSerializer,
    MeditationEntrySerializer,
    ReadingPlanSerializer,
    ReadingPlanEnrollmentSerializer,
    ReadingHistorySerializer,
    BibleReadingPlanEventSerializer,
    BibleBookmarkSerializer,
    BibleNoteSerializer,
    BibleHighlightSerializer,
    MemoryVerseSerializer,
    BiblePreferenceSerializer,
    BibleCrossReferenceSerializer,
    BibleContentAuditLogSerializer,
    BibleCourseSerializer,
    BibleCourseModuleSerializer,
    BibleLessonSerializer,
    BibleCourseEnrollmentSerializer,
    BibleLessonProgressSerializer,
    BibleLessonReactionSerializer,
    BibleLessonCommentSerializer,
    BibleCourseReactionSerializer,
    BibleCourseCommentSerializer,
    BibleCourseShareSerializer,
    BibleCourseTrackSerializer,
    BibleCourseTrackItemSerializer,
    BibleCoursePrerequisiteSerializer,
    BibleQuizSerializer,
    BibleQuizQuestionSerializer,
    BibleQuizChoiceSerializer,
    BibleQuizAttemptSerializer,
    BibleAssignmentSerializer,
    BibleAssignmentSubmissionSerializer,
    BiblePeerReviewSerializer,
    BibleCourseForumSerializer,
    BibleForumThreadSerializer,
    BibleForumPostSerializer,
    BibleMentorAssignmentSerializer,
    BibleLiveSessionSerializer,
    BibleLiveAttendanceSerializer,
    BibleLiveRecordingSerializer,
    BibleCourseBundleSerializer,
    BibleCourseBundleItemSerializer,
    BibleCourseCouponSerializer,
    BibleEnterpriseSeatPoolSerializer,
    BibleRefundRequestSerializer,
    BibleCourseCredentialSerializer,
)
from .certificates import build_certificate_pdf, ensure_certificate_file, build_certificate_url
from .importers import scan_bible_translation_registry
from .reader import PassageReferenceError, parse_passage_reference
from apps.partners.models import Partner, PartnerMembership, PartnerMembershipStatus, PartnerOrganizationProfile
from apps.partners.seed import KCAN_PARTNER_NAME, KCAN_PARTNER_SLUG, LEGACY_DEFAULT_PARTNER_SLUGS
from apps.chat.models import ConversationMember, BaseConversationRole
from apps.billing.services import record_ledger, get_credit_account


class TranslationListView(generics.ListAPIView):
    permission_classes = [AllowAny]
    serializer_class = BibleTranslationSerializer
    pagination_class = None

    def get_queryset(self):
        return public_translation_queryset()


def public_translation_queryset():
    return (
        BibleTranslation.objects.filter(
            is_active=True,
            metadata__is_public=True,
            metadata__is_licensed=True,
            metadata__validation_status__in=["valid", "warning"],
        )
        .select_related("metadata")
        .order_by("sort_order")
    )


def get_public_translation(identifier=None):
    qs = public_translation_queryset()
    if identifier:
        lookup = models.Q(code__iexact=identifier)
        if str(identifier).isdigit():
            lookup |= models.Q(id=int(identifier))
        translation = qs.filter(lookup).first()
        if not translation:
            raise ValidationError({"translation": "Translation is not available for public reading."})
        return translation
    translation = qs.first()
    if not translation:
        raise ValidationError({"translation": "No public licensed Bible translation is available."})
    return translation


def _chapter_navigation(chapter: BibleChapter):
    previous_chapter = (
        BibleChapter.objects.filter(book=chapter.book, number__lt=chapter.number).order_by("-number").first()
    )
    if not previous_chapter:
        previous_book = BibleBook.objects.filter(order__lt=chapter.book.order).order_by("-order").first()
        if previous_book:
            previous_chapter = BibleChapter.objects.filter(book=previous_book).order_by("-number").first()
    next_chapter = BibleChapter.objects.filter(book=chapter.book, number__gt=chapter.number).order_by("number").first()
    if not next_chapter:
        next_book = BibleBook.objects.filter(order__gt=chapter.book.order).order_by("order").first()
        if next_book:
            next_chapter = BibleChapter.objects.filter(book=next_book).order_by("number").first()
    return {
        "previous": BibleChapterSerializer(previous_chapter).data if previous_chapter else None,
        "next": BibleChapterSerializer(next_chapter).data if next_chapter else None,
    }


def _verses_for_passage(translation: BibleTranslation, chapter: BibleChapter, start_verse=None, end_verse=None):
    qs = BibleVerse.objects.filter(translation=translation, chapter=chapter).select_related("chapter", "chapter__book")
    if start_verse:
        qs = qs.filter(number__gte=start_verse)
    if end_verse:
        qs = qs.filter(number__lte=end_verse)
    return qs.order_by("number")


def _build_reader_payload(translation: BibleTranslation, chapter: BibleChapter, verses, reference: str):
    audio = (
        BibleAudio.objects.filter(translation=translation, chapter=chapter)
        .prefetch_related("segments")
        .first()
    )
    return {
        "translation": BibleTranslationSerializer(translation).data,
        "book": BibleBookSerializer(chapter.book).data,
        "chapter": BibleChapterSerializer(chapter).data,
        "reference": reference,
        "navigation": _chapter_navigation(chapter),
        "verses": BibleVerseSerializer(verses, many=True).data,
        "audio": BibleAudioSerializer(audio).data if audio else None,
    }


class BibleTranslationMetadataViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = BibleTranslationMetadataSerializer

    def get_queryset(self):
        qs = BibleTranslationMetadata.objects.select_related("translation").all()
        if not can_manage_kcan_content(self.request.user):
            qs = qs.filter(is_public=True, is_licensed=True, validation_status__in=["valid", "warning"])
        language = self.request.query_params.get("language")
        status_filter = self.request.query_params.get("validation_status")
        copyright_status = self.request.query_params.get("copyright_status")
        public = self.request.query_params.get("public")
        if language:
            qs = qs.filter(language__iexact=language)
        if status_filter:
            qs = qs.filter(validation_status=status_filter)
        if copyright_status:
            qs = qs.filter(copyright_status=copyright_status)
        if public is not None:
            qs = qs.filter(is_public=str(public).lower() in {"1", "true", "yes"})
        return qs.order_by("language", "full_name")

    def _require_admin(self):
        require_manage_kcan_content(self.request.user)

    def perform_create(self, serializer):
        self._require_admin()
        serializer.save()

    def perform_update(self, serializer):
        self._require_admin()
        old_instance = self.get_object()
        requested_public = serializer.validated_data.get("is_public", old_instance.is_public)
        requested_licensed = serializer.validated_data.get("is_licensed", old_instance.is_licensed)
        requested_review = serializer.validated_data.get("license_review_status", old_instance.license_review_status)
        if requested_public and requested_licensed and requested_review == BibleTranslationLicenseReviewStatus.PENDING:
            raise ValidationError(
                {
                    "license_review_status": (
                        "Approve the human license review before making a non-public-domain translation public."
                    )
                }
            )
        if (
            requested_review != old_instance.license_review_status
            and requested_review
            in {
                BibleTranslationLicenseReviewStatus.APPROVED,
                BibleTranslationLicenseReviewStatus.REJECTED,
                BibleTranslationLicenseReviewStatus.NOT_REQUIRED,
            }
        ):
            serializer.validated_data["license_reviewed_by"] = self.request.user
            serializer.validated_data["license_reviewed_at"] = timezone.now()
        instance = serializer.save()
        if instance.translation_id:
            instance.translation.is_active = instance.can_be_public
            instance.translation.save(update_fields=["is_active"])

    def perform_destroy(self, instance):
        self._require_admin()
        instance.delete()

    @action(detail=False, methods=["post"], url_path="scan")
    def scan(self, request):
        self._require_admin()
        languages = request.data.get("languages")
        translations = request.data.get("translations")
        scanned = scan_bible_translation_registry(languages=languages, translations=translations)
        serializer = self.get_serializer(scanned, many=True)
        return Response({"count": len(scanned), "results": serializer.data}, status=status.HTTP_200_OK)


class BookListView(generics.ListAPIView):
    permission_classes = [AllowAny]
    serializer_class = BibleBookSerializer
    pagination_class = None

    def get_queryset(self):
        return BibleBook.objects.all().order_by("order")


class ChapterListView(generics.ListAPIView):
    permission_classes = [AllowAny]
    serializer_class = BibleChapterSerializer
    pagination_class = None

    def get_queryset(self):
        book_id = self.request.query_params.get("book")
        qs = BibleChapter.objects.select_related("book").all()
        if book_id:
            qs = qs.filter(book_id=book_id)
        return qs.order_by("number")


class ReaderView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        translation_code = request.query_params.get("translation")
        reference = request.query_params.get("reference")
        book_code = request.query_params.get("book")
        chapter_number = request.query_params.get("chapter")

        translation = get_public_translation(translation_code)
        start_verse = request.query_params.get("start_verse")
        end_verse = request.query_params.get("end_verse")

        if reference:
            try:
                parsed = parse_passage_reference(reference)
            except PassageReferenceError as exc:
                return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
            chapter = BibleChapter.objects.filter(book=parsed.book, number=parsed.chapter).first()
            if not chapter:
                return Response({"detail": "Chapter not found."}, status=status.HTTP_404_NOT_FOUND)
            verses = _verses_for_passage(translation, chapter, parsed.start_verse, parsed.end_verse)
            return Response(_build_reader_payload(translation, chapter, verses, parsed.display_ref))

        book = None
        if book_code:
            book = BibleBook.objects.filter(models.Q(code__iexact=book_code) | models.Q(name__iexact=book_code)).first()
        if not book:
            book = BibleBook.objects.order_by("order").first()
        if not book:
            return Response({"detail": "No books available"}, status=status.HTTP_404_NOT_FOUND)
        try:
            chapter_number = int(chapter_number or 1)
        except (TypeError, ValueError):
            return Response({"detail": "Chapter must be a number."}, status=status.HTTP_400_BAD_REQUEST)
        chapter = BibleChapter.objects.filter(book=book, number=chapter_number).first()
        if not chapter:
            chapter = BibleChapter.objects.filter(book=book).order_by("number").first()
        if not chapter:
            return Response({"detail": "Chapter not found."}, status=status.HTTP_404_NOT_FOUND)

        try:
            start_verse = int(start_verse) if start_verse else None
            end_verse = int(end_verse) if end_verse else start_verse
        except (TypeError, ValueError):
            return Response({"detail": "Verse range must be numeric."}, status=status.HTTP_400_BAD_REQUEST)
        verses = _verses_for_passage(translation, chapter, start_verse, end_verse)
        if start_verse:
            reference_label = f"{book.name} {chapter.number}:{start_verse}"
            if end_verse and end_verse != start_verse:
                reference_label = f"{reference_label}-{end_verse}"
        else:
            reference_label = f"{book.name} {chapter.number}"

        return Response(_build_reader_payload(translation, chapter, verses, reference_label))


class ParallelReaderView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        reference = request.query_params.get("reference")
        if not reference:
            return Response({"detail": "Reference is required."}, status=status.HTTP_400_BAD_REQUEST)
        translation_codes = [
            item.strip()
            for item in (request.query_params.get("translations") or "").split(",")
            if item.strip()
        ]
        if not translation_codes:
            default_translation = get_public_translation()
            translation_codes = [default_translation.code]
        try:
            parsed = parse_passage_reference(reference)
        except PassageReferenceError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        chapter = BibleChapter.objects.filter(book=parsed.book, number=parsed.chapter).first()
        if not chapter:
            return Response({"detail": "Chapter not found."}, status=status.HTTP_404_NOT_FOUND)

        payload = []
        for code in translation_codes:
            translation = get_public_translation(code)
            verses = _verses_for_passage(translation, chapter, parsed.start_verse, parsed.end_verse)
            payload.append(
                {
                    "translation": BibleTranslationSerializer(translation).data,
                    "verses": BibleVerseSerializer(verses, many=True).data,
                }
            )
        return Response(
            {
                "reference": parsed.display_ref,
                "book": BibleBookSerializer(parsed.book).data,
                "chapter": BibleChapterSerializer(chapter).data,
                "navigation": _chapter_navigation(chapter),
                "translations": payload,
            },
            status=status.HTTP_200_OK,
        )


class VerseSearchView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        query = request.query_params.get("q", "").strip()
        translation_id = request.query_params.get("translation")
        if not query:
            return Response({"results": []})
        public_translation_ids = public_translation_queryset().values_list("id", flat=True)
        qs = BibleVerse.objects.filter(translation_id__in=public_translation_ids).select_related("chapter", "chapter__book")
        if translation_id:
            translation = get_public_translation(translation_id)
            qs = qs.filter(translation=translation)
        qs = qs.filter(text__icontains=query)[:50]
        return Response({"results": BibleVerseSerializer(qs, many=True).data})


def get_kcan_partner():
    return Partner.objects.filter(slug__in=LEGACY_DEFAULT_PARTNER_SLUGS).first()


def can_manage_kcan_content(user) -> bool:
    partner = get_kcan_partner()
    return bool(partner and can_manage_partner_courses(user, partner))


def require_manage_kcan_content(user):
    partner = get_kcan_partner()
    if not partner:
        raise PermissionDenied("KCAN partner is not configured.")
    if not can_manage_partner_courses(user, partner):
        raise PermissionDenied("Only KCAN admins can manage official Bible content.")
    return partner


def _mark_published_if_needed(instance):
    if getattr(instance, "status", None) == "published" and not getattr(instance, "published_at", None):
        instance.published_at = timezone.now()
        instance.save(update_fields=["published_at"])


def _log_bible_content_action(partner, user, action: str, target):
    BibleContentAuditLog.objects.create(
        partner=partner,
        user=user if getattr(user, "is_authenticated", False) else None,
        action=action,
        target_type=target.__class__.__name__,
        target_id=str(target.pk),
        metadata={"status": getattr(target, "status", None)},
    )


class KCANPublishedContentMixin:
    permission_classes = [AllowAny]

    def _base_queryset(self):
        raise NotImplementedError

    def get_queryset(self):
        qs = self._base_queryset()
        if can_manage_kcan_content(self.request.user):
            return qs
        return qs.filter(status="published")

    def perform_create(self, serializer):
        partner = require_manage_kcan_content(self.request.user)
        instance = serializer.save(partner=partner, created_by=self.request.user)
        _mark_published_if_needed(instance)
        _log_bible_content_action(partner, self.request.user, "create", instance)

    def perform_update(self, serializer):
        partner = require_manage_kcan_content(self.request.user)
        instance = serializer.save()
        _mark_published_if_needed(instance)
        _log_bible_content_action(partner, self.request.user, "update", instance)

    def perform_destroy(self, instance):
        partner = require_manage_kcan_content(self.request.user)
        _log_bible_content_action(partner, self.request.user, "delete", instance)
        instance.delete()


class DailyDevotionalView(generics.ListAPIView):
    permission_classes = [AllowAny]
    serializer_class = DailyDevotionalSerializer

    def get_queryset(self):
        return DailyDevotional.objects.all().order_by("-date")


class BibleDailyPassageViewSet(KCANPublishedContentMixin, viewsets.ModelViewSet):
    serializer_class = BibleDailyPassageSerializer

    def _base_queryset(self):
        qs = BibleDailyPassage.objects.filter(partner__slug__in=LEGACY_DEFAULT_PARTNER_SLUGS).select_related(
            "partner", "translation", "created_by", "reviewed_by"
        )
        language = self.request.query_params.get("language")
        if language:
            qs = qs.filter(language__iexact=language)
        return qs.order_by("-date", "-created_at")

    @action(detail=False, methods=["get"], url_path="today")
    def today(self, request):
        language = request.query_params.get("language", "en")
        today = timezone.localdate()
        qs = self.get_queryset().filter(date=today, language__iexact=language)
        passage = qs.first()
        if not passage and language.lower() != "en":
            passage = self.get_queryset().filter(date=today, language__iexact="en").first()
        if not passage:
            return Response({"detail": "No daily passage published for today."}, status=status.HTTP_404_NOT_FOUND)
        return Response(self.get_serializer(passage).data, status=status.HTTP_200_OK)


class BibleMeditationPostViewSet(KCANPublishedContentMixin, viewsets.ModelViewSet):
    serializer_class = BibleMeditationPostSerializer

    def _base_queryset(self):
        qs = BibleMeditationPost.objects.filter(partner__slug__in=LEGACY_DEFAULT_PARTNER_SLUGS).select_related(
            "partner", "created_by", "reviewed_by"
        )
        language = self.request.query_params.get("language")
        content_type = self.request.query_params.get("content_type")
        if language:
            qs = qs.filter(language__iexact=language)
        if content_type:
            qs = qs.filter(content_type=content_type)
        return qs.order_by("-published_at", "-created_at")


class BiblePrayerMonthViewSet(KCANPublishedContentMixin, viewsets.ModelViewSet):
    serializer_class = BiblePrayerMonthSerializer

    def _base_queryset(self):
        qs = (
            BiblePrayerMonth.objects.filter(partner__slug__in=LEGACY_DEFAULT_PARTNER_SLUGS)
            .select_related("partner", "created_by", "reviewed_by")
            .prefetch_related("days")
        )
        year = self.request.query_params.get("year")
        month = self.request.query_params.get("month")
        language = self.request.query_params.get("language")
        if year:
            qs = qs.filter(year=year)
        if month:
            qs = qs.filter(month=month)
        if language:
            qs = qs.filter(language__iexact=language)
        return qs.order_by("-year", "-month")

    @action(detail=False, methods=["get"], url_path="current")
    def current(self, request):
        today = timezone.localdate()
        language = request.query_params.get("language", "en")
        month = self.get_queryset().filter(year=today.year, month=today.month, language__iexact=language).first()
        if not month and language.lower() != "en":
            month = self.get_queryset().filter(year=today.year, month=today.month, language__iexact="en").first()
        if not month:
            return Response({"detail": "No prayer calendar published for the current month."}, status=status.HTTP_404_NOT_FOUND)
        return Response(self.get_serializer(month).data, status=status.HTTP_200_OK)


class BiblePrayerDayViewSet(viewsets.ModelViewSet):
    permission_classes = [AllowAny]
    serializer_class = BiblePrayerDaySerializer

    def get_queryset(self):
        qs = BiblePrayerDay.objects.filter(prayer_month__partner__slug__in=LEGACY_DEFAULT_PARTNER_SLUGS).select_related(
            "prayer_month", "prayer_month__partner"
        )
        prayer_month_id = self.request.query_params.get("prayer_month")
        if prayer_month_id:
            qs = qs.filter(prayer_month_id=prayer_month_id)
        if not can_manage_kcan_content(self.request.user):
            qs = qs.filter(prayer_month__status="published")
        return qs.order_by("day")

    def perform_create(self, serializer):
        prayer_month = serializer.validated_data.get("prayer_month")
        require_manage_kcan_content(self.request.user)
        if prayer_month.partner.slug not in LEGACY_DEFAULT_PARTNER_SLUGS:
            raise PermissionDenied("Prayer calendar days must belong to KCAN.")
        instance = serializer.save()
        _log_bible_content_action(prayer_month.partner, self.request.user, "create_day", instance)

    def perform_update(self, serializer):
        require_manage_kcan_content(self.request.user)
        instance = serializer.save()
        _log_bible_content_action(instance.prayer_month.partner, self.request.user, "update_day", instance)

    def perform_destroy(self, instance):
        require_manage_kcan_content(self.request.user)
        _log_bible_content_action(instance.prayer_month.partner, self.request.user, "delete_day", instance)
        instance.delete()


class BibleContentAuditLogViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = BibleContentAuditLogSerializer

    def get_queryset(self):
        require_manage_kcan_content(self.request.user)
        return BibleContentAuditLog.objects.filter(partner__slug__in=LEGACY_DEFAULT_PARTNER_SLUGS).select_related(
            "partner", "user"
        )


class MeditationTopicViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = MeditationTopicSerializer
    queryset = MeditationTopic.objects.filter(is_active=True).order_by("name")


class MeditationScheduleViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = MeditationScheduleSerializer

    def get_queryset(self):
        return MeditationSchedule.objects.filter(user=self.request.user).order_by("-created_at")

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


class MeditationEntryViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = MeditationEntrySerializer

    def get_queryset(self):
        return MeditationEntry.objects.filter(user=self.request.user).order_by("-date", "-created_at")


class PrayerRequestViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = PrayerRequestSerializer

    def get_queryset(self):
        return PrayerRequest.objects.filter(user=self.request.user).order_by("-created_at")

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    @action(detail=False, methods=["get"], url_path="public")
    def public_prayers(self, request):
        qs = PrayerRequest.objects.filter(is_public=True).order_by("-created_at")[:50]
        return Response(PrayerRequestSerializer(qs, many=True).data, status=status.HTTP_200_OK)


class ReadingPlanViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = ReadingPlanSerializer
    queryset = ReadingPlan.objects.prefetch_related("items").all().order_by("name")


class ReadingPlanEnrollmentViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = ReadingPlanEnrollmentSerializer

    def get_queryset(self):
        return ReadingPlanEnrollment.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


class ReadingHistoryViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = ReadingHistorySerializer

    def get_queryset(self):
        return ReadingHistory.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


class BibleReadingPlanEventViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = BibleReadingPlanEventSerializer

    def get_queryset(self):
        qs = (
            BibleReadingPlanEvent.objects.filter(user=self.request.user)
            .select_related("translation")
            .prefetch_related("chapters", "verses")
        )
        date_from = self.request.query_params.get("date_from")
        date_to = self.request.query_params.get("date_to")
        status_filter = self.request.query_params.get("status")
        if date_from:
            qs = qs.filter(start_at__date__gte=date_from)
        if date_to:
            qs = qs.filter(start_at__date__lte=date_to)
        if status_filter:
            qs = qs.filter(status=status_filter)
        return qs.order_by("start_at")

    def perform_create(self, serializer):
        translation = serializer.validated_data.get("translation")
        if translation:
            get_public_translation(translation.code)
        event = serializer.save(user=self.request.user)
        notify_main_tab_badges_updated(
            [str(self.request.user.id)],
            source="bible",
            reason="reading_event_created",
            extra={"reading_event_id": str(event.id)},
        )

    def perform_update(self, serializer):
        event = serializer.save()
        notify_main_tab_badges_updated(
            [str(self.request.user.id)],
            source="bible",
            reason="reading_event_updated",
            extra={"reading_event_id": str(event.id)},
        )

    def perform_destroy(self, instance):
        event_id = str(instance.id)
        instance.delete()
        notify_main_tab_badges_updated(
            [str(self.request.user.id)],
            source="bible",
            reason="reading_event_deleted",
            extra={"reading_event_id": event_id},
        )

    @action(detail=False, methods=["post"], url_path="from-selection")
    def from_selection(self, request):
        translation = get_public_translation(request.data.get("translation"))
        verse_ids = request.data.get("verses") or []
        chapter_ids = request.data.get("chapters") or []
        if not verse_ids and not chapter_ids:
            return Response({"detail": "Select at least one verse or chapter."}, status=status.HTTP_400_BAD_REQUEST)

        verses = BibleVerse.objects.filter(id__in=verse_ids, translation=translation).select_related("chapter", "chapter__book")
        chapters = BibleChapter.objects.filter(id__in=chapter_ids).select_related("book")
        if verse_ids and verses.count() != len(set(verse_ids)):
            return Response({"detail": "One or more selected verses are not available for this translation."}, status=status.HTTP_400_BAD_REQUEST)
        if chapter_ids and chapters.count() != len(set(chapter_ids)):
            return Response({"detail": "One or more selected chapters were not found."}, status=status.HTTP_400_BAD_REQUEST)

        refs = [
            f"{verse.chapter.book.name} {verse.chapter.number}:{verse.number}"
            for verse in verses.order_by("chapter__book__order", "chapter__number", "number")
        ] + [
            f"{chapter.book.name} {chapter.number}"
            for chapter in chapters.order_by("book__order", "number")
        ]
        data = {
            "translation": translation.id,
            "passage_ref": request.data.get("passage_ref") or ", ".join(refs),
            "start_at": request.data.get("start_at"),
            "end_at": request.data.get("end_at"),
            "recurrence": request.data.get("recurrence", "none"),
            "reminder_offsets": request.data.get("reminder_offsets", []),
            "reminder_channels": request.data.get("reminder_channels", []),
            "source": request.data.get("source", "reader"),
        }
        serializer = self.get_serializer(data=data)
        serializer.is_valid(raise_exception=True)
        event = serializer.save(user=request.user)
        event.verses.set(verses)
        event.chapters.set(chapters)
        notify_main_tab_badges_updated(
            [str(request.user.id)],
            source="bible",
            reason="reading_event_created",
            extra={"reading_event_id": str(event.id), "source": "from_selection"},
        )
        return Response(self.get_serializer(event).data, status=status.HTTP_201_CREATED)


class BibleBookmarkViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = BibleBookmarkSerializer

    def get_queryset(self):
        qs = BibleBookmark.objects.filter(user=self.request.user).select_related("verse", "verse__chapter", "verse__chapter__book")
        book = self.request.query_params.get("book")
        translation = self.request.query_params.get("translation")
        if book:
            qs = qs.filter(verse__chapter__book__code__iexact=book)
        if translation:
            qs = qs.filter(verse__translation=get_public_translation(translation))
        return qs.order_by("-created_at")

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


class BibleNoteViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = BibleNoteSerializer

    def get_queryset(self):
        qs = BibleNote.objects.filter(user=self.request.user).select_related("verse", "verse__chapter", "verse__chapter__book")
        book = self.request.query_params.get("book")
        translation = self.request.query_params.get("translation")
        query = self.request.query_params.get("q")
        if book:
            qs = qs.filter(verse__chapter__book__code__iexact=book)
        if translation:
            qs = qs.filter(verse__translation=get_public_translation(translation))
        if query:
            qs = qs.filter(text__icontains=query)
        return qs.order_by("-updated_at")

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


class BibleHighlightViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = BibleHighlightSerializer

    def get_queryset(self):
        qs = BibleHighlight.objects.filter(user=self.request.user).select_related("verse", "verse__chapter", "verse__chapter__book")
        color = self.request.query_params.get("color")
        book = self.request.query_params.get("book")
        translation = self.request.query_params.get("translation")
        if color:
            qs = qs.filter(color__iexact=color)
        if book:
            qs = qs.filter(verse__chapter__book__code__iexact=book)
        if translation:
            qs = qs.filter(verse__translation=get_public_translation(translation))
        return qs.order_by("-created_at")

    @action(detail=False, methods=["get"], url_path="colors")
    def colors(self, request):
        colors = (
            BibleHighlight.objects.filter(user=request.user)
            .values("color")
            .annotate(count=models.Count("id"))
            .order_by("color")
        )
        return Response(list(colors), status=status.HTTP_200_OK)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


class MemoryVerseViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = MemoryVerseSerializer

    def get_queryset(self):
        return MemoryVerse.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


class BiblePreferenceViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = BiblePreferenceSerializer

    def get_queryset(self):
        return BiblePreference.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    @action(detail=False, methods=["get", "patch"], url_path="current")
    def current(self, request):
        preference, _ = BiblePreference.objects.get_or_create(user=request.user)
        if request.method.lower() == "patch":
            serializer = self.get_serializer(preference, data=request.data, partial=True)
            serializer.is_valid(raise_exception=True)
            serializer.save()
            return Response(serializer.data, status=status.HTTP_200_OK)
        return Response(self.get_serializer(preference).data, status=status.HTTP_200_OK)


class BibleCrossReferenceViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = BibleCrossReferenceSerializer

    def get_queryset(self):
        verse_id = self.request.query_params.get("verse")
        qs = BibleCrossReference.objects.select_related("verse", "related_verse", "verse__chapter__book", "related_verse__chapter__book")
        if verse_id:
            qs = qs.filter(verse_id=verse_id)
        return qs


class BibleStatsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        history_count = ReadingHistory.objects.filter(user=request.user).count()
        bookmarks = BibleBookmark.objects.filter(user=request.user).count()
        highlights = BibleHighlight.objects.filter(user=request.user).count()
        notes = BibleNote.objects.filter(user=request.user).count()
        plans = ReadingPlanEnrollment.objects.filter(user=request.user).count()
        return Response(
            {
                "reading_sessions": history_count,
                "bookmarks": bookmarks,
                "highlights": highlights,
                "notes": notes,
                "active_plans": plans,
                "streak": max(history_count, 1),
            }
        )


def can_manage_partner_courses(user, partner: Partner) -> bool:
    if not partner or not getattr(user, "is_authenticated", False):
        return False
    if partner.owner_id == user.id:
        return True
    if not partner.main_conversation_id:
        return False
    member = ConversationMember.objects.filter(
        conversation_id=partner.main_conversation_id,
        user=user,
        left_at__isnull=True,
    ).first()
    if not member:
        return False
    return member.base_role in {BaseConversationRole.OWNER, BaseConversationRole.ADMIN}


def _apply_coupon_to_price(price_amount, coupon: BibleCourseCoupon):
    if not coupon or not coupon.is_active:
        return price_amount
    if coupon.valid_until and coupon.valid_until < timezone.now():
        return price_amount
    if coupon.max_redemptions and coupon.redeemed_count >= coupon.max_redemptions:
        return price_amount
    amount = price_amount
    if coupon.percent_off:
        amount = amount * (100 - coupon.percent_off) / 100
    if coupon.amount_off:
        amount = max(0, amount - coupon.amount_off)
    return amount


def require_course_access(user, course: BibleCourse):
    if course.is_public:
        return
    enrolled = BibleCourseEnrollment.objects.filter(user=user, course=course).exists()
    if not enrolled and course.partner_id:
        membership = PartnerMembership.objects.filter(partner=course.partner, user=user).exists()
        if membership:
            return
    if not enrolled:
        raise PermissionDenied("Not allowed to access this course.")


def require_manage_course(user, course: BibleCourse):
    if course.partner and not can_manage_partner_courses(user, course.partner):
        raise PermissionDenied("You do not have permission to manage this course.")


class BibleCourseViewSet(viewsets.ModelViewSet):
    permission_classes = [AllowAny]
    serializer_class = BibleCourseSerializer

    def get_queryset(self):
        scope = self.request.query_params.get("scope")
        partner_id = self.request.query_params.get("partner")
        qs = BibleCourse.objects.filter(published=True).select_related(
            "partner",
            "partner__owner",
            "partner__organization_profile",
        )
        if scope == "bible":
            qs = qs.filter(is_bible_course=True, partner__slug__in=LEGACY_DEFAULT_PARTNER_SLUGS)
        elif scope == "partner":
            qs = qs.filter(is_bible_course=False)
        partner = None
        if partner_id:
            partner = Partner.objects.filter(id=partner_id).first()
        elif scope == "partner":
            partner = get_kcan_partner()
        if partner:
            if scope == "partner":
                membership = PartnerMembership.objects.filter(partner=partner, user=self.request.user).first()
                enrolled = BibleCourseEnrollment.objects.filter(user=self.request.user, course__partner=partner).exists()
                if not membership and not enrolled:
                    return BibleCourse.objects.none()
            qs = qs.filter(partner=partner)
        if self.request.user.is_authenticated:
            enrolled_ids = BibleCourseEnrollment.objects.filter(user=self.request.user).values_list("course_id", flat=True)
            qs = qs.filter(
                models.Q(is_public=True)
                | models.Q(id__in=enrolled_ids)
                | models.Q(partner__owner=self.request.user)
            )
        else:
            qs = qs.filter(is_public=True)
        return qs.order_by("-created_at")

    def perform_create(self, serializer):
        partner_id = self.request.data.get("partner")
        partner = Partner.objects.filter(id=partner_id).first() if partner_id else None
        is_bible_course = bool(self.request.data.get("is_bible_course"))
        if is_bible_course and partner and partner.slug != KCAN_PARTNER_SLUG:
            raise PermissionDenied("Bible courses are reserved for the KCAN partner.")
        if partner and not can_manage_partner_courses(self.request.user, partner):
            raise PermissionDenied("You do not have permission to create courses for this partner.")
        is_public = self.request.data.get("is_public")
        if is_public is None:
            is_public = True if is_bible_course else False
        serializer.save(partner=partner, is_public=bool(is_public))

    def perform_update(self, serializer):
        partner = serializer.instance.partner
        if partner and not can_manage_partner_courses(self.request.user, partner):
            raise PermissionDenied("You do not have permission to update courses for this partner.")
        serializer.save()

    def get_permissions(self):
        if self.action in {"list", "retrieve"}:
            return [AllowAny()]
        if self.action == "certificate":
            return [AllowAny()]
        return [IsAuthenticated()]

    def get_authenticators(self):
        if getattr(self, "action", None) == "certificate":
            return []
        return super().get_authenticators()

    @action(detail=True, methods=["get"], url_path="certificate", authentication_classes=[], permission_classes=[AllowAny])
    def certificate(self, request, pk=None):
        course = self.get_object()
        user = request.user
        if not user or user.is_anonymous:
            token = request.query_params.get("token")
            if not token:
                raise PermissionDenied("Authentication required.")
            jwt_auth = JWTAuthentication()
            try:
                validated = jwt_auth.get_validated_token(token)
                user = jwt_auth.get_user(validated)
                request.user = user
            except (InvalidToken, TokenError):
                raise PermissionDenied("Invalid token.")
        enrollment = BibleCourseEnrollment.objects.filter(user=user, course=course).first()
        if not enrollment:
            raise PermissionDenied("You are not enrolled in this course.")
        if enrollment.status != "completed" and enrollment.progress_percent < 100:
            raise PermissionDenied("Course is not completed.")
        user_name = user.display_name or user.phone or user.email or "Member"
        partner_profile = None
        partner_name = KCAN_PARTNER_NAME
        brand_color = None
        logo_url = None
        if course.partner_id:
            partner_profile = PartnerOrganizationProfile.objects.filter(partner=course.partner).first()
            partner_name = (
                partner_profile.display_name
                if partner_profile and partner_profile.display_name
                else course.partner.name or partner_name
            )
        if partner_profile:
            colors = partner_profile.brand_colors or []
            brand_color = colors[0] if colors else None
            logo_url = partner_profile.logo_url or None
        partner_override = request.query_params.get("partner_name")
        if partner_override:
            partner_name = partner_override
        instructor_override = (
            request.query_params.get("instructor_name") or request.query_params.get("instructor")
        )
        instructor_name = instructor_override or user.display_name or user.email or "David Williams"
        wordmark = (
            request.query_params.get("wordmark")
            or (partner_profile.display_name if partner_profile and partner_profile.display_name else None)
            or partner_name
            or "KIS"
        )
        completed_at = getattr(enrollment, "completed_at", None)
        if not completed_at:
            completed_at = timezone.now()
        issued_on = completed_at.date()
        credential_id = (
            BibleCourseCredential.objects.filter(enrollment=enrollment)
            .values_list("share_token", flat=True)
            .first()
        )
        pdf_bytes = build_certificate_pdf(
            user_name=user_name,
            course_title=course.title,
            partner_name=partner_name,
            brand_color=brand_color,
            logo_url=logo_url,
            issued_on=issued_on,
            credential_id=credential_id,
            instructor_name=instructor_name,
            wordmark=wordmark,
        )
        rel_path = ensure_certificate_file(enrollment.id, credential_id, pdf_bytes)
        certificate_url = build_certificate_url(request, rel_path)
        if request.query_params.get("format") == "json":
            return Response({"certificate_url": certificate_url}, status=status.HTTP_200_OK)
        filename = f"kis-certificate-{course.id}.pdf"
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'inline; filename="{filename}"'
        response["X-Certificate-URL"] = certificate_url
        return response


class BibleCourseModuleViewSet(viewsets.ModelViewSet):
    permission_classes = [AllowAny]
    serializer_class = BibleCourseModuleSerializer

    def get_queryset(self):
        course_id = self.request.query_params.get("course")
        qs = BibleCourseModule.objects.all()
        if course_id:
            qs = qs.filter(course_id=course_id)
        return qs.order_by("order")

    def get_permissions(self):
        if self.action in {"list", "retrieve"}:
            return [AllowAny()]
        return [IsAuthenticated()]

    def perform_create(self, serializer):
        course = serializer.validated_data.get("course")
        if course and course.partner and not can_manage_partner_courses(self.request.user, course.partner):
            raise PermissionDenied("You do not have permission to manage this course.")
        serializer.save()


class BibleLessonViewSet(viewsets.ModelViewSet):
    permission_classes = [AllowAny]
    serializer_class = BibleLessonSerializer

    def get_queryset(self):
        course_id = self.request.query_params.get("course")
        qs = BibleLesson.objects.all()
        if course_id:
            qs = qs.filter(course_id=course_id)
        return qs.order_by("order")

    def get_permissions(self):
        if self.action in {"list", "retrieve"}:
            return [AllowAny()]
        return [IsAuthenticated()]

    def perform_create(self, serializer):
        course = serializer.validated_data.get("course")
        if course and course.partner and not can_manage_partner_courses(self.request.user, course.partner):
            raise PermissionDenied("You do not have permission to manage this lesson.")
        serializer.save()


class BibleCourseTrackViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = BibleCourseTrackSerializer

    def get_queryset(self):
        partner_id = self.request.query_params.get("partner")
        qs = BibleCourseTrack.objects.all()
        if partner_id:
            qs = qs.filter(partner_id=partner_id)
        return qs.order_by("-created_at")

    def perform_create(self, serializer):
        partner_id = self.request.data.get("partner")
        partner = Partner.objects.filter(id=partner_id).first() if partner_id else None
        if partner and not can_manage_partner_courses(self.request.user, partner):
            raise PermissionDenied("You do not have permission to manage this track.")
        serializer.save(partner=partner)


class BibleCourseTrackItemViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = BibleCourseTrackItemSerializer

    def get_queryset(self):
        track_id = self.request.query_params.get("track")
        qs = BibleCourseTrackItem.objects.all()
        if track_id:
            qs = qs.filter(track_id=track_id)
        return qs.order_by("order")

    def perform_create(self, serializer):
        track = serializer.validated_data.get("track")
        if track and track.partner and not can_manage_partner_courses(self.request.user, track.partner):
            raise PermissionDenied("You do not have permission to manage this track.")
        serializer.save()


class BibleCoursePrerequisiteViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = BibleCoursePrerequisiteSerializer

    def get_queryset(self):
        course_id = self.request.query_params.get("course")
        qs = BibleCoursePrerequisite.objects.all()
        if course_id:
            qs = qs.filter(course_id=course_id)
        return qs

    def perform_create(self, serializer):
        course = serializer.validated_data.get("course")
        if course:
            require_manage_course(self.request.user, course)
        serializer.save()


class BibleQuizViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = BibleQuizSerializer

    def get_queryset(self):
        course_id = self.request.query_params.get("course")
        lesson_id = self.request.query_params.get("lesson")
        qs = BibleQuiz.objects.all()
        if course_id:
            course = BibleCourse.objects.filter(id=course_id).first()
            if course:
                require_course_access(self.request.user, course)
            qs = qs.filter(course_id=course_id)
        if lesson_id:
            qs = qs.filter(lesson_id=lesson_id)
        return qs.order_by("order")

    def perform_create(self, serializer):
        course = serializer.validated_data.get("course")
        if course:
            require_manage_course(self.request.user, course)
        serializer.save()

    @action(detail=True, methods=["post"], url_path="submit")
    def submit(self, request, pk=None):
        quiz = self.get_object()
        require_course_access(request.user, quiz.course)
        attempts_used = BibleQuizAttempt.objects.filter(user=request.user, quiz=quiz).count()
        if quiz.attempts_allowed and attempts_used >= quiz.attempts_allowed:
            return Response({"detail": "Attempts limit reached."}, status=status.HTTP_400_BAD_REQUEST)
        answers = request.data.get("answers") or []
        score = 0
        max_score = 0
        for question in quiz.questions.all():
            max_score += question.points
            user_answer = next((a for a in answers if str(a.get("question")) == str(question.id)), None)
            if not user_answer:
                continue
            if question.kind in ["single_choice", "multiple_choice", "true_false"]:
                selected_ids = user_answer.get("choices") or []
                correct_ids = list(question.choices.filter(is_correct=True).values_list("id", flat=True))
                if question.kind == "single_choice":
                    if selected_ids and selected_ids[0] in correct_ids:
                        score += question.points
                elif question.kind == "multiple_choice":
                    if set(selected_ids) == set(correct_ids):
                        score += question.points
                else:
                    if selected_ids and selected_ids[0] in correct_ids:
                        score += question.points
            else:
                # short_answer manual grading later
                pass
        passed = max_score > 0 and int((score / max_score) * 100) >= quiz.pass_score
        attempt = BibleQuizAttempt.objects.create(
            quiz=quiz,
            user=request.user,
            score=score,
            max_score=max_score,
            passed=passed,
            answers=answers,
            completed_at=timezone.now(),
        )
        return Response(BibleQuizAttemptSerializer(attempt).data, status=status.HTTP_200_OK)


class BibleQuizQuestionViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = BibleQuizQuestionSerializer

    def get_queryset(self):
        quiz_id = self.request.query_params.get("quiz")
        qs = BibleQuizQuestion.objects.all()
        if quiz_id:
            qs = qs.filter(quiz_id=quiz_id)
        return qs.order_by("order")

    def perform_create(self, serializer):
        quiz = serializer.validated_data.get("quiz")
        if quiz:
            require_manage_course(self.request.user, quiz.course)
        serializer.save()


class BibleQuizChoiceViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = BibleQuizChoiceSerializer

    def get_queryset(self):
        question_id = self.request.query_params.get("question")
        qs = BibleQuizChoice.objects.all()
        if question_id:
            qs = qs.filter(question_id=question_id)
        return qs

    def perform_create(self, serializer):
        question = serializer.validated_data.get("question")
        if question:
            require_manage_course(self.request.user, question.quiz.course)
        serializer.save()


class BibleQuizAttemptViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = BibleQuizAttemptSerializer

    def get_queryset(self):
        quiz_id = self.request.query_params.get("quiz")
        qs = BibleQuizAttempt.objects.filter(user=self.request.user)
        if quiz_id:
            qs = qs.filter(quiz_id=quiz_id)
        return qs.order_by("-started_at")


class BibleAssignmentViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = BibleAssignmentSerializer

    def get_queryset(self):
        course_id = self.request.query_params.get("course")
        lesson_id = self.request.query_params.get("lesson")
        qs = BibleAssignment.objects.all()
        if course_id:
            course = BibleCourse.objects.filter(id=course_id).first()
            if course:
                require_course_access(self.request.user, course)
            qs = qs.filter(course_id=course_id)
        if lesson_id:
            qs = qs.filter(lesson_id=lesson_id)
        return qs.order_by("order")

    def perform_create(self, serializer):
        course = serializer.validated_data.get("course")
        if course:
            require_manage_course(self.request.user, course)
        serializer.save()


class BibleAssignmentSubmissionViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = BibleAssignmentSubmissionSerializer

    def get_queryset(self):
        assignment_id = self.request.query_params.get("assignment")
        qs = BibleAssignmentSubmission.objects.filter(user=self.request.user)
        if assignment_id:
            assignment = BibleAssignment.objects.filter(id=assignment_id).first()
            if assignment and assignment.course:
                try:
                    require_manage_course(self.request.user, assignment.course)
                    qs = BibleAssignmentSubmission.objects.filter(assignment_id=assignment_id)
                except PermissionDenied:
                    qs = qs.filter(assignment_id=assignment_id)
            else:
                qs = qs.filter(assignment_id=assignment_id)
        return qs.order_by("-created_at")

    def perform_create(self, serializer):
        assignment = serializer.validated_data.get("assignment")
        require_course_access(self.request.user, assignment.course)
        content_text = serializer.validated_data.get("content_text") or ""
        length = len(content_text.strip())
        plagiarism_score = 0
        plagiarism_status = "clean"
        if length > 1200:
            plagiarism_score = 15
            plagiarism_status = "pending"
        serializer.save(
            user=self.request.user,
            plagiarism_score=plagiarism_score,
            plagiarism_status=plagiarism_status,
        )

    @action(detail=True, methods=["post"], url_path="grade")
    def grade(self, request, pk=None):
        submission = self.get_object()
        assignment = submission.assignment
        require_manage_course(request.user, assignment.course)
        score = int(request.data.get("score", submission.score or 0))
        feedback = request.data.get("feedback", submission.feedback or "")
        submission.score = score
        submission.feedback = feedback
        submission.status = "graded"
        submission.graded_at = timezone.now()
        submission.save(update_fields=["score", "feedback", "status", "graded_at"])
        return Response(BibleAssignmentSubmissionSerializer(submission).data, status=status.HTTP_200_OK)


class BiblePeerReviewViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = BiblePeerReviewSerializer

    def get_queryset(self):
        submission_id = self.request.query_params.get("submission")
        qs = BiblePeerReview.objects.all()
        if submission_id:
            qs = qs.filter(submission_id=submission_id)
        return qs.order_by("-created_at")

    def perform_create(self, serializer):
        submission = serializer.validated_data.get("submission")
        if submission.user_id == self.request.user.id:
            raise PermissionDenied("Cannot review your own submission.")
        serializer.save(reviewer=self.request.user)


class BibleCourseForumViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = BibleCourseForumSerializer

    def get_queryset(self):
        course_id = self.request.query_params.get("course")
        qs = BibleCourseForum.objects.all()
        if course_id:
            course = BibleCourse.objects.filter(id=course_id).first()
            if course:
                require_course_access(self.request.user, course)
            qs = qs.filter(course_id=course_id)
        return qs

    def perform_create(self, serializer):
        course = serializer.validated_data.get("course")
        if course:
            require_manage_course(self.request.user, course)
        serializer.save()


class BibleForumThreadViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = BibleForumThreadSerializer

    def get_queryset(self):
        forum_id = self.request.query_params.get("forum")
        qs = BibleForumThread.objects.all().select_related("forum", "created_by")
        if forum_id:
            forum = BibleCourseForum.objects.filter(id=forum_id).first()
            if forum:
                require_course_access(self.request.user, forum.course)
            qs = qs.filter(forum_id=forum_id)
        return qs

    def perform_create(self, serializer):
        forum = serializer.validated_data.get("forum")
        if forum and forum.is_locked:
            raise PermissionDenied("Forum is locked.")
        require_course_access(self.request.user, forum.course)
        serializer.save(created_by=self.request.user)


class BibleForumPostViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = BibleForumPostSerializer

    def get_queryset(self):
        thread_id = self.request.query_params.get("thread")
        qs = BibleForumPost.objects.all().select_related("thread", "user")
        if thread_id:
            thread = BibleForumThread.objects.filter(id=thread_id).first()
            if thread:
                require_course_access(self.request.user, thread.forum.course)
            qs = qs.filter(thread_id=thread_id)
        return qs

    def perform_create(self, serializer):
        thread = serializer.validated_data.get("thread")
        if thread and thread.is_locked:
            raise PermissionDenied("Thread is locked.")
        require_course_access(self.request.user, thread.forum.course)
        serializer.save(user=self.request.user)


class BibleMentorAssignmentViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = BibleMentorAssignmentSerializer

    def get_queryset(self):
        course_id = self.request.query_params.get("course")
        qs = BibleMentorAssignment.objects.all()
        if course_id:
            qs = qs.filter(course_id=course_id)
        return qs

    def perform_create(self, serializer):
        course = serializer.validated_data.get("course")
        if course:
            require_manage_course(self.request.user, course)
        serializer.save()


class BibleLiveSessionViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = BibleLiveSessionSerializer

    def get_queryset(self):
        course_id = self.request.query_params.get("course")
        qs = BibleLiveSession.objects.all()
        if course_id:
            course = BibleCourse.objects.filter(id=course_id).first()
            if course:
                require_course_access(self.request.user, course)
            qs = qs.filter(course_id=course_id)
        return qs

    def perform_create(self, serializer):
        course = serializer.validated_data.get("course")
        if course:
            require_manage_course(self.request.user, course)
        serializer.save(host=self.request.user)


class BibleLiveAttendanceViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = BibleLiveAttendanceSerializer

    def get_queryset(self):
        session_id = self.request.query_params.get("session")
        qs = BibleLiveAttendance.objects.filter(user=self.request.user)
        if session_id:
            qs = qs.filter(session_id=session_id)
        return qs

    def perform_create(self, serializer):
        session = serializer.validated_data.get("session")
        require_course_access(self.request.user, session.course)
        serializer.save(user=self.request.user, status="registered")

    @action(detail=True, methods=["post"], url_path="join")
    def join(self, request, pk=None):
        attendance = self.get_object()
        attendance.status = "joined"
        attendance.joined_at = timezone.now()
        attendance.save(update_fields=["status", "joined_at"])
        return Response(BibleLiveAttendanceSerializer(attendance).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="leave")
    def leave(self, request, pk=None):
        attendance = self.get_object()
        attendance.status = "left"
        attendance.left_at = timezone.now()
        attendance.save(update_fields=["status", "left_at"])
        return Response(BibleLiveAttendanceSerializer(attendance).data, status=status.HTTP_200_OK)


class BibleLiveRecordingViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = BibleLiveRecordingSerializer

    def get_queryset(self):
        session_id = self.request.query_params.get("session")
        qs = BibleLiveRecording.objects.all()
        if session_id:
            qs = qs.filter(session_id=session_id)
        return qs

    def perform_create(self, serializer):
        session = serializer.validated_data.get("session")
        if session:
            require_manage_course(self.request.user, session.course)
        serializer.save()


class BibleCourseBundleViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = BibleCourseBundleSerializer

    def get_queryset(self):
        partner_id = self.request.query_params.get("partner")
        qs = BibleCourseBundle.objects.all()
        if partner_id:
            qs = qs.filter(partner_id=partner_id)
        return qs

    def perform_create(self, serializer):
        partner = serializer.validated_data.get("partner")
        if partner and not can_manage_partner_courses(self.request.user, partner):
            raise PermissionDenied("You do not have permission to manage bundles.")
        serializer.save()


class BibleCourseBundleItemViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = BibleCourseBundleItemSerializer

    def get_queryset(self):
        bundle_id = self.request.query_params.get("bundle")
        qs = BibleCourseBundleItem.objects.all()
        if bundle_id:
            qs = qs.filter(bundle_id=bundle_id)
        return qs.order_by("order")

    def perform_create(self, serializer):
        bundle = serializer.validated_data.get("bundle")
        if bundle and not can_manage_partner_courses(self.request.user, bundle.partner):
            raise PermissionDenied("You do not have permission to manage bundles.")
        serializer.save()


class BibleCourseCouponViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = BibleCourseCouponSerializer

    def get_queryset(self):
        partner_id = self.request.query_params.get("partner")
        qs = BibleCourseCoupon.objects.all()
        if partner_id:
            qs = qs.filter(models.Q(course__partner_id=partner_id) | models.Q(bundle__partner_id=partner_id))
        return qs

    def perform_create(self, serializer):
        course = serializer.validated_data.get("course")
        bundle = serializer.validated_data.get("bundle")
        partner = course.partner if course else bundle.partner if bundle else None
        if partner and not can_manage_partner_courses(self.request.user, partner):
            raise PermissionDenied("You do not have permission to manage coupons.")
        serializer.save()


class BibleEnterpriseSeatPoolViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = BibleEnterpriseSeatPoolSerializer

    def get_queryset(self):
        partner_id = self.request.query_params.get("partner")
        qs = BibleEnterpriseSeatPool.objects.all()
        if partner_id:
            qs = qs.filter(partner_id=partner_id)
        return qs

    def perform_create(self, serializer):
        partner = serializer.validated_data.get("partner")
        if partner and not can_manage_partner_courses(self.request.user, partner):
            raise PermissionDenied("You do not have permission to manage seat pools.")
        serializer.save()


class BibleRefundRequestViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = BibleRefundRequestSerializer

    def get_queryset(self):
        return BibleRefundRequest.objects.filter(enrollment__user=self.request.user)

    def perform_create(self, serializer):
        enrollment = serializer.validated_data.get("enrollment")
        if enrollment.user_id != self.request.user.id:
            raise PermissionDenied("Cannot request refund for another user.")
        serializer.save()


class BibleCourseCredentialViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = BibleCourseCredentialSerializer

    def get_queryset(self):
        return BibleCourseCredential.objects.filter(enrollment__user=self.request.user)

    @action(detail=True, methods=["get"], url_path="share")
    def share(self, request, pk=None):
        credential = self.get_object()
        return Response({"share_url": f"/api/v1/bible/credentials/share/{credential.share_token}/"}, status=status.HTTP_200_OK)


class BibleCourseCredentialShareView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, token=None):
        credential = get_object_or_404(BibleCourseCredential, share_token=token)
        enrollment = credential.enrollment
        data = {
            "course": enrollment.course.title,
            "user": enrollment.user.display_name or enrollment.user.phone,
            "badge_name": credential.badge_name,
            "issued_at": credential.issued_at,
        }
        return Response(data, status=status.HTTP_200_OK)


class BibleCourseEnrollmentViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = BibleCourseEnrollmentSerializer

    def get_queryset(self):
        return BibleCourseEnrollment.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        course = serializer.validated_data.get("course")
        if course:
            for prereq in course.prerequisites.select_related("required_course").all():
                req_enrollment = BibleCourseEnrollment.objects.filter(
                    user=self.request.user,
                    course=prereq.required_course,
                ).first()
                if not req_enrollment or req_enrollment.progress_percent < prereq.required_percent:
                    raise PermissionDenied("Prerequisites not met for this course.")
        enrollment = serializer.save(user=self.request.user)
        course = enrollment.course
        if course.partner:
            PartnerMembership.objects.get_or_create(
                partner=course.partner,
                user=self.request.user,
                defaults={"status": PartnerMembershipStatus.SUBSCRIBER, "role": "subscriber"},
            )

    @action(detail=True, methods=["post"], url_path="complete")
    def complete_course(self, request, pk=None):
        enrollment = self.get_object()
        enrollment.status = "completed"
        enrollment.progress_percent = 100
        enrollment.save(update_fields=["status", "progress_percent"])
        return Response(BibleCourseEnrollmentSerializer(enrollment).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="purchase")
    def purchase_course(self, request, pk=None):
        enrollment = self.get_object()
        course = enrollment.course
        if enrollment.is_paid:
            return Response({"detail": "Course already unlocked."}, status=status.HTTP_200_OK)
        if course.is_free:
            return Response({"detail": "Course is free."}, status=status.HTTP_400_BAD_REQUEST)
        if not course.price_amount:
            enrollment.is_paid = True
            enrollment.save(update_fields=["is_paid"])
            return Response(BibleCourseEnrollmentSerializer(enrollment).data, status=status.HTTP_200_OK)
        seat_pool_id = request.data.get("seat_pool")
        if seat_pool_id:
            seat_pool = BibleEnterpriseSeatPool.objects.filter(id=seat_pool_id, partner=course.partner).first()
            if not seat_pool:
                return Response({"detail": "Seat pool not found."}, status=status.HTTP_400_BAD_REQUEST)
            if seat_pool.course_id and seat_pool.course_id != course.id:
                return Response({"detail": "Seat pool does not match this course."}, status=status.HTTP_400_BAD_REQUEST)
            if seat_pool.expires_at and seat_pool.expires_at < timezone.now():
                return Response({"detail": "Seat pool has expired."}, status=status.HTTP_400_BAD_REQUEST)
            if seat_pool.seats_used >= seat_pool.seats_total:
                return Response({"detail": "No seats available in this pool."}, status=status.HTTP_400_BAD_REQUEST)
            BibleEnterpriseSeatPool.objects.filter(id=seat_pool.id).update(seats_used=models.F("seats_used") + 1)
            enrollment.is_paid = True
            enrollment.save(update_fields=["is_paid"])
            record_ledger(
                request.user,
                amount_cents=0,
                credits_delta=0,
                kind="enterprise-seat",
                reference=f"course:{course.id}",
                meta={"course": course.title, "seat_pool": str(seat_pool.id)},
            )
            return Response(BibleCourseEnrollmentSerializer(enrollment).data, status=status.HTTP_200_OK)
        coupon_code = request.data.get("coupon_code") or request.data.get("coupon")
        coupon = None
        if coupon_code:
            coupon = BibleCourseCoupon.objects.filter(code__iexact=coupon_code).first()
            if not coupon:
                return Response({"detail": "Invalid coupon code."}, status=status.HTTP_400_BAD_REQUEST)
            if not coupon.is_active:
                return Response({"detail": "Coupon is inactive."}, status=status.HTTP_400_BAD_REQUEST)
            if coupon.valid_until and coupon.valid_until < timezone.now():
                return Response({"detail": "Coupon has expired."}, status=status.HTTP_400_BAD_REQUEST)
            if coupon.max_redemptions and coupon.redeemed_count >= coupon.max_redemptions:
                return Response({"detail": "Coupon has no remaining redemptions."}, status=status.HTTP_400_BAD_REQUEST)
            if coupon.course_id and coupon.course_id != course.id:
                return Response({"detail": "Coupon is not valid for this course."}, status=status.HTTP_400_BAD_REQUEST)
            if coupon.bundle_id:
                bundle_match = BibleCourseBundleItem.objects.filter(
                    bundle_id=coupon.bundle_id,
                    course_id=course.id,
                ).exists()
                if not bundle_match:
                    return Response({"detail": "Coupon is not valid for this course."}, status=status.HTTP_400_BAD_REQUEST)
        credits = get_credit_account(request.user)
        price_amount = Decimal(course.price_amount or 0)
        if coupon:
            price_amount = Decimal(str(_apply_coupon_to_price(price_amount, coupon)))
        price_cents = int(price_amount * Decimal("100"))
        required_credits = 0 if price_cents <= 0 else max(1, price_cents // 5)
        if credits.credits < required_credits:
            return Response({"detail": "Insufficient credits."}, status=status.HTTP_400_BAD_REQUEST)
        if required_credits:
            record_ledger(
                request.user,
                amount_cents=0,
                credits_delta=-required_credits,
                kind="purchase",
                reference=f"course:{course.id}",
                meta={"course": course.title, "coupon": coupon.code if coupon else None},
            )
        enrollment.is_paid = True
        enrollment.save(update_fields=["is_paid"])
        if coupon:
            BibleCourseCoupon.objects.filter(id=coupon.id).update(redeemed_count=models.F("redeemed_count") + 1)
        return Response(BibleCourseEnrollmentSerializer(enrollment).data, status=status.HTTP_200_OK)


class BibleLessonProgressViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = BibleLessonProgressSerializer

    def get_queryset(self):
        return BibleLessonProgress.objects.filter(user=self.request.user)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        lesson = serializer.validated_data.get("lesson")
        completed = serializer.validated_data.get("completed", False)
        last_position_ms = serializer.validated_data.get("last_position_ms", 0)
        progress, created = BibleLessonProgress.objects.get_or_create(
            user=request.user,
            lesson=lesson,
            defaults={"completed": completed, "last_position_ms": last_position_ms},
        )
        if not created:
            if "completed" in serializer.validated_data:
                progress.completed = completed
            if "last_position_ms" in serializer.validated_data:
                progress.last_position_ms = last_position_ms
            progress.save(update_fields=["completed", "last_position_ms", "updated_at"])
        _update_course_progress(lesson.course, request.user)
        return Response(BibleLessonProgressSerializer(progress).data, status=status.HTTP_200_OK)

    def perform_create(self, serializer):
        progress = serializer.save(user=self.request.user)
        _update_course_progress(progress.lesson.course, self.request.user)

    def perform_update(self, serializer):
        progress = serializer.save()
        _update_course_progress(progress.lesson.course, self.request.user)


def _update_course_progress(course: BibleCourse, user):
    total_lessons = course.lessons.count()
    if total_lessons == 0:
        return
    completed = BibleLessonProgress.objects.filter(user=user, lesson__course=course, completed=True).count()
    percent = int((completed / total_lessons) * 100)
    enrollment = BibleCourseEnrollment.objects.filter(user=user, course=course).first()
    if enrollment:
        enrollment.progress_percent = percent
        if percent >= 100:
            enrollment.status = "completed"
            if not hasattr(enrollment, "credential"):
                BibleCourseCredential.objects.create(
                    enrollment=enrollment,
                    badge_name=f"{course.title} Certificate",
                    share_token=secrets.token_hex(24),
                )
        enrollment.save(update_fields=["progress_percent", "status"])


class BibleLessonReactionView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk=None):
        lesson = get_object_or_404(BibleLesson, pk=pk)
        require_course_access(request.user, lesson.course)
        reaction = BibleLessonReaction.objects.filter(lesson=lesson, user=request.user).first()
        if reaction:
            reaction.delete()
            reacted = False
        else:
            reaction = BibleLessonReaction.objects.create(lesson=lesson, user=request.user, emoji="❤️")
            reacted = True
        return Response(
            {"count": lesson.reactions.count(), "reacted": reacted},
            status=status.HTTP_200_OK,
        )


class BibleLessonCommentViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = BibleLessonCommentSerializer

    def get_queryset(self):
        lesson_id = self.request.query_params.get("lesson")
        qs = BibleLessonComment.objects.all()
        if lesson_id:
            qs = qs.filter(lesson_id=lesson_id)
        return qs.order_by("-created_at")

    def perform_create(self, serializer):
        lesson = serializer.validated_data.get("lesson")
        require_course_access(self.request.user, lesson.course)
        serializer.save(user=self.request.user)

class BibleCourseReactionView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk=None):
        course = get_object_or_404(BibleCourse, pk=pk, published=True)
        if not course.is_public:
            enrolled = BibleCourseEnrollment.objects.filter(user=request.user, course=course).exists()
            if not enrolled:
                raise PermissionDenied("Not allowed to react to this course.")
        reaction = BibleCourseReaction.objects.filter(course=course, user=request.user).first()
        if reaction:
            reaction.delete()
            reacted = False
        else:
            emoji = request.data.get("emoji", "❤️")
            BibleCourseReaction.objects.create(course=course, user=request.user, emoji=emoji)
            reacted = True
        return Response(
            {"count": course.reactions.count(), "reacted": reacted},
            status=status.HTTP_200_OK,
        )


class BibleCourseCommentViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = BibleCourseCommentSerializer

    def get_queryset(self):
        course_id = self.request.query_params.get("course")
        qs = BibleCourseComment.objects.all().select_related("course", "user")
        if course_id:
            qs = qs.filter(course_id=course_id)
        return qs

    def perform_create(self, serializer):
        course_id = self.request.data.get("course")
        course = get_object_or_404(BibleCourse, pk=course_id, published=True)
        if not course.is_public:
            enrolled = BibleCourseEnrollment.objects.filter(user=self.request.user, course=course).exists()
            if not enrolled:
                raise PermissionDenied("Not allowed to comment on this course.")
        serializer.save(user=self.request.user, course=course)


class BibleCourseShareView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk=None):
        course = get_object_or_404(BibleCourse, pk=pk, published=True)
        if not course.is_public:
            raise PermissionDenied("Course is private.")
        BibleCourseShare.objects.create(course=course, user=request.user)
        return Response({"shared": True}, status=status.HTTP_200_OK)
