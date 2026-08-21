import json
import logging
import re
import uuid
from copy import deepcopy
from decimal import Decimal, InvalidOperation
from typing import Any
from django.core.files.uploadedfile import UploadedFile

from django.db import IntegrityError
from django.db.utils import ProgrammingError
from django.db.models import Max
from django.http import QueryDict
from django.utils import timezone
from django.utils.text import slugify
from rest_framework import serializers
from .availability import normalize_availability_payload, derive_availability_rules_from_payload
from common.media_urls import absolutize_backend_media, normalize_image_payload
from .models import (
    Shop,
    ShopLandingPage,
    ShopLandingTestimonial,
    ShopVerificationRequest,
    Product,
    ProductAuthenticityCheck,
    Order,
    OrderItem,
    Payment,
    Promotion,
    Subscription,
    LoyaltyPoint,
    ShopFollow,
    ProductShare,
    ProductReview,
    ProductQuestion,
    AIRecommendation,
    AuditLog,
    FraudSignal,
    ProductImage,
    ProductRating,
    ShopTeamMember,
    ShopService,
    ServiceBooking,
    ServiceBookingEscrow,
    ServiceBookingComplaint,
    ShopServiceImage,
    ServiceRating,
    ServiceBookingPayment,
    Cart,
    CartItem,
    MarketplaceOrder,
    MarketplaceOrderItem,
    MarketplaceOrderStatus,
    MarketplaceComplaint,
    CatalogCategory,
)

AVAILABILITY_RULE_SCOPES = {'year', 'month', 'week', 'day'}
TIME_PATTERN = re.compile(r'^([01]?\d|2[0-3]):([0-5]\d)$')
logger = logging.getLogger(__name__)


def _usd_label_from_cents(value: int | None) -> str:
    cents = int(value or 0)
    return f"${(Decimal(cents) / Decimal('100')).quantize(Decimal('0.01'))}"


def _commerce_currency_label(currency: object, metadata: dict | None = None) -> str:
    code = str(currency or '').strip().upper()
    if code == 'USD':
        return 'USD'
    if code in {'KISC', 'KIS'}:
        return 'Historical promotional-credit order'
    return code or 'USD'


def _public_user_summary(user) -> dict | None:
    if not user:
        return None
    display = (
        getattr(user, 'display_name', '')
        or getattr(user, 'username', '')
        or getattr(user, 'phone', '')
        or 'KIS member'
    )
    return {
        'id': str(getattr(user, 'id', '')),
        'display_name': display,
    }


def _commerce_trust_summary(shop: Shop | None) -> dict:
    if not shop:
        return {'verified': False, 'badges': [], 'label': 'Seller'}
    badges = [str(item) for item in (getattr(shop, 'trust_badges', []) or []) if str(item).strip()]
    verified = bool(getattr(shop, 'is_verified', False) or str(getattr(shop, 'verification_status', '')).upper() == 'VERIFIED')
    if verified and not any(item in badges for item in ('verified-shop', 'trusted-merchant')):
        badges.append('verified-shop')
    return {
        'verified': verified,
        'badges': sorted(set(badges)),
        'label': 'Verified seller' if verified else 'Seller',
        'rating_avg': float(getattr(shop, 'rating_avg', 0) or 0),
        'rating_count': int(getattr(shop, 'rating_count', 0) or 0),
    }


def normalize_availability_rules_value(value):
    normalized = []
    if not isinstance(value, list):
        return normalized
    for rule in value:
        if not rule or not isinstance(rule, dict):
            continue
        scope_candidate = str(rule.get("scope", "day")).lower()
        scope = scope_candidate if scope_candidate in AVAILABILITY_RULE_SCOPES else "day"
        targets = _normalize_string_list(rule.get("targets"))
        times = _normalize_time_list(rule.get("times"))
        if not times:
            continue
        normalized.append({"scope": scope, "targets": targets, "times": times})
    return normalized

def _parse_list_field(value):
    if value is None:
        return []
    if isinstance(value, str):
        return [segment.strip() for segment in value.split(',') if segment.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()]

def _parse_json_field(value, default=None):
    fallback = default() if callable(default) else default
    if fallback is None:
        fallback = {}
    if value is None:
        return fallback
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return fallback
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return value
    return fallback


def _querydict_to_serializer_data(data: QueryDict, list_keys: set[str] | None = None):
    serialized = {}
    configured_list_keys = set(list_keys or set())
    for key in data.keys():
        values = data.getlist(key)
        if key in configured_list_keys:
            serialized[key] = [item for item in values if str(item or '').strip()]
            continue
        serialized[key] = values[-1] if values else None
    return serialized


CATEGORY_SELECTION_LIMIT = 5


def _normalize_landing_string(value, default=''):
    if value is None:
        return default
    return str(value)


def _normalize_landing_image_string(value, default=''):
    return normalize_image_payload(_normalize_landing_string(value, default))


def _normalize_landing_list(value):
    return value if isinstance(value, list) else []


def _normalize_landing_dict(value):
    return value if isinstance(value, dict) else {}


def _extract_landing_section_data(sections, section_type):
    for section in sections or []:
        if not isinstance(section, dict):
            continue
        if section.get('type') != section_type:
            continue
        return _normalize_landing_dict(section.get('data'))
    return {}


def _normalize_landing_testimonials(items):
    normalized = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        quote = str(item.get('quote') or '').strip()
        author = str(item.get('author') or '').strip()
        role = str(item.get('role') or '').strip()
        rating_raw = item.get('rating')
        rating = None
        if rating_raw not in (None, ''):
            try:
                rating = max(1, min(5, int(rating_raw)))
            except (TypeError, ValueError):
                rating = None
        if not quote and not author and not role:
            continue
        normalized.append({
            'id': str(item.get('id') or uuid.uuid4()),
            'quote': quote,
            'author': author,
            'role': role,
            'rating': rating,
            'sort_order': int(item.get('sort_order') or len(normalized)),
        })
    return normalized


def _build_landing_builder_payload(landing_page, request=None):
    raw_builder = deepcopy(getattr(landing_page, 'builder_data', {}) or {})
    builder = raw_builder if isinstance(raw_builder, dict) else {}
    sections = _normalize_landing_list(builder.get('sections'))
    hero = _normalize_landing_dict(builder.get('hero'))
    about_section = _extract_landing_section_data(sections, 'about')
    gallery_section = _extract_landing_section_data(sections, 'image_gallery_grid')
    contact_section = _extract_landing_section_data(sections, 'contact_information')
    testimonials_section = _extract_landing_section_data(sections, 'testimonials')

    normalized_testimonials = _normalize_landing_testimonials(
        builder.get('testimonials') or testimonials_section.get('items')
    )
    if not normalized_testimonials:
        normalized_testimonials = ShopLandingTestimonialSerializer(
            landing_page.testimonials.all(),
            many=True,
        ).data

    hero_image = absolutize_backend_media(_normalize_landing_string(
        hero.get('imageUrl')
        or hero.get('image_url')
        or hero.get('image')
        or builder.get('hero_image_url')
        or landing_page.hero_image_url
    ), request=request)
    hero_cta_text = _normalize_landing_string(
        hero.get('ctaLabel')
        or hero.get('ctaText')
        or hero.get('cta_label')
        or builder.get('hero_cta_text')
        or builder.get('hero_cta_label')
        or landing_page.hero_cta_text
        or 'View shop'
    )
    hero_cta_url = _normalize_landing_string(
        hero.get('ctaUrl')
        or hero.get('ctaLink')
        or hero.get('cta_url')
        or builder.get('hero_cta_url')
        or builder.get('hero_cta_link')
        or landing_page.hero_cta_url
    )
    hero_title = _normalize_landing_string(
        hero.get('title')
        or builder.get('headline')
        or landing_page.headline
        or getattr(landing_page.shop, 'name', 'Shop')
    )
    hero_slogan = _normalize_landing_string(
        hero.get('slogan')
        or hero.get('subtitle')
        or builder.get('subheadline')
        or landing_page.subheadline
        or getattr(landing_page.shop, 'description', '')
    )

    gallery = _normalize_landing_list(builder.get('gallery'))
    if not gallery:
        gallery = _normalize_landing_list(gallery_section.get('images'))

    about = _normalize_landing_string(
        builder.get('about')
        or about_section.get('description')
    )
    contact = _normalize_landing_dict(builder.get('contact'))
    if not contact:
        contact = {
            'phone': _normalize_landing_string(contact_section.get('phone')),
            'email': _normalize_landing_string(contact_section.get('email')),
            'address': _normalize_landing_string(contact_section.get('address')),
        }

    return {
        **builder,
        'id': str(landing_page.id),
        'headline': _normalize_landing_string(landing_page.headline or hero_title),
        'subheadline': _normalize_landing_string(landing_page.subheadline or hero_slogan),
        'hero_image_url': hero_image,
        'hero_cta_text': _normalize_landing_string(landing_page.hero_cta_text or hero_cta_text),
        'hero_cta_url': _normalize_landing_string(landing_page.hero_cta_url or hero_cta_url),
        'is_public': bool(landing_page.is_public),
        'is_published': bool(landing_page.is_published),
        'hero': {
            **hero,
            'title': hero_title,
            'slogan': hero_slogan,
            'imageUrl': hero_image,
            'ctaLabel': hero_cta_text,
            'ctaText': hero_cta_text,
            'ctaUrl': hero_cta_url,
            'ctaLink': hero_cta_url,
        },
        'about': about,
        'gallery': gallery,
        'contact': {
            'phone': _normalize_landing_string(contact.get('phone')),
            'email': _normalize_landing_string(contact.get('email')),
            'address': _normalize_landing_string(contact.get('address')),
        },
        'sections': sections,
        'testimonials': normalized_testimonials,
        'servicesVisibility': _normalize_landing_dict(builder.get('servicesVisibility')),
        'staffDisplayEnabled': bool(builder.get('staffDisplayEnabled', True)),
        'certifications': _normalize_landing_list(builder.get('certifications')),
        'faqs': _normalize_landing_list(builder.get('faqs')),
        'seo': _normalize_landing_dict(builder.get('seo')),
        'socialLinks': _normalize_landing_list(builder.get('socialLinks')),
        'emergencyBanner': _normalize_landing_dict(builder.get('emergencyBanner')),
        'operatingHours': _normalize_landing_list(builder.get('operatingHours')),
        'pricingVisibilityEnabled': bool(builder.get('pricingVisibilityEnabled', True)),
        'landingBackgroundImageUrl': _normalize_landing_string(builder.get('landingBackgroundImageUrl')),
        'landingBackgroundColorKey': _normalize_landing_string(builder.get('landingBackgroundColorKey')),
        'landingLogoUrl': _normalize_landing_string(builder.get('landingLogoUrl')),
    }


def _normalize_category_id_list(value):
    if value is None:
        return []
    iterable = value if isinstance(value, (list, tuple)) else [value]
    normalized = []
    seen = set()
    for token in iterable:
        candidate = str(token or '').strip()
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        normalized.append(candidate)
    return normalized


def _resolve_catalog_categories(category_ids, category_type):
    if not category_ids:
        return []
    categories = list(CatalogCategory.objects.filter(id__in=category_ids, category_type=category_type))
    if len(categories) != len(set(category_ids)):
        return None
    lookup = {str(cat.id): cat for cat in categories}
    ordered = [lookup[cid] for cid in category_ids if cid in lookup]
    return ordered


def _generate_shop_slug(name: str | None) -> str:
    base = slugify((name or '').strip()) or 'shop'
    slug = base
    suffix = 1
    while Shop.objects.filter(slug=slug).exists():
        slug = f"{base}-{suffix}"
        suffix += 1
    return slug


def _generate_product_slug(name: str | None) -> str:
    base = slugify((name or '').strip()) or 'product'
    slug = base
    suffix = 1
    while Product.objects.filter(slug=slug).exists():
        slug = f"{base}-{suffix}"
        suffix += 1
    return slug


def _generate_product_sku() -> str:
    while True:
        sku = f"PRD-{uuid.uuid4().hex[:8].upper()}"
        if not Product.objects.filter(sku=sku).exists():
            return sku


def _decimal_from_value(value):
    if value in (None, ''):
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, TypeError, ValueError):
        return None


class CatalogCategorySerializer(serializers.ModelSerializer):
    parent_id = serializers.UUIDField(read_only=True)
    parent_slug = serializers.SerializerMethodField()
    parent_name = serializers.SerializerMethodField()

    class Meta:
        model = CatalogCategory
        fields = (
            'id',
            'slug',
            'name',
            'description',
            'category_type',
            'parent_id',
            'parent_slug',
            'parent_name',
            'sort_order',
        )

    def get_parent_slug(self, obj):
        parent = getattr(obj, 'parent', None)
        return getattr(parent, 'slug', None)

    def get_parent_name(self, obj):
        parent = getattr(obj, 'parent', None)
        return getattr(parent, 'name', None)


class ProductImageSerializer(serializers.ModelSerializer):
    image_url = serializers.SerializerMethodField()

    class Meta:
        model = ProductImage
        fields = ('id', 'image_url', 'sort_order')

    def get_image_url(self, obj):
        try:
            url = obj.image_file.url
        except ValueError:
            return ''
        request = self.context.get('request')
        return absolutize_backend_media(url, request=request)


class ServiceImageSerializer(serializers.ModelSerializer):
    image_url = serializers.SerializerMethodField()

    class Meta:
        model = ShopServiceImage
        fields = ('id', 'image_url', 'order')

    def get_image_url(self, obj):
        try:
            url = obj.image_file.url
        except ValueError:
            return ''
        request = self.context.get('request')
        return absolutize_backend_media(url, request=request)


class ShopTeamMemberSerializer(serializers.ModelSerializer):
    user_details = serializers.SerializerMethodField()
    role_display = serializers.CharField(source='get_role_display', read_only=True)
    is_current_user = serializers.SerializerMethodField()

    class Meta:
        model = ShopTeamMember
        fields = (
            'id',
            'shop',
            'user',
            'user_details',
            'role',
            'role_display',
            'is_active',
            'created_at',
            'updated_at',
            'is_current_user',
        )
        read_only_fields = ('created_at', 'updated_at')

    def get_user_details(self, obj):
        user = getattr(obj, 'user', None)
        if not user:
            return None
        return {
            'id': str(user.id),
            'display_name': getattr(user, 'display_name', '') or getattr(user, 'username', '') or getattr(user, 'phone', ''),
            'phone': getattr(user, 'phone', None),
            'email': getattr(user, 'email', None),
        }

    def get_is_current_user(self, obj):
        request = self.context.get('request')
        if not request or not request.user or not request.user.is_authenticated:
            return False
        return obj.user_id == request.user.id


class ProductRatingSerializer(serializers.ModelSerializer):
    user = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = ProductRating
        fields = ('id', 'product', 'user', 'score', 'created_at', 'updated_at')

    def validate_score(self, value):
        if value < 1 or value > 5:
            raise serializers.ValidationError('Score must be between 1 and 5.')
        return value


class ProductReviewSerializer(serializers.ModelSerializer):
    user = serializers.SerializerMethodField()
    product_name = serializers.CharField(source='product.name', read_only=True)

    class Meta:
        model = ProductReview
        fields = (
            'id',
            'product',
            'product_name',
            'user',
            'rating',
            'title',
            'body',
            'status',
            'helpful_count',
            'created_at',
            'updated_at',
        )
        read_only_fields = ('id', 'user', 'status', 'helpful_count', 'created_at', 'updated_at')

    def get_user(self, obj):
        return _public_user_summary(getattr(obj, 'user', None))

    def validate_rating(self, value):
        if value < 1 or value > 5:
            raise serializers.ValidationError('Rating must be between 1 and 5.')
        return value

    def validate(self, attrs):
        title = str(attrs.get('title') or '').strip()
        body = str(attrs.get('body') or '').strip()
        if not title and not body:
            raise serializers.ValidationError({'body': 'Share a short review before submitting.'})
        return attrs


class ProductQuestionSerializer(serializers.ModelSerializer):
    user = serializers.SerializerMethodField()
    answered_by = serializers.SerializerMethodField()
    product_name = serializers.CharField(source='product.name', read_only=True)

    class Meta:
        model = ProductQuestion
        fields = (
            'id',
            'product',
            'product_name',
            'user',
            'question',
            'answer',
            'answered_by',
            'answered_at',
            'status',
            'created_at',
            'updated_at',
        )
        read_only_fields = ('id', 'user', 'answer', 'answered_by', 'answered_at', 'status', 'created_at', 'updated_at')

    def get_user(self, obj):
        return _public_user_summary(getattr(obj, 'user', None))

    def get_answered_by(self, obj):
        return _public_user_summary(getattr(obj, 'answered_by', None))

    def validate_question(self, value):
        normalized = str(value or '').strip()
        if len(normalized) < 4:
            raise serializers.ValidationError('Ask a clear product question.')
        return normalized


class ShopLandingTestimonialSerializer(serializers.ModelSerializer):
    class Meta:
        model = ShopLandingTestimonial
        fields = ('id', 'quote', 'author', 'role', 'rating', 'sort_order')


class ShopLandingPageSerializer(serializers.Serializer):
    def to_representation(self, instance):
        return _build_landing_builder_payload(instance, request=self.context.get('request'))


class LandingPageField(serializers.Field):
    def to_representation(self, value):
        if not value:
            return {}
        return ShopLandingPageSerializer(value, context=getattr(self.parent, 'context', {})).data

    def to_internal_value(self, data):
        if data is None:
            return None
        if not isinstance(data, dict):
            raise serializers.ValidationError('Landing page data must be an object.')
        return data


class LandingVisibilityField(serializers.BooleanField):
    def __init__(self, attr_name, **kwargs):
        self.attr_name = attr_name
        kwargs.setdefault('required', False)
        kwargs.setdefault('default', None)
        super().__init__(**kwargs)

    def get_attribute(self, instance):
        return instance

    def to_representation(self, shop):
        landing_page = getattr(shop, 'landing_page', None)
        if not landing_page:
            return False
        return bool(getattr(landing_page, self.attr_name, False))

class ShopSerializer(serializers.ModelSerializer):
    image_file = serializers.ImageField(required=False, allow_null=True)
    image_url = serializers.SerializerMethodField()
    verification_summary = serializers.SerializerMethodField()
    employee_slots = serializers.IntegerField(min_value=1, default=1)
    team_members = serializers.SerializerMethodField()
    landing_page = LandingPageField(required=False, allow_null=True)
    landing_is_public = LandingVisibilityField(attr_name='is_public')
    landing_is_published = LandingVisibilityField(attr_name='is_published')
    landing_page_is_public = LandingVisibilityField(attr_name='is_public')
    landing_page_is_published = LandingVisibilityField(attr_name='is_published')

    VISIBILITY_FIELDS = (
        'landing_is_public',
        'landing_is_published',
        'landing_page_is_public',
        'landing_page_is_published',
    )

    class Meta:
        model = Shop
        fields = '__all__'
        read_only_fields = (
            'owner',
            'is_verified',
            'verification_status',
            'trust_badges',
            'verification_summary',
            'image_url',
            'slug',
        )

    def get_image_url(self, obj):
        return absolutize_backend_media(obj.image_url, request=self.context.get('request'))

    def get_verification_summary(self, obj):
        from apps.verification.services import current_shop_verification_status

        return current_shop_verification_status(obj)

    def get_team_members(self, obj):
        members = getattr(obj, 'team_members', None)
        if members is None:
            return []
        serializer = ShopTeamMemberSerializer(members.filter(is_active=True), many=True, context=self.context)
        return serializer.data

    def _get_actor(self):
        request = self.context.get('request')
        if request and hasattr(request, 'user') and request.user.is_authenticated:
            return request.user
        return None

    def _ensure_landing_page(self, instance, actor=None):
        landing_page, created = ShopLandingPage.objects.get_or_create(shop=instance)
        if actor:
            if created:
                landing_page.created_by = actor
            landing_page.updated_by = actor
        return landing_page, created

    def _apply_landing_payload(self, landing_page, payload, updated_fields):
        changed = False
        existing_builder = deepcopy(getattr(landing_page, 'builder_data', {}) or {})
        next_builder = existing_builder if isinstance(existing_builder, dict) else {}

        for key, value in payload.items():
            if key in {'is_public', 'is_published'}:
                continue
            next_builder[key] = value

        hero_payload = _normalize_landing_dict(next_builder.get('hero'))
        hero_title = payload.get('headline')
        if hero_title is None:
            hero_title = hero_payload.get('title')
        if hero_title is None:
            hero_title = landing_page.headline

        hero_slogan = payload.get('subheadline')
        if hero_slogan is None:
            hero_slogan = hero_payload.get('slogan') or hero_payload.get('subtitle')
        if hero_slogan is None:
            hero_slogan = landing_page.subheadline

        hero_image_url = payload.get('hero_image_url')
        if hero_image_url is None:
            hero_image_url = (
                hero_payload.get('imageUrl')
                or hero_payload.get('image_url')
                or hero_payload.get('image')
            )
        if hero_image_url is None:
            hero_image_url = landing_page.hero_image_url

        hero_cta_text = payload.get('hero_cta_text')
        if hero_cta_text is None:
            hero_cta_text = (
                hero_payload.get('ctaLabel')
                or hero_payload.get('ctaText')
                or hero_payload.get('cta_label')
            )
        if hero_cta_text is None:
            hero_cta_text = landing_page.hero_cta_text

        hero_cta_url = payload.get('hero_cta_url')
        if hero_cta_url is None:
            hero_cta_url = (
                hero_payload.get('ctaUrl')
                or hero_payload.get('ctaLink')
                or hero_payload.get('cta_url')
            )
        if hero_cta_url is None:
            hero_cta_url = landing_page.hero_cta_url

        normalized_core_fields = {
            'headline': _normalize_landing_string(hero_title),
            'subheadline': _normalize_landing_string(hero_slogan),
            'hero_image_url': _normalize_landing_image_string(hero_image_url),
            'hero_cta_text': _normalize_landing_string(hero_cta_text),
            'hero_cta_url': _normalize_landing_string(hero_cta_url),
        }

        for field, value in normalized_core_fields.items():
            if getattr(landing_page, field, '') != value:
                setattr(landing_page, field, value)
                updated_fields.add(field)
                changed = True

        if next_builder != getattr(landing_page, 'builder_data', {}):
            landing_page.builder_data = next_builder
            updated_fields.add('builder_data')
            changed = True

        for flag in ('is_public', 'is_published'):
            if flag in payload:
                value = bool(payload[flag])
                if getattr(landing_page, flag) != value:
                    setattr(landing_page, flag, value)
                    updated_fields.add(flag)
                    changed = True
        return changed

    def _apply_visibility(self, landing_page, visibility_updates, updated_fields):
        changed = False
        public_value = visibility_updates.get('landing_page_is_public')
        if public_value is None:
            public_value = visibility_updates.get('landing_is_public')
        if public_value is not None and landing_page.is_public != public_value:
            landing_page.is_public = public_value
            updated_fields.add('is_public')
            changed = True
        published_value = visibility_updates.get('landing_page_is_published')
        if published_value is None:
            published_value = visibility_updates.get('landing_is_published')
        if published_value is not None and landing_page.is_published != published_value:
            landing_page.is_published = published_value
            updated_fields.add('is_published')
            changed = True
        return changed

    def update(self, instance, validated_data):
        landing_payload = validated_data.pop('landing_page', None)
        visibility_updates = {key: validated_data.pop(key, None) for key in self.VISIBILITY_FIELDS}
        needs_landing = landing_payload is not None or any(value is not None for value in visibility_updates.values())
        if needs_landing:
            actor = self._get_actor()
            landing_page, created = self._ensure_landing_page(instance, actor)
            updated_fields = set()
            if created and actor:
                updated_fields.add('created_by')
            if landing_payload:
                self._apply_landing_payload(landing_page, landing_payload, updated_fields)
            self._apply_visibility(landing_page, visibility_updates, updated_fields)
            if updated_fields:
                if actor:
                    updated_fields.add('updated_by')
                updated_fields.add('updated_at')
                landing_page.save(update_fields=list(updated_fields))
        return super().update(instance, validated_data)

    def create(self, validated_data):
        if not validated_data.get('slug'):
            validated_data['slug'] = _generate_shop_slug(validated_data.get('name'))
        # landing_page and the four landing visibility fields are virtual
        # (backed by a related ShopLandingPage, not real Shop columns) —
        # update() already pops these before saving; create() previously
        # didn't, so Shop.objects.create() crashed with a TypeError on
        # every request where DRF populated them (which is every request,
        # since the visibility fields default to None rather than being
        # omitted from validated_data).
        landing_payload = validated_data.pop('landing_page', None)
        visibility_updates = {key: validated_data.pop(key, None) for key in self.VISIBILITY_FIELDS}
        shop = super().create(validated_data)
        needs_landing = landing_payload is not None or any(value is not None for value in visibility_updates.values())
        if needs_landing:
            actor = self._get_actor()
            landing_page, created = self._ensure_landing_page(shop, actor)
            updated_fields = set()
            if created and actor:
                updated_fields.add('created_by')
            if landing_payload:
                self._apply_landing_payload(landing_page, landing_payload, updated_fields)
            self._apply_visibility(landing_page, visibility_updates, updated_fields)
            if updated_fields:
                if actor:
                    updated_fields.add('updated_by')
                updated_fields.add('updated_at')
                landing_page.save(update_fields=list(updated_fields))
        return shop


class ShopVerificationRequestSerializer(serializers.ModelSerializer):
    verification_case_id = serializers.SerializerMethodField()
    verification_summary = serializers.SerializerMethodField()

    class Meta:
        model = ShopVerificationRequest
        fields = '__all__'
        read_only_fields = ('status', 'risk_score', 'processed_at', 'verification_case_id', 'verification_summary')

    def validate_documents(self, value):
        from apps.verification.serializers import validate_private_evidence_metadata

        validate_private_evidence_metadata({"documents": value or []})
        return value or []

    def get_verification_case_id(self, obj):
        from apps.verification.services import find_shop_verification_case

        case = find_shop_verification_case(obj)
        return str(case.id) if case else None

    def get_verification_summary(self, obj):
        from apps.verification.services import current_shop_verification_status

        return current_shop_verification_status(obj.shop)


class ProductSerializer(serializers.ModelSerializer):
    ATTRIBUTE_BACKED_FIELDS = (
        "brand",
        "condition",
        "material",
        "fit",
        "size_guide",
        "compare_at_price",
        "available_sizes",
        "available_colors",
        "weight",
        "length",
        "width",
        "height",
        "pickup_available",
        "allow_backorder",
    )

    main_image = serializers.ImageField(required=False, allow_null=True)
    image_url = serializers.SerializerMethodField()
    category = serializers.SerializerMethodField()
    catalog_categories = CatalogCategorySerializer(many=True, read_only=True)

    category_id = serializers.UUIDField(write_only=True, required=False, allow_null=True)
    category_ids = serializers.ListField(
        child=serializers.UUIDField(),
        write_only=True,
        required=False,
        allow_empty=True,
    )
    catalog_category_ids = serializers.ListField(
        child=serializers.UUIDField(),
        write_only=True,
        required=False,
        allow_empty=True,
    )

    gallery_images = ProductImageSerializer(many=True, read_only=True)
    images = serializers.ListField(
        child=serializers.ImageField(),
        write_only=True,
        required=False,
    )

    slug = serializers.CharField(required=False, allow_blank=True)
    sku = serializers.CharField(required=False, allow_blank=True)

    is_broadcasted = serializers.SerializerMethodField()
    broadcast_item_id = serializers.SerializerMethodField()
    currency_display = serializers.SerializerMethodField()
    seller_trust = serializers.SerializerMethodField()
    review_summary = serializers.SerializerMethodField()
    question_summary = serializers.SerializerMethodField()
    fulfillment_summary = serializers.SerializerMethodField()
    recommendation_context = serializers.SerializerMethodField()

    brand = serializers.CharField(required=False, allow_blank=True)
    condition = serializers.CharField(required=False, allow_blank=True)
    sale_price = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, allow_null=True)
    compare_at_price = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, allow_null=True)
    material = serializers.CharField(required=False, allow_blank=True)
    fit = serializers.CharField(required=False, allow_blank=True)
    size_guide = serializers.CharField(required=False, allow_blank=True)

    available_sizes = serializers.ListField(
        child=serializers.CharField(max_length=64),
        required=False,
        allow_empty=True,
    )
    available_colors = serializers.ListField(
        child=serializers.CharField(max_length=64),
        required=False,
        allow_empty=True,
    )

    weight = serializers.DecimalField(max_digits=10, decimal_places=2, required=False, allow_null=True)
    length = serializers.DecimalField(max_digits=10, decimal_places=2, required=False, allow_null=True)
    width = serializers.DecimalField(max_digits=10, decimal_places=2, required=False, allow_null=True)
    height = serializers.DecimalField(max_digits=10, decimal_places=2, required=False, allow_null=True)

    low_stock_threshold = serializers.IntegerField(required=False, min_value=0, allow_null=True)
    requires_shipping = serializers.BooleanField(required=False)
    pickup_available = serializers.BooleanField(required=False)
    allow_backorder = serializers.BooleanField(required=False)

    def to_internal_value(self, data):
        mutable = data
        if isinstance(data, QueryDict):
            normalized: dict[str, Any] = {}
            for key in data.keys():
                values = data.getlist(key)
                if not values:
                    continue
                if len(values) == 1:
                    normalized[key] = values[0]
                else:
                    normalized[key] = values
            mutable = normalized
        elif isinstance(data, dict):
            mutable = data.copy()
        if isinstance(mutable, dict) and 'image_file' in mutable and 'main_image' not in mutable:
            mutable['main_image'] = mutable.pop('image_file')
        return super().to_internal_value(mutable)

    attributes = serializers.JSONField(required=False)
    variants = serializers.JSONField(required=False)

    class Meta:
        model = Product
        fields = '__all__'
        read_only_fields = (
            'ai_score',
            'authenticity_status',
            'authenticity_proof',
            'image_url',
            'is_broadcasted',
            'broadcast_item_id',
            'currency',
            'currency_display',
            'seller_trust',
            'review_summary',
            'question_summary',
            'fulfillment_summary',
            'recommendation_context',
        )

    def get_currency_display(self, obj):
        return 'USD'

    def get_seller_trust(self, obj):
        return _commerce_trust_summary(getattr(obj, 'shop', None))

    def get_review_summary(self, obj):
        reviews = getattr(obj, 'reviews', None)
        ratings = getattr(obj, 'ratings', None)
        published_reviews = reviews.filter(status=ProductReview.STATUS_PUBLISHED, is_deleted=False) if reviews is not None else []
        try:
            review_count = published_reviews.count()
            avg_rating = sum(float(item.rating or 0) for item in published_reviews[:50]) / review_count if review_count else 0
        except Exception:
            review_count = 0
            avg_rating = 0
        if not review_count and ratings is not None:
            try:
                rating_values = list(ratings.values_list('score', flat=True)[:100])
                review_count = len(rating_values)
                avg_rating = sum(float(item or 0) for item in rating_values) / review_count if review_count else 0
            except Exception:
                review_count = 0
                avg_rating = 0
        return {
            'count': review_count,
            'average': round(avg_rating, 2) if avg_rating else 0,
            'label': f"{round(avg_rating, 1)} stars" if avg_rating else 'No reviews yet',
        }

    def get_question_summary(self, obj):
        questions = getattr(obj, 'questions', None)
        if questions is None:
            return {'count': 0, 'answered_count': 0}
        qs = questions.filter(is_deleted=False).exclude(status=ProductQuestion.STATUS_HIDDEN)
        return {
            'count': qs.count(),
            'answered_count': qs.filter(status=ProductQuestion.STATUS_ANSWERED).count(),
        }

    def get_fulfillment_summary(self, obj):
        attrs = getattr(obj, 'attributes', {}) if isinstance(getattr(obj, 'attributes', None), dict) else {}
        requires_shipping = bool(getattr(obj, 'requires_shipping', True))
        pickup_available = bool(attrs.get('pickup_available') or attrs.get('pickupAvailable') or False)
        delivery_estimate = str(attrs.get('delivery_estimate') or attrs.get('deliveryEstimate') or '').strip()
        return {
            'requires_shipping': requires_shipping,
            'pickup_available': pickup_available,
            'delivery_estimate': delivery_estimate or ('Provider delivery' if requires_shipping else 'Digital or service fulfillment'),
            'stock_status': 'in_stock' if int(getattr(obj, 'stock_qty', 0) or 0) > 0 else 'out_of_stock',
        }

    def get_recommendation_context(self, obj):
        category_names = []
        try:
            category_names = list(obj.catalog_categories.values_list('name', flat=True)[:5])
        except Exception:
            category_names = []
        return {
            'reason': 'Based on seller trust, product category, and availability.',
            'signals': {
                'featured': bool(getattr(obj, 'is_featured', False)),
                'verified_seller': bool(getattr(getattr(obj, 'shop', None), 'is_verified', False)),
                'categories': category_names,
            },
        }

    def get_image_url(self, obj):
        request = self.context.get('request')
        url_candidate = ''

        main_image = getattr(obj, 'main_image', None)
        if main_image:
            try:
                url_candidate = main_image.url or ''
            except (ValueError, AttributeError):
                url_candidate = ''

        if not url_candidate:
            first_image = self._get_first_gallery_image(obj)
            if first_image:
                try:
                    url_candidate = first_image.image_file.url or ''
                except (ValueError, AttributeError):
                    url_candidate = ''

        return absolutize_backend_media(url_candidate, request=request)

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data['currency'] = 'USD'
        data['currency_display'] = 'USD'
        attributes = getattr(instance, "attributes", None)
        if not isinstance(attributes, dict):
            return data

        for field in self.ATTRIBUTE_BACKED_FIELDS:
            current = data.get(field)
            fallback = attributes.get(field)
            if field in {"available_sizes", "available_colors"}:
                if not current and fallback not in (None, "", []):
                    data[field] = fallback if isinstance(fallback, list) else _parse_list_field(fallback)
                continue
            if current in (None, "", []):
                data[field] = fallback

        return data

    def _get_first_gallery_image(self, obj):
        if not hasattr(obj, 'gallery_images'):
            return None
        try:
            return obj.gallery_images.filter(is_active=True).order_by('sort_order', 'id').first()
        except ProgrammingError as exc:
            logger.warning(
                "Unable to fetch gallery images for product %s (missing column?): %s",
                getattr(obj, 'id', None),
                exc,
            )
            return None

    def get_category(self, obj):
        first_category = (
            obj.catalog_categories.order_by('name').first()
            if hasattr(obj, 'catalog_categories') else None
        )
        if not first_category:
            return None
        serializer = CatalogCategorySerializer(first_category, context=self.context)
        return serializer.data

    def validate(self, attrs):
        try:
            attrs = super().validate(attrs)

            category_id = attrs.pop("category_id", None)
            raw_category_ids = attrs.pop("category_ids", None)
            raw_catalog_category_ids = attrs.pop("catalog_category_ids", None)

            normalized_ids = _normalize_category_id_list(raw_catalog_category_ids)
            legacy_ids = _normalize_category_id_list(raw_category_ids)

            for item in legacy_ids:
                if item not in normalized_ids:
                    normalized_ids.append(item)

            if category_id:
                primary = str(category_id or '').strip()
                if primary and primary not in normalized_ids:
                    normalized_ids.insert(0, primary)

            initial_keys = []
            if hasattr(self.initial_data, "keys"):
                initial_keys = list(self.initial_data.keys())
            elif isinstance(self.initial_data, dict):
                initial_keys = list(self.initial_data.keys())

            category_ids_provided = any(
                key in {'category_ids', 'categoryIds', 'catalog_category_ids', 'catalogCategoryIds'}
                for key in initial_keys
            )

            catalog_categories = None
            if normalized_ids:
                if len(normalized_ids) > CATEGORY_SELECTION_LIMIT:
                    raise serializers.ValidationError({
                        "catalog_category_ids": f"Select up to {CATEGORY_SELECTION_LIMIT} categories."
                    })
                resolved = _resolve_catalog_categories(normalized_ids, 'product')
                if resolved is None:
                    raise serializers.ValidationError({
                        "catalog_category_ids": "One or more selected categories are invalid."
                    })
                catalog_categories = resolved
            elif category_ids_provided:
                catalog_categories = []

            attrs["_catalog_categories"] = catalog_categories
            attrs = self._hydrate_extended_attributes(attrs)

            attrs["currency"] = "USD"

            sanitized_attributes = self._sanitize_attributes(attrs.get("attributes"))
            attrs["attributes"] = self._serialize_json_value(sanitized_attributes)

            sanitized_variants = self._sanitize_variants(attrs.get("variants"))
            attrs["variants"] = sanitized_variants

            inventory_type = attrs.get("inventory_type") or getattr(self.instance, "inventory_type", None)
            requires_shipping = attrs.get("requires_shipping")
            if requires_shipping is None and self.instance is not None:
                requires_shipping = getattr(self.instance, "requires_shipping", None)

            if inventory_type in ["DIGITAL", "SERVICE"]:
                attrs["requires_shipping"] = False
            elif requires_shipping is not None:
                attrs["requires_shipping"] = bool(requires_shipping)

            price = attrs.get("price", getattr(self.instance, "price", None))
            sale_price = attrs.get("sale_price", getattr(self.instance, "sale_price", None))
            if price is not None and sale_price is not None and Decimal(str(sale_price)) > Decimal(str(price)):
                raise serializers.ValidationError({
                    "sale_price": "Sale price cannot be greater than price."
                })

            # Payment-readiness gate — is_active is commerce's actual
            # "published" flag (there's no separate draft state on
            # Product), so a shop can always save an inactive/draft
            # product regardless of price; only an active, priced listing
            # requires the shop to actually be able to receive money. This
            # is a UX-layer check (tell the seller at listing time, not
            # just at the buyer's checkout) — the real backstop that makes
            # this unbypassable lives in
            # apps.billing.direct_payments.create_direct_payment_intent.
            shop = attrs.get("shop", getattr(self.instance, "shop", None))
            is_active = attrs.get("is_active", getattr(self.instance, "is_active", True))
            if shop is not None and is_active and price is not None and Decimal(str(price)) > 0:
                from apps.billing.eligibility import PaymentSetupRequiredError, can_receive_payments

                eligibility = can_receive_payments(shop)
                if not eligibility.eligible:
                    raise PaymentSetupRequiredError(eligibility)

            return attrs
        except serializers.ValidationError as exc:
            logger.warning(
                "Product validation failed user=%s keys=%s errors=%s",
                getattr(self.context.get("request"), "user", None),
                list(self.initial_data.keys()) if hasattr(self.initial_data, "keys") else [],
                exc.detail,
            )
            raise

    def _sanitize_attributes(self, value):
        if not isinstance(value, dict):
            return value

        mapping = {
            "bookingWindow": "booking_window",
            "serviceType": "service_type",
            "otherShopsDiscount": "other_shops_discount",
            "availabilityRules": "availability_rules",
            "brand": "brand",
            "condition": "condition",
            "material": "material",
            "fit": "fit",
            "sizeGuide": "size_guide",
            "size_guide": "size_guide",
            "salePrice": "sale_price",
            "sale_price": "sale_price",
            "compareAtPrice": "compare_at_price",
            "compare_at_price": "compare_at_price",
            "availableSizes": "available_sizes",
            "available_sizes": "available_sizes",
            "availableColors": "available_colors",
            "available_colors": "available_colors",
            "weight": "weight",
            "length": "length",
            "width": "width",
            "height": "height",
            "lowStockThreshold": "low_stock_threshold",
            "low_stock_threshold": "low_stock_threshold",
            "requiresShipping": "requires_shipping",
            "requires_shipping": "requires_shipping",
            "pickupAvailable": "pickup_available",
            "pickup_available": "pickup_available",
            "allowBackorder": "allow_backorder",
            "allow_backorder": "allow_backorder",
        }

        sanitized = {}
        for key, val in value.items():
            if key == "duration":
                continue

            target_key = mapping.get(key, key)

            if target_key == "availability_rules":
                normalized_rules = self._normalize_availability_rules(val)
                if normalized_rules:
                    sanitized[target_key] = normalized_rules
                continue

            candidate = val.strip() if isinstance(val, str) else val
            if candidate is None or candidate == '':
                continue

            sanitized[target_key] = candidate

        return sanitized

    def _sanitize_variants(self, value):
        value = _parse_json_field(value, default=list)
        if not isinstance(value, list):
            return []

        sanitized = []
        for item in value:
            if not isinstance(item, dict):
                continue

            variant_id = str(item.get("id") or "").strip() or _generate_product_sku()
            name = str(item.get("name") or "").strip()
            sku = str(item.get("sku") or "").strip()
            options = item.get("options") if isinstance(item.get("options"), dict) else {}

            cleaned_options = {}
            for key, val in options.items():
                option_key = str(key or "").strip()
                option_val = str(val or "").strip()
                if option_key:
                    cleaned_options[option_key] = option_val

            price = _decimal_from_value(item.get("price"))
            sale_price = _decimal_from_value(item.get("sale_price"))
            stock_qty = self._normalize_variant_stock(item.get("stock_qty"))
            is_active = bool(item.get("is_active", True))

            sanitized.append({
                "id": variant_id,
                "name": name,
                "sku": sku,
                "price": str(price.quantize(Decimal('0.01'))) if price is not None else "0.00",
                "sale_price": str(sale_price.quantize(Decimal('0.01'))) if sale_price is not None else None,
                "stock_qty": stock_qty,
                "is_active": is_active,
                "options": cleaned_options,
            })

        return sanitized

    def _hydrate_extended_attributes(self, attrs):
        attributes = _parse_json_field(attrs.pop("attributes", None), default=dict)

        def patch_value(field, caster=lambda v: v):
            attr_value = attributes.get(field)
            current = attrs.get(field)
            if current in (None, '', []):
                if attr_value not in (None, '', []):
                    attrs[field] = caster(attr_value)
            else:
                attributes[field] = attrs[field]

        patch_value("brand", str)
        patch_value("condition", str)
        patch_value("material", str)
        patch_value("fit", str)
        patch_value("size_guide", str)
        patch_value("sale_price", lambda v: v)
        patch_value("compare_at_price", lambda v: v)
        patch_value("weight", lambda v: v)
        patch_value("length", lambda v: v)
        patch_value("width", lambda v: v)
        patch_value("height", lambda v: v)
        patch_value("low_stock_threshold", lambda v: v)
        patch_value("requires_shipping", lambda v: v)
        patch_value("pickup_available", lambda v: v)
        patch_value("allow_backorder", lambda v: v)

        for list_field in ("available_sizes", "available_colors"):
            list_value = attrs.get(list_field)
            if list_value in (None, '', []):
                attr_value = attributes.get(list_field)
                if attr_value not in (None, '', []):
                    if isinstance(attr_value, list):
                        attrs[list_field] = attr_value
                    else:
                        attrs[list_field] = _parse_list_field(attr_value)
            else:
                attributes[list_field] = list_value

        attrs["attributes"] = attributes
        return attrs

    def _normalize_string_list(self, value):
        items = []
        if isinstance(value, list):
            items = value
        elif isinstance(value, str):
            items = value.split(",")
        else:
            return []
        return [str(item).strip() for item in items if str(item or "").strip()]

    def _normalize_time_list(self, value):
        normalized = []
        for token in self._normalize_string_list(value):
            match = TIME_PATTERN.match(token)
            if match:
                hour = int(match.group(1))
                minute = int(match.group(2))
                normalized.append(f"{hour:02d}:{minute:02d}")
        return normalized

    def _normalize_availability_rules(self, value):
        return normalize_availability_rules_value(value)

    def _normalize_variant_stock(self, value):
        try:
            numeric = int(Decimal(str(value)))
            return max(0, numeric)
        except (ValueError, TypeError, ArithmeticError):
            return 0

    def _serialize_json_value(self, value):
        if value is None or isinstance(value, (str, bool, int, float)):
            return value
        if isinstance(value, Decimal):
            return str(value)
        if isinstance(value, list):
            return [self._serialize_json_value(item) for item in value]
        if isinstance(value, dict):
            return {key: self._serialize_json_value(item) for key, item in value.items()}
        return str(value)

    def _get_market_broadcast_item(self, obj: Product):
        existing = getattr(obj, '_market_broadcast_item', None)
        if existing is not None:
            if existing.is_deleted or (
                getattr(existing, "expires_at", None) is not None
                and existing.expires_at <= timezone.now()
            ):
                existing = None
                setattr(obj, "_market_broadcast_item", None)
            else:
                return existing

        from apps.broadcasts.models import BroadcastItem, BroadcastSourceType

        item = (
            BroadcastItem.objects.filter(
                source_type=BroadcastSourceType.MARKET_PRODUCT,
                source_id=str(obj.id),
                is_deleted=False,
            )
            .order_by('-broadcasted_at')
            .first()
        )
        setattr(obj, '_market_broadcast_item', item)
        return item

    def get_is_broadcasted(self, obj: Product):
        return bool(self._get_market_broadcast_item(obj))

    def get_broadcast_item_id(self, obj: Product):
        item = self._get_market_broadcast_item(obj)
        return str(item.id) if item else None

    def validate_currency(self, value):
        normalized = str(value or '').strip().upper()
        if normalized and normalized != "USD":
            raise serializers.ValidationError("Currency must be USD.")
        return "USD"

    def create(self, validated_data):
        gallery_images = validated_data.pop("images", None)
        catalog_categories = validated_data.pop("_catalog_categories", None)
        validated_data.pop("category_id", None)
        validated_data.pop("category_ids", None)
        validated_data.pop("catalog_category_ids", None)
        for field in self.ATTRIBUTE_BACKED_FIELDS:
            validated_data.pop(field, None)

        if not validated_data.get("slug"):
            validated_data["slug"] = _generate_product_slug(validated_data.get("name"))
        if not validated_data.get("sku"):
            validated_data["sku"] = _generate_product_sku()

        product = super().create(validated_data)

        if catalog_categories is not None:
            product.catalog_categories.set(catalog_categories)

        self._save_gallery_images(product, gallery_images)

        return product

    def update(self, instance, validated_data):
        gallery_images = validated_data.pop("images", None)
        catalog_categories = validated_data.pop("_catalog_categories", None)
        validated_data.pop("category_id", None)
        validated_data.pop("category_ids", None)
        validated_data.pop("catalog_category_ids", None)
        for field in self.ATTRIBUTE_BACKED_FIELDS:
            validated_data.pop(field, None)

        product = super().update(instance, validated_data)

        if catalog_categories is not None:
            product.catalog_categories.set(catalog_categories)

        self._save_gallery_images(product, gallery_images)

        return product

    def _save_gallery_images(self, product, images):
        if not images:
            return
        max_sort = product.gallery_images.aggregate(max_sort=Max("sort_order")).get("max_sort")
        next_sort = (max_sort or 0) + 1
        for image_file in images:
            ProductImage.objects.create(
                product=product,
                image_file=image_file,
                sort_order=next_sort,
            )
            next_sort += 1


class ProductAuthenticityCheckSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductAuthenticityCheck
        fields = '__all__'
        read_only_fields = ('status', 'result', 'confidence', 'checked_at')


class OrderItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrderItem
        fields = '__all__'


class OrderSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(many=True)

    class Meta:
        model = Order
        fields = '__all__'
        read_only_fields = ('status','subtotal','total')

    def create(self, validated_data):
        items_data = validated_data.pop('items')
        order = Order.objects.create(**validated_data)
        subtotal = 0
        for item in items_data:
            OrderItem.objects.create(order=order, **item)
            subtotal += float(item['unit_price']) * int(item.get('quantity',1))
        order.subtotal = subtotal
        order.total = subtotal + float(order.tax) + float(order.shipping) - float(order.discount_amount)
        order.save()
        return order


class PaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Payment
        fields = '__all__'


class PromotionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Promotion
        fields = '__all__'


class SubscriptionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Subscription
        fields = '__all__'


class LoyaltyPointSerializer(serializers.ModelSerializer):
    class Meta:
        model = LoyaltyPoint
        fields = '__all__'


class ShopFollowSerializer(serializers.ModelSerializer):
    user_details = serializers.SerializerMethodField()

    class Meta:
        model = ShopFollow
        fields = ('id', 'shop', 'user', 'user_details', 'followed_at', 'is_deleted')
        read_only_fields = ('followed_at',)

    def get_user_details(self, obj):
        user = getattr(obj, 'user', None)
        if not user:
            return None
        return {
            'id': str(user.id),
            'display_name': getattr(user, 'display_name', '') or getattr(user, 'username', '') or getattr(user, 'phone', ''),
            'phone': getattr(user, 'phone', None),
            'email': getattr(user, 'email', None),
        }


class ShopServiceSerializer(serializers.ModelSerializer):
    category = serializers.SerializerMethodField()
    catalog_categories = CatalogCategorySerializer(many=True, read_only=True)
    image_url = serializers.SerializerMethodField()
    category_id = serializers.UUIDField(write_only=True, required=False, allow_null=True)
    category_ids = serializers.ListField(
        child=serializers.UUIDField(),
        write_only=True,
        required=False,
        allow_empty=True,
    )
    catalog_category_ids = serializers.ListField(
        child=serializers.UUIDField(),
        write_only=True,
        required=False,
        allow_empty=True,
    )
    images = ServiceImageSerializer(many=True, read_only=True)
    uploaded_images = serializers.ListField(
        child=serializers.ImageField(),
        write_only=True,
        required=False,
        allow_empty=True,
    )
    remove_featured_image = serializers.BooleanField(write_only=True, required=False, default=False)
    remove_image_ids = serializers.ListField(
        child=serializers.UUIDField(),
        write_only=True,
        required=False,
        allow_empty=True,
    )
    is_broadcasted = serializers.SerializerMethodField()
    broadcast_item_id = serializers.SerializerMethodField()
    packages = serializers.JSONField(required=False, default=list)
    addons = serializers.JSONField(required=False, default=list)
    requirements = serializers.JSONField(required=False, default=list)
    delivery_modes = serializers.ListField(child=serializers.CharField(), required=False, default=list)
    coverage = serializers.ListField(child=serializers.CharField(), required=False, default=list)
    remote_regions = serializers.ListField(child=serializers.CharField(), required=False, default=list)
    remote_meeting_link = serializers.CharField(required=False, allow_blank=True)
    seller_trust = serializers.SerializerMethodField()
    service_quality_summary = serializers.SerializerMethodField()
    fulfillment_summary = serializers.SerializerMethodField()

    class Meta:
        model = ShopService
        fields = '__all__'
        read_only_fields = (
            'created_at',
            'updated_at',
            'is_broadcasted',
            'broadcast_item_id',
            'seller_trust',
            'service_quality_summary',
            'fulfillment_summary',
        )
        validators: list = []

    def to_internal_value(self, data):
        normalized_slug = None
        slug_candidate = str(data.get('slug') or data.get('name') or '').strip()
        if slug_candidate:
            normalized_slug = slugify(slug_candidate) or 'service'
        mutable_data = (
            _querydict_to_serializer_data(
                data,
                {
                    'category_ids',
                    'catalog_category_ids',
                    'delivery_modes',
                    'coverage',
                    'remote_regions',
                    'tags',
                    'blackout_dates',
                    'images',
                    'remove_image_ids',
                },
            ) if isinstance(data, QueryDict)
            else data.copy() if isinstance(data, dict)
            else dict(data)
        )
        if normalized_slug:
            mutable_data['slug'] = normalized_slug
        slug_candidate = str(mutable_data.get('slug') or mutable_data.get('name') or '').strip()
        if slug_candidate:
            mutable_data['slug'] = slugify(slug_candidate) or 'service'
        mapper = {
            'availability': 'availability',
            'availabilityRules': 'availability_rules',
            'serviceType': 'service_type',
            'otherShopsDiscount': 'other_shops_discount',
            'categoryId': 'category_id',
            'categoryIds': 'category_ids',
            'catalogCategoryIds': 'catalog_category_ids',
            'remoteMeetingLink': 'remote_meeting_link',
            'allowMultipleAttendeesPerSlot': 'allow_multiple_attendees_per_slot',
            'removeFeaturedImage': 'remove_featured_image',
            'removeImageIds': 'remove_image_ids',
        }
        for camel, snake in mapper.items():
            if camel in mutable_data and snake not in mutable_data:
                mutable_data[snake] = mutable_data.pop(camel)
        raw_images = mutable_data.get('images')
        if isinstance(raw_images, list) and any(isinstance(item, UploadedFile) for item in raw_images):
            mutable_data['uploaded_images'] = raw_images
            mutable_data.pop('images', None)
        logger.debug("ShopServiceSerializer payload: %s", mutable_data)
        mutable_data['packages'] = _parse_json_field(mutable_data.get('packages'), default=list)
        mutable_data['addons'] = _parse_json_field(mutable_data.get('addons'), default=list)
        mutable_data['requirements'] = _parse_json_field(mutable_data.get('requirements'), default=list)
        availability_raw = _parse_json_field(mutable_data.get('availability'), default=dict)
        normalized_availability = normalize_availability_payload(availability_raw)
        mutable_data['availability'] = normalized_availability
        rules_raw = _parse_json_field(mutable_data.get('availability_rules'), default=list)
        normalized_rules = normalize_availability_rules_value(rules_raw)
        if not normalized_rules:
            normalized_rules = normalize_availability_rules_value(
                derive_availability_rules_from_payload(normalized_availability)
            )
        mutable_data['availability_rules'] = normalized_rules
        mutable_data['delivery_modes'] = _parse_list_field(mutable_data.get('delivery_modes'))
        mutable_data['coverage'] = _parse_list_field(mutable_data.get('coverage'))
        mutable_data['remote_regions'] = _parse_list_field(mutable_data.get('remote_regions'))
        mutable_data['tags'] = _parse_list_field(mutable_data.get('tags'))
        blackout_dates_raw = mutable_data.get('blackout_dates')
        if isinstance(blackout_dates_raw, str):
            mutable_data['blackout_dates'] = _parse_list_field(blackout_dates_raw)
        return super().to_internal_value(mutable_data)

    def get_category(self, obj):
        first_category = (
            obj.catalog_categories.order_by('name').first()
            if hasattr(obj, 'catalog_categories') else None
        )
        if not first_category:
            return None
        serializer = CatalogCategorySerializer(first_category, context=self.context)
        return serializer.data

    def get_seller_trust(self, obj):
        return _commerce_trust_summary(getattr(obj, 'shop', None))

    def get_service_quality_summary(self, obj):
        return {
            'rating_avg': float(getattr(obj, 'rating_avg', 0) or 0),
            'rating_count': int(getattr(obj, 'rating_count', 0) or 0),
            'label': f"{float(getattr(obj, 'rating_avg', 0) or 0):.1f} stars" if getattr(obj, 'rating_count', 0) else 'No ratings yet',
        }

    def get_fulfillment_summary(self, obj):
        delivery_modes = [str(item) for item in (getattr(obj, 'delivery_modes', []) or []) if str(item).strip()]
        return {
            'delivery_modes': delivery_modes,
            'duration_minutes': int(getattr(obj, 'duration_minutes', 0) or 0),
            'remote_available': any(item.lower() == 'remote' for item in delivery_modes),
            'coverage': getattr(obj, 'coverage', []) or [],
            'turnaround_hours': int(getattr(obj, 'turnaround_hours', 0) or 0),
        }

    def get_image_url(self, obj):
        image_file = getattr(obj, 'image_file', None)
        if image_file:
            try:
                url = image_file.url or ''
            except ValueError:
                url = ''
            if url:
                request = self.context.get('request')
                return absolutize_backend_media(url, request=request)
        stored_url = str(getattr(obj, 'image_url', '') or '').strip()
        if stored_url:
            return absolutize_backend_media(stored_url, request=self.context.get('request'))
        first_image = getattr(obj, 'images', None)
        if first_image is None:
            return ''
        gallery_item = obj.images.order_by('order', 'id').first()
        if gallery_item and getattr(gallery_item, 'image_file', None):
            try:
                url = gallery_item.image_file.url or ''
            except ValueError:
                return ''
            if url:
                request = self.context.get('request')
                return absolutize_backend_media(url, request=request)
        return ''

    def validate_image_url(self, value):
        return normalize_image_payload(value)

    def _generate_unique_slug(self, value: str, shop: Shop | None) -> str:
        base = slugify(value) or 'service'
        candidate = base
        suffix = 1
        while shop and ShopService.objects.filter(shop=shop, slug=candidate).exclude(id=getattr(self.instance, 'id', None)).exists():
            candidate = f"{base}-{suffix}"
            suffix += 1
        return candidate

    def validate(self, attrs):
        shop = attrs.get('shop') or getattr(self.instance, 'shop', None)
        name_value = str(attrs.get('name') or getattr(self.instance, 'name', '')).strip()
        slug_value = str(attrs.get('slug') or getattr(self.instance, 'slug', '')).strip()
        slug_source = slug_value or name_value or ''
        if slug_source:
            normalized = slugify(slug_source) or 'service'
            if shop:
                attrs['slug'] = self._generate_unique_slug(normalized, shop)
            else:
                attrs['slug'] = normalized
        attrs = super().validate(attrs)
        category_id_value = attrs.pop('category_id', None)
        raw_category_ids = attrs.pop('category_ids', None)
        raw_catalog_category_ids = attrs.pop('catalog_category_ids', None)
        normalized_ids = _normalize_category_id_list(raw_catalog_category_ids)
        legacy_ids = _normalize_category_id_list(raw_category_ids)
        if legacy_ids:
            normalized_ids.extend([cid for cid in legacy_ids if cid not in normalized_ids])
        if category_id_value:
            primary = str(category_id_value or '').strip()
            if primary and primary not in normalized_ids:
                normalized_ids.insert(0, primary)
        initial_keys = []
        if hasattr(self.initial_data, 'keys'):
            initial_keys = list(self.initial_data.keys())
        elif isinstance(self.initial_data, dict):
            initial_keys = list(self.initial_data.keys())
        has_category_ids = any(
            key in {'category_ids', 'categoryIds', 'catalog_category_ids', 'catalogCategoryIds'}
            for key in initial_keys
        )
        has_category_id = any(key in {'category_id', 'categoryId'} for key in initial_keys)
        catalog_categories = None
        if normalized_ids:
            if len(normalized_ids) > CATEGORY_SELECTION_LIMIT:
                raise serializers.ValidationError({
                    'category_ids': f'Select up to {CATEGORY_SELECTION_LIMIT} categories.'
                })
            resolved = _resolve_catalog_categories(normalized_ids, 'service')
            if not resolved:
                raise serializers.ValidationError({'category_ids': 'One or more selected categories are invalid.'})
            catalog_categories = resolved
        elif has_category_ids or has_category_id or self.instance is None:
            raise serializers.ValidationError({'category_ids': 'Please select at least one category.'})
        attrs['_catalog_categories'] = catalog_categories
        if 'price' in attrs:
            if attrs['price'] is None or attrs['price'] < 0:
                raise serializers.ValidationError({'price': 'Price must be greater than or equal to zero.'})

        # Payment-readiness gate — 'published' is ShopService's actual
        # publish state (unlike Product, it has a real draft/published/
        # paused status field), so a shop can save a draft/paused service
        # at any price; only a published, priced service requires the
        # shop to actually be able to receive money. UX-layer check; the
        # real backstop lives in
        # apps.billing.direct_payments.create_direct_payment_intent.
        shop = attrs.get('shop', getattr(self.instance, 'shop', None))
        service_status = attrs.get('status', getattr(self.instance, 'status', 'draft'))
        price = attrs.get('price', getattr(self.instance, 'price', None))
        if shop is not None and service_status == 'published' and price is not None and price > 0:
            from apps.billing.eligibility import PaymentSetupRequiredError, can_receive_payments

            eligibility = can_receive_payments(shop)
            if not eligibility.eligible:
                raise PaymentSetupRequiredError(eligibility)

        if 'deposit_percent' in attrs and attrs['deposit_percent'] is not None:
            if attrs['deposit_percent'] < 0 or attrs['deposit_percent'] > 100:
                raise serializers.ValidationError({'deposit_percent': 'Deposit percent must be between 0 and 100.'})
        if 'duration_minutes' in attrs and attrs['duration_minutes'] <= 0:
            raise serializers.ValidationError({'duration_minutes': 'Duration must be greater than zero.'})
        if 'max_bookings_per_slot' in attrs and attrs['max_bookings_per_slot'] < 1:
            raise serializers.ValidationError({'max_bookings_per_slot': 'Max bookings per slot must be at least 1.'})
        if 'travel_radius_km' in attrs and attrs['travel_radius_km'] < 0:
            raise serializers.ValidationError({'travel_radius_km': 'Travel radius must be zero or positive.'})
        if 'status' in attrs and attrs['status'] not in {'draft', 'published', 'paused'}:
            raise serializers.ValidationError({'status': 'Invalid status value.'})
        if 'visibility' in attrs and attrs['visibility'] not in {'public', 'unlisted', 'private', 'draft'}:
            raise serializers.ValidationError({'visibility': 'Invalid visibility value.'})
        delivery_modes = attrs.get('delivery_modes') or getattr(self.instance, 'delivery_modes', [])
        remote_required = any(str(mode).lower() == 'remote' for mode in (delivery_modes or []))
        remote_link = (attrs.get('remote_meeting_link') or getattr(self.instance, 'remote_meeting_link', '') or '').strip()
        if remote_required and not remote_link:
            raise serializers.ValidationError({'remote_meeting_link': 'Remote services must include a meeting link.'})
        return attrs

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data['currency'] = 'USD'
        data['availability'] = normalize_availability_payload(getattr(instance, 'availability', {}))
        if not self._user_can_view_remote_link(instance):
            data.pop('remote_meeting_link', None)
        return data

    def create(self, validated_data):
        gallery_images = validated_data.pop('uploaded_images', None)
        catalog_categories = validated_data.pop('_catalog_categories', None)
        validated_data.pop('remove_featured_image', None)
        validated_data.pop('remove_image_ids', None)
        shop = validated_data.get('shop')
        slug_source = validated_data.get('slug') or validated_data.get('name') or ''
        attempts = 0
        while True:
            try:
                service = super().create(validated_data)
                break
            except IntegrityError as exc:
                if 'shop_slug' not in str(exc).lower() or not shop:
                    raise
                attempts += 1
                fallback_base = f"{slug_source}-{attempts}"
                validated_data['slug'] = self._generate_unique_slug(fallback_base, shop)
        if catalog_categories is not None:
            service.catalog_categories.set(catalog_categories)
        self._save_gallery_images(service, gallery_images)
        return service

    def _get_market_broadcast_item(self, obj: ShopService):
        existing = getattr(obj, '_market_broadcast_item', None)
        if existing is not None:
            return existing
        from apps.broadcasts.models import BroadcastItem, BroadcastSourceType

        item = (
            BroadcastItem.objects.filter(
                source_type=BroadcastSourceType.MARKET_SERVICE,
                source_id=str(obj.id),
                is_deleted=False,
            )
            .order_by('-broadcasted_at')
            .first()
        )
        setattr(obj, '_market_broadcast_item', item)
        return item

    def get_is_broadcasted(self, obj: ShopService):
        return bool(self._get_market_broadcast_item(obj))

    def get_broadcast_item_id(self, obj: ShopService):
        item = self._get_market_broadcast_item(obj)
        return str(item.id) if item else None

    def update(self, instance, validated_data):
        gallery_images = validated_data.pop('uploaded_images', None)
        catalog_categories = validated_data.pop('_catalog_categories', None)
        remove_featured_image = bool(validated_data.pop('remove_featured_image', False))
        remove_image_ids = [str(image_id) for image_id in validated_data.pop('remove_image_ids', [])]
        if remove_featured_image and 'image_file' not in validated_data:
            validated_data['image_file'] = None
            validated_data['image_url'] = ''
        service = super().update(instance, validated_data)
        if catalog_categories is not None:
            service.catalog_categories.set(catalog_categories)
        if remove_image_ids:
            service.images.filter(id__in=remove_image_ids).delete()
        self._save_gallery_images(service, gallery_images)
        return service

    def _save_gallery_images(self, service, images):
        if not images:
            return
        max_order = service.images.aggregate(max_order=Max('order')).get('max_order')
        next_order = (max_order or 0) + 1
        for image_file in images:
            ShopServiceImage.objects.create(
                service=service,
                image_file=image_file,
                order=next_order,
            )
            next_order += 1

    def _user_can_view_remote_link(self, instance: ShopService) -> bool:
        request = self.context.get('request')
        user = getattr(request, 'user', None)
        if not user:
            return False
        if getattr(user, 'is_staff', False):
            return True
        shop = getattr(instance, 'shop', None)
        owner_id = getattr(shop, 'owner_id', None)
        return bool(owner_id and str(owner_id) == str(getattr(user, 'id', None)))


class ServiceRatingSerializer(serializers.ModelSerializer):
    user = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = ServiceRating
        fields = ('id', 'service', 'user', 'score', 'created_at', 'updated_at')

    def validate_score(self, value):
        if value < 1 or value > 5:
            raise serializers.ValidationError('Score must be between 1 and 5.')
        return value


class ServiceBookingPaymentSerializer(serializers.ModelSerializer):
    amount_usd_label = serializers.SerializerMethodField()
    payment_provider = serializers.SerializerMethodField()
    payment_required = serializers.SerializerMethodField()
    payment_intent_id = serializers.SerializerMethodField()
    direct_payment_intent_id = serializers.SerializerMethodField()
    payment_reference = serializers.SerializerMethodField()
    payment_url = serializers.SerializerMethodField()
    currency_label = serializers.SerializerMethodField()

    class Meta:
        model = ServiceBookingPayment
        fields = (
            'id',
            'booking',
            'amount_cents',
            'amount_usd_label',
            'currency',
            'currency_label',
            'payment_method',
            'payment_provider',
            'payment_required',
            'payment_intent_id',
            'direct_payment_intent_id',
            'payment_reference',
            'payment_url',
            'payment_status',
            'paid_at',
            'transaction_reference',
            'notes',
            'satisfied_at',
            'created_at',
            'updated_at',
        )
        read_only_fields = fields

    def get_amount_usd_label(self, obj):
        return _usd_label_from_cents(obj.amount_cents)

    def get_payment_provider(self, obj):
        method = str(obj.payment_method or '').strip().lower()
        return 'legacy_wallet' if method in {'wallet', 'kisc', 'kis_wallet'} else (method or 'flutterwave')

    def get_payment_required(self, obj):
        return str(obj.payment_status or '').strip().lower() == ServiceBookingPayment.STATUS_PENDING

    def get_payment_intent_id(self, obj):
        metadata = getattr(getattr(obj, 'booking', None), 'metadata', None)
        return str((metadata or {}).get('direct_payment_intent_id') or '') or None

    def get_direct_payment_intent_id(self, obj):
        # The `direct_payment_intent_id` SerializerMethodField (declared
        # above, in Meta.fields) never had a matching get_ method on this
        # class — DRF requires one for every SerializerMethodField, so
        # serializing ANY ServiceBookingPayment raised AttributeError
        # unconditionally. Both fields read the same metadata key here (see
        # get_payment_intent_id above), matching the equivalent pattern
        # already used correctly elsewhere in this file for marketplace
        # orders.
        return self.get_payment_intent_id(obj)

    def get_payment_url(self, obj):
        metadata = getattr(getattr(obj, 'booking', None), 'metadata', None)
        return str((metadata or {}).get('payment_url') or '') or None

    def get_payment_reference(self, obj):
        # Same missing-getter class of bug as get_direct_payment_intent_id
        # above — `payment_reference` was declared as a field with no
        # matching get_ method at all, so serializing ANY ServiceBookingPayment
        # raised AttributeError unconditionally. Prefers the provider-side
        # reference recorded in booking metadata (matching this class's own
        # get_payment_intent_id/get_payment_url pattern above), falling back
        # to the model's own transaction_reference column.
        metadata = getattr(getattr(obj, 'booking', None), 'metadata', None)
        from_metadata = str((metadata or {}).get('payment_reference') or '').strip()
        return from_metadata or (str(obj.transaction_reference).strip() or None)

    def get_currency_label(self, obj):
        return _commerce_currency_label(obj.currency)


class ServiceBookingSerializer(serializers.ModelSerializer):
    service_name = serializers.CharField(source='service.name', read_only=True)
    shop_name = serializers.CharField(source='shop.name', read_only=True)
    user_details = serializers.SerializerMethodField()
    provider_details = serializers.SerializerMethodField()
    service_details = serializers.SerializerMethodField()
    escrow_status = serializers.SerializerMethodField()
    escrow_amount_cents = serializers.SerializerMethodField()
    escrow_locked_at = serializers.SerializerMethodField()
    escrow_id = serializers.SerializerMethodField()
    payment_status = serializers.SerializerMethodField()
    schedule_label = serializers.SerializerMethodField()
    remote_available = serializers.SerializerMethodField()
    complaint_window = serializers.SerializerMethodField()
    complaint_count = serializers.SerializerMethodField()
    has_open_complaint = serializers.SerializerMethodField()
    latest_complaint = serializers.SerializerMethodField()
    remote_meeting_link = serializers.CharField(read_only=True)
    payment = serializers.SerializerMethodField()
    metadata = serializers.JSONField(read_only=True)
    price_usd_label = serializers.SerializerMethodField()
    deposit_usd_label = serializers.SerializerMethodField()
    balance_usd_label = serializers.SerializerMethodField()

    class Meta:
        model = ServiceBooking
        fields = (
            'id',
            'service',
            'service_name',
            'shop',
            'shop_name',
            'user',
            'user_details',
            'provider_details',
            'service_details',
            'scheduled_at',
            'schedule_label',
            'status',
            'price_cents',
            'price_usd_label',
            'deposit_cents',
            'deposit_usd_label',
            'balance_cents',
            'balance_usd_label',
            'instructions',
            'metadata',
            'payment_tx_ref',
            'remote_meeting_link',
            'remote_available',
            'escrow_status',
            'escrow_amount_cents',
            'escrow_locked_at',
            'escrow_id',
            'payment_status',
            'payment',
            'price_usd_label',
            'deposit_usd_label',
            'balance_usd_label',
            'provider_completed_at',
            'payer_satisfied_at',
            'satisfaction_deadline',
            'complaint_window',
            'complaint_count',
            'has_open_complaint',
            'latest_complaint',
            'created_at',
            'updated_at',
        )
        read_only_fields = (
            'id',
            'service',
            'shop',
            'user',
            'status',
            'payment_tx_ref',
            'remote_available',
            'escrow_status',
            'escrow_amount_cents',
            'escrow_locked_at',
            'escrow_id',
            'payment_status',
            'payment',
            'provider_completed_at',
            'payer_satisfied_at',
            'satisfaction_deadline',
            'complaint_window',
            'complaint_count',
            'has_open_complaint',
            'latest_complaint',
            'created_at',
            'updated_at',
        )

    def get_user_details(self, obj):
        user = getattr(obj, 'user', None)
        if not user:
            return None
        return {
            'id': str(user.id),
            'display_name': getattr(user, 'display_name', '') or getattr(user, 'username', '') or getattr(user, 'phone', ''),
            'phone': getattr(user, 'phone', None),
            'email': getattr(user, 'email', None),
        }

    def get_price_usd_label(self, obj):
        return _usd_label_from_cents(obj.price_cents)

    def get_deposit_usd_label(self, obj):
        return _usd_label_from_cents(obj.deposit_cents)

    def get_balance_usd_label(self, obj):
        return _usd_label_from_cents(obj.balance_cents)

    def get_provider_details(self, obj):
        provider = obj.provider_user
        if not provider:
            return None
        return {
            'id': str(provider.id),
            'display_name': getattr(provider, 'display_name', '') or getattr(provider, 'username', '') or getattr(provider, 'phone', ''),
            'phone': getattr(provider, 'phone', None),
            'email': getattr(provider, 'email', None),
        }

    def get_service_details(self, obj):
        service = getattr(obj, 'service', None)
        if not service:
            return None
        price_cents = int((service.price or 0) * 100)
        deposit_value = service.deposit_amount if service.deposit_amount is not None else service.price
        deposit_cents = int((deposit_value or 0) * 100)
        return {
            'id': str(service.id),
            'shop_id': str(service.shop_id) if service.shop_id else None,
            'name': service.name,
            'short_summary': service.short_summary,
            'description': service.description,
            'service_type': service.service_type,
            'pricing_model': service.pricing_model,
            'quote_required': service.quote_required,
            'negotiable': service.negotiable,
            'delivery_modes': service.delivery_modes or [],
            'duration_minutes': service.duration_minutes,
            'prep_buffer_minutes': service.prep_buffer_minutes,
            'cleanup_buffer_minutes': service.cleanup_buffer_minutes,
            'turnaround_hours': service.turnaround_hours,
            'staff_required': service.staff_required,
            'max_participants': service.max_participants,
            'group_booking_allowed': service.group_booking_allowed,
            'max_bookings_per_slot': service.max_bookings_per_slot,
            'price_cents': price_cents,
            'deposit_cents': deposit_cents,
            'deposit_percent': service.deposit_percent,
            'minimum_charge': service.minimum_charge,
            'coverage': service.coverage or [],
            'remote_regions': service.remote_regions or [],
            'remote_meeting_link': service.remote_meeting_link,
            'availability': service.availability or {},
            'availability_rules': service.availability_rules or [],
            'blackout_dates': service.blackout_dates or [],
            'cancellation_window_hours': service.cancellation_window_hours,
            'reschedule_window_hours': service.reschedule_window_hours,
            'refund_policy': service.refund_policy,
            'service_terms': service.service_terms,
            'requirements': service.requirements or [],
            'packages': service.packages or [],
            'addons': service.addons or [],
            'min_notice_hours': service.min_notice_hours,
            'max_advance_booking_days': service.max_advance_booking_days,
            'tags': service.tags or [],
        }

    def get_escrow_status(self, obj):
        escrow = getattr(obj, 'escrow', None)
        if escrow:
            return escrow.status
        return ServiceBookingEscrow.STATUS_PENDING

    def get_escrow_amount_cents(self, obj):
        escrow = getattr(obj, 'escrow', None)
        if escrow and escrow.amount_cents:
            return escrow.amount_cents
        return obj.deposit_cents

    def get_escrow_locked_at(self, obj):
        escrow = getattr(obj, 'escrow', None)
        if escrow and escrow.locked_at:
            return escrow.locked_at
        return obj.created_at

    def get_escrow_id(self, obj):
        escrow = getattr(obj, 'escrow', None)
        if escrow and getattr(escrow, 'id', None):
            return str(escrow.id)
        payment = getattr(obj, 'payment', None)
        if not payment:
            return None
        serializer = ServiceBookingPaymentSerializer(payment, context=self.context)
        return serializer.data

    def get_payment_status(self, obj):
        payment = getattr(obj, 'payment', None)
        if payment:
            status = str(payment.payment_status or '').strip()
            if status:
                return status.capitalize()
        escrow = getattr(obj, 'escrow', None)
        if escrow and escrow.status not in {
            ServiceBookingEscrow.STATUS_PENDING,
            ServiceBookingEscrow.STATUS_DISPUTE,
        }:
            return 'Paid'
        return 'Pending'

    def get_payment(self, obj):
        payment = getattr(obj, 'payment', None)
        if not payment:
            return None
        serializer = ServiceBookingPaymentSerializer(payment, context=self.context)
        return serializer.data

    def _get_complaints(self, obj):
        complaints = getattr(obj, 'complaints', None)
        if not complaints:
            return []
        return list(complaints.all())

    def _latest_complaint(self, obj):
        complaints = self._get_complaints(obj)
        if not complaints:
            return None
        latest = max(complaints, key=lambda item: item.created_at or item.updated_at or item.id)
        return latest

    def get_complaint_count(self, obj):
        return len(self._get_complaints(obj))

    def get_has_open_complaint(self, obj):
        latest = self._latest_complaint(obj)
        if not latest:
            return False
        return latest.status not in {
            ServiceBookingComplaint.STATUS_RESOLVED_RELEASE,
            ServiceBookingComplaint.STATUS_RESOLVED_REFUND,
            ServiceBookingComplaint.STATUS_REJECTED,
        }

    def get_latest_complaint(self, obj):
        complaint = self._latest_complaint(obj)
        if not complaint:
            return None
        return {
            'id': str(complaint.id),
            'status': complaint.status,
            'status_display': complaint.get_status_display(),
            'action': complaint.action,
            'action_display': complaint.get_action_display(),
            'transaction_reference': complaint.transaction_reference,
            'receipt_url': complaint.receipt_url,
            'personal_statement': complaint.personal_statement,
            'reason': complaint.reason,
            'resolution_note': complaint.resolution_note,
            'resolved_by': str(complaint.resolved_by_id) if complaint.resolved_by_id else None,
            'resolved_at': complaint.resolved_at,
            'created_at': complaint.created_at,
            'updated_at': complaint.updated_at,
        }

    def get_schedule_label(self, obj):
        if not obj.scheduled_at:
            return None
        return timezone.localtime(obj.scheduled_at).strftime('%a, %b %d %Y • %I:%M %p')

    def get_remote_available(self, obj):
        return bool(obj.remote_meeting_link and obj.status != ServiceBooking.STATUS_CANCELLED)

    def get_complaint_window(self, obj):
        expires = obj.complaint_window_expires
        if not expires:
            return None
        return expires


class ServiceBookingCreateSerializer(serializers.Serializer):
    service_id = serializers.UUIDField()
    scheduled_at = serializers.DateTimeField()
    instructions = serializers.CharField(required=False, allow_blank=True)
    requirements_acknowledged = serializers.ListField(
        child=serializers.CharField(), required=False, default=list
    )
    terms_accepted = serializers.BooleanField(required=False, default=False)
    selected_package = serializers.CharField(required=False, allow_blank=True)
    selected_addons = serializers.ListField(child=serializers.CharField(), required=False, default=list)
    requested_price = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, min_value=0)
    location = serializers.DictField(required=False)
    distance_km = serializers.DecimalField(max_digits=8, decimal_places=2, required=False)
    is_remote = serializers.BooleanField(required=False, default=False)
    remote_region = serializers.CharField(required=False, allow_blank=True)
    participant_count = serializers.IntegerField(required=False, min_value=1)
    staff_on_site = serializers.IntegerField(required=False, min_value=0)


class ServiceBookingRescheduleSerializer(serializers.Serializer):
    scheduled_at = serializers.DateTimeField()


class ServiceBookingComplaintSerializer(serializers.ModelSerializer):
    booking_reference = serializers.CharField(source='booking.payment_tx_ref', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    action_display = serializers.CharField(source='get_action_display', read_only=True)
    booking_status = serializers.SerializerMethodField()
    escrow_status = serializers.SerializerMethodField()

    class Meta:
        model = ServiceBookingComplaint
        fields = (
            'id',
            'booking',
            'booking_reference',
            'escrow',
            'submitted_by',
            'provider',
            'status',
            'status_display',
            'action',
            'action_display',
            'transaction_reference',
            'receipt_url',
            'personal_statement',
            'reason',
        'service_name',
        'shop_name',
        'provider_info',
        'booking_status',
        'escrow_status',
        'metadata',
        'resolution_note',
        'resolved_by',
        'resolved_at',
            'created_at',
            'updated_at',
        )
        read_only_fields = (
            'id',
            'booking_reference',
            'submitted_by',
            'provider',
            'status',
            'status_display',
            'action',
            'action_display',
            'service_name',
            'shop_name',
            'provider_info',
            'booking_status',
            'escrow_status',
            'metadata',
            'resolution_note',
            'resolved_by',
            'resolved_at',
            'created_at',
            'updated_at',
        )
        extra_kwargs = {
            'escrow': {'required': False, 'allow_null': True},
            'payment': {'required': False, 'allow_null': True},
            'transaction_reference': {'required': False, 'allow_blank': True},
            'receipt_url': {'required': False, 'allow_blank': True},
            'personal_statement': {'required': False, 'allow_blank': True},
            'reason': {'required': False, 'allow_blank': True},
        }

    def create(self, validated_data):
        request = self.context.get('request')
        user = getattr(request, 'user', None)
        if user:
            validated_data['submitted_by'] = user
        booking = validated_data.get('booking')
        if booking:
            validated_data.setdefault('escrow', getattr(booking, 'escrow', None))
            validated_data.setdefault('provider', booking.provider_user)
            validated_data.setdefault('service_name', booking.service.name if booking.service else '')
            validated_data.setdefault('shop_name', booking.shop.name if booking.shop else '')
            provider_info = validated_data.get('provider_info') or {}
            provider_info.setdefault('shop_name', booking.shop.name if booking.shop else '')
            provider_info.setdefault('service_name', booking.service.name if booking.service else '')
            validated_data['provider_info'] = provider_info
            payment = getattr(booking, 'payment', None)
            if payment:
                validated_data.setdefault('payment', payment)
                validated_data.setdefault('transaction_reference', payment.transaction_reference or booking.payment_tx_ref)
            else:
                validated_data.setdefault('transaction_reference', booking.payment_tx_ref)
        metadata = validated_data.get('metadata') or {}
        if booking:
            metadata.setdefault('booking_id', str(booking.id))
            metadata.setdefault('service_id', str(booking.service_id))
        validated_data['metadata'] = metadata
        complaint = super().create(validated_data)
        if complaint.status == ServiceBookingComplaint.STATUS_SUBMITTED:
            complaint.status = ServiceBookingComplaint.STATUS_UNDER_REVIEW
            complaint.save(update_fields=['status'])
        if booking:
            booking.status = ServiceBooking.STATUS_DISPUTE
            booking.save(update_fields=['status'])
            escrow = getattr(booking, 'escrow', None)
            if escrow:
                escrow.status = ServiceBookingEscrow.STATUS_DISPUTE
                escrow.save(update_fields=['status'])
        return complaint

    def validate(self, attrs):
        request = self.context.get('request')
        user = getattr(request, 'user', None)
        booking = attrs.get('booking')
        if booking and user and booking.user_id != user.id and not getattr(user, 'is_staff', False):
            raise serializers.ValidationError({'booking': 'Only the payer can submit a complaint for this booking.'})
        if booking and not attrs.get('escrow') and not getattr(booking, 'escrow', None):
            raise serializers.ValidationError({'escrow': 'This booking has no escrow record for complaint processing.'})
        return attrs

    def get_booking_status(self, obj):
        booking = getattr(obj, 'booking', None)
        return booking.status if booking else None

    def get_escrow_status(self, obj):
        escrow = getattr(obj, 'escrow', None)
        return escrow.status if escrow else None

class ProductShareSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductShare
        fields = '__all__'


class AIRecommendationSerializer(serializers.ModelSerializer):
    class Meta:
        model = AIRecommendation
        fields = '__all__'


class AuditLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = AuditLog
        fields = '__all__'


class FraudSignalSerializer(serializers.ModelSerializer):
    class Meta:
        model = FraudSignal
        fields = '__all__'


class CartItemSerializer(serializers.ModelSerializer):
    quantity = serializers.IntegerField(min_value=1)
    product_name = serializers.SerializerMethodField()
    product_image = serializers.SerializerMethodField()
    attribute_labels = serializers.DictField(child=serializers.CharField(), required=False)

    class Meta:
        model = CartItem
        fields = (
            'id',
            'cart',
            'product',
            'variant',
            'size',
            'color',
            'variant_snapshot',
            'quantity',
            'product_name',
            'product_image',
            'price_snapshot',
            'stock_snapshot',
            'selected_attributes',
            'attribute_labels',
            'custom_description',
            'created_at',
            'updated_at',
        )
        read_only_fields = ('id', 'created_at', 'updated_at')

    def _build_image_url(self, product):
        if not product:
            return ''
        try:
            url = product.effective_image_url or ''
        except AttributeError:
            url = getattr(product, 'image_url', '') or ''
        request = self.context.get('request')
        return absolutize_backend_media(url, request=request)

    def get_product_name(self, obj):
        return obj.product.name if obj.product else None

    def get_product_image(self, obj):
        return self._build_image_url(obj.product)


class CartItemAddOrUpdateSerializer(serializers.Serializer):
    shop_id = serializers.UUIDField()
    product_id = serializers.UUIDField()
    variant_id = serializers.CharField(required=False, allow_blank=True)
    size = serializers.CharField(required=False, allow_blank=True)
    color = serializers.CharField(required=False, allow_blank=True)
    quantity = serializers.IntegerField(min_value=1, default=1)
    selected_options = serializers.DictField(
        child=serializers.ListField(child=serializers.CharField()),
        required=False,
    )
    custom_description = serializers.CharField(required=False, allow_blank=True)


class CartSerializer(serializers.ModelSerializer):
    items = CartItemSerializer(many=True, read_only=True)
    shop_info = serializers.SerializerMethodField()

    class Meta:
        model = Cart
        fields = (
            'id',
            'user',
            'shop',
            'shop_info',
            'status',
            'subtotal',
            'items',
            'created_at',
            'updated_at',
        )
        read_only_fields = ('id', 'user', 'created_at', 'updated_at')

    def get_shop_info(self, obj):
        shop = getattr(obj, 'shop', None)
        if not shop:
            return None
        image_url = getattr(shop, 'image_url', '') or ''
        return {
            'id': str(shop.id),
            'name': shop.name,
            'slug': shop.slug,
            'description': getattr(shop, 'description', '') or '',
            'image_url': image_url,
            'image': image_url,
            'logo': image_url,
        }


class MarketplaceOrderItemSerializer(serializers.ModelSerializer):
    product_name = serializers.SerializerMethodField()

    class Meta:
        model = MarketplaceOrderItem
        fields = (
            'id',
            'product',
            'product_name',
            'variant_id',
            'quantity',
            'unit_price_cents',
            'selected_attributes',
            'custom_description',
        )

    def get_product_name(self, obj):
        return obj.product.name if obj.product else ''


class MarketplaceOrderSerializer(serializers.ModelSerializer):
    items = MarketplaceOrderItemSerializer(many=True, read_only=True)
    buyer_info = serializers.SerializerMethodField()
    shop_info = serializers.SerializerMethodField()
    total_amount_cents = serializers.SerializerMethodField()
    total_usd_label = serializers.SerializerMethodField()
    currency_label = serializers.SerializerMethodField()
    payment_status = serializers.SerializerMethodField()
    payment_provider = serializers.SerializerMethodField()
    payment_required = serializers.SerializerMethodField()
    payment_intent_id = serializers.SerializerMethodField()
    direct_payment_intent_id = serializers.SerializerMethodField()
    payment_reference = serializers.SerializerMethodField()
    payment_url = serializers.SerializerMethodField()
    fulfillment_summary = serializers.SerializerMethodField()
    seller_trust = serializers.SerializerMethodField()
    next_action = serializers.SerializerMethodField()

    class Meta:
        model = MarketplaceOrder
        fields = (
            'id',
            'buyer',
            'buyer_info',
            'shop',
            'shop_info',
            'total_amount',
            'total_amount_cents',
            'total_usd_label',
            'currency',
            'currency_label',
            'status',
            'payment_status',
            'payment_provider',
            'payment_required',
            'payment_intent_id',
            'direct_payment_intent_id',
            'payment_reference',
            'payment_url',
            'fulfillment_summary',
            'seller_trust',
            'next_action',
            'metadata',
            'items',
            'created_at',
            'updated_at',
        )
        read_only_fields = ('id', 'status', 'currency', 'created_at', 'updated_at')

    def get_buyer_info(self, obj):
        user = getattr(obj, 'buyer', None)
        if not user:
            return None
        return {
            'id': str(user.id),
            'username': getattr(user, 'username', ''),
        }

    def get_shop_info(self, obj):
        shop = getattr(obj, 'shop', None)
        if not shop:
            return None
        return {
            'id': str(shop.id),
            'name': shop.name,
            'slug': shop.slug,
        }

    def get_total_amount_cents(self, obj):
        try:
            return int((Decimal(str(obj.total_amount or 0)) * Decimal('100')).quantize(Decimal('1')))
        except Exception:
            return 0

    def get_total_usd_label(self, obj):
        return _usd_label_from_cents(self.get_total_amount_cents(obj))

    def get_currency_label(self, obj):
        return _commerce_currency_label(obj.currency, obj.metadata if isinstance(obj.metadata, dict) else {})

    def get_payment_status(self, obj):
        meta = obj.metadata if isinstance(obj.metadata, dict) else {}
        return str(meta.get('payment_status') or ('paid' if obj.buyer_debit_transaction_id else 'pending'))

    def get_payment_provider(self, obj):
        meta = obj.metadata if isinstance(obj.metadata, dict) else {}
        return str(meta.get('payment_provider') or meta.get('payment_method') or ('legacy_wallet' if obj.buyer_debit_transaction_id else 'flutterwave'))

    def get_payment_required(self, obj):
        meta = obj.metadata if isinstance(obj.metadata, dict) else {}
        if 'payment_required' in meta:
            return bool(meta.get('payment_required'))
        return not bool(obj.buyer_debit_transaction_id)

    def get_payment_intent_id(self, obj):
        meta = obj.metadata if isinstance(obj.metadata, dict) else {}
        return str(meta.get('direct_payment_intent_id') or '') or None

    def get_direct_payment_intent_id(self, obj):
        return self.get_payment_intent_id(obj)

    def get_payment_reference(self, obj):
        meta = obj.metadata if isinstance(obj.metadata, dict) else {}
        return str(meta.get('payment_reference') or meta.get('tx_ref') or '') or None

    def get_payment_url(self, obj):
        meta = obj.metadata if isinstance(obj.metadata, dict) else {}
        return str(meta.get('payment_url') or '') or None

    def get_fulfillment_summary(self, obj):
        meta = obj.metadata if isinstance(obj.metadata, dict) else {}
        return {
            'status': str(obj.status or ''),
            'provider_completed_at': meta.get('provider_completed_at'),
            'satisfaction_deadline': meta.get('awaiting_satisfaction_until') or meta.get('satisfaction_deadline'),
            'delivery_status': meta.get('delivery_status') or meta.get('fulfillment_status') or ('awaiting_satisfaction' if obj.status == MarketplaceOrderStatus.AWAITING_SATISFACTION else 'pending'),
            'tracking_reference': meta.get('tracking_reference') or meta.get('fulfillment_reference') or '',
            'buyer_message': self._fulfillment_buyer_message(obj, meta),
        }

    def get_seller_trust(self, obj):
        return _commerce_trust_summary(getattr(obj, 'shop', None))

    def get_next_action(self, obj):
        meta = obj.metadata if isinstance(obj.metadata, dict) else {}
        payment_status = str(meta.get('payment_status') or '').lower()
        if payment_status in {'pending', 'failed', 'cancelled', 'canceled'}:
            return {
                'code': 'open_checkout',
                'label': 'Complete secure USD payment',
                'enabled': bool(meta.get('payment_url')),
            }
        if obj.status == MarketplaceOrderStatus.AWAITING_SATISFACTION:
            return {'code': 'confirm_satisfaction', 'label': 'Confirm satisfaction or open a complaint', 'enabled': True}
        if obj.status == MarketplaceOrderStatus.TEMPORAL:
            return {'code': 'await_provider_completion', 'label': 'Waiting for seller fulfillment', 'enabled': False}
        return {'code': 'none', 'label': 'No action needed', 'enabled': False}

    def _fulfillment_buyer_message(self, obj, meta):
        if obj.status == MarketplaceOrderStatus.AWAITING_SATISFACTION:
            return 'Seller marked the order complete. Confirm satisfaction within 3 days or file a complaint.'
        if obj.status == MarketplaceOrderStatus.COMPLAINT:
            return 'A complaint is open for review.'
        if str(meta.get('payment_status') or '').lower() in {'pending', 'failed'}:
            return 'Complete secure Flutterwave checkout before fulfillment begins.'
        return 'Seller fulfillment is being tracked on KIS.'


class MarketplaceOrderItemCreateSerializer(serializers.Serializer):
    product_id = serializers.UUIDField()
    variant_id = serializers.CharField(required=False, allow_blank=True)
    quantity = serializers.IntegerField(min_value=1, default=1)
    # Accepted for backward compatibility with existing clients but never
    # trusted - services._normalize_marketplace_items always re-derives the
    # authoritative price from Product/ProductVariant at order time, so this
    # is optional and purely advisory (e.g. for optimistic UI).
    unit_price_cents = serializers.IntegerField(min_value=1, required=False)
    selected_attributes = serializers.JSONField(required=False)
    custom_description = serializers.CharField(required=False, allow_blank=True)


class MarketplaceOrderCreateSerializer(serializers.Serializer):
    shop_id = serializers.UUIDField()
    items = MarketplaceOrderItemCreateSerializer(many=True)
    metadata = serializers.JSONField(required=False)
    payment_method = serializers.CharField(required=False, allow_blank=True)


class MarketplaceComplaintSerializer(serializers.ModelSerializer):
    class Meta:
        model = MarketplaceComplaint
        fields = ('id', 'order', 'user', 'text', 'attachment', 'status', 'created_at')
        read_only_fields = ('id', 'status', 'created_at')


class MarketplaceComplaintCreateSerializer(serializers.Serializer):
    order_id = serializers.UUIDField()
    text = serializers.CharField()
    attachment = serializers.FileField(required=False, allow_null=True)
