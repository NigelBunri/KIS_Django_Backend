import re
import uuid

from django.utils import timezone
from django.utils.text import slugify

from django.db import IntegrityError
import json
from django.http import QueryDict
from rest_framework import serializers
from .availability import normalize_availability_payload, derive_availability_rules_from_payload
from .constants import KIS_COIN_CODE
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
    AIRecommendation,
    AuditLog,
    FraudSignal,
    ProductImage,
    ProductRating,
    ShopCategory,
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
)

AVAILABILITY_RULE_SCOPES = {'year', 'month', 'week', 'day'}
TIME_PATTERN = re.compile(r'^([01]?\d|2[0-3]):([0-5]\d)$')


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
    if isinstance(value, dict):
        return value
    return fallback


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


class ShopCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = ShopCategory
        fields = ('id', 'shop', 'name', 'slug', 'description', 'category_type')
        extra_kwargs = {
            'slug': {'required': False, 'allow_blank': True},
        }


class ProductCategorySerializer(serializers.ModelSerializer):
    slug = serializers.SlugField(required=False, allow_blank=True)

    class Meta:
        model = ShopCategory
        fields = ('id', 'shop', 'name', 'slug', 'description', 'category_type')
        extra_kwargs = {
            'slug': {'required': False, 'allow_blank': True},
        }

    @staticmethod
    def _generate_slug(name: str, shop: Shop) -> str:
        base = slugify((name or '').strip()) or 'category'
        candidate = base
        suffix = 1
        while ShopCategory.objects.filter(shop=shop, slug=candidate).exists():
            candidate = f"{base}-{suffix}"
            suffix += 1
        return candidate

    def to_internal_value(self, data):
        payload = data.copy() if isinstance(data, dict) else dict(data)
        if not payload.get('slug'):
            shop_id = payload.get('shop')
            name = payload.get('name') or ''
            if shop_id and name:
                shop = Shop.objects.filter(pk=shop_id).first()
                if shop:
                    payload['slug'] = self._generate_slug(name, shop)
        return super().to_internal_value(payload)

    def create(self, validated_data):
        if not validated_data.get('slug'):
            shop = validated_data.get('shop')
            name = validated_data.get('name') or ''
            if shop:
                validated_data['slug'] = self._generate_slug(name, shop)
        return super().create(validated_data)


class ProductImageSerializer(serializers.ModelSerializer):
    image_url = serializers.SerializerMethodField()

    class Meta:
        model = ProductImage
        fields = ('id', 'image_url', 'order')

    def get_image_url(self, obj):
        try:
            url = obj.image_file.url
        except ValueError:
            return ''
        request = self.context.get('request')
        if request is not None:
            return request.build_absolute_uri(url)
        return url


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
        if request is not None:
            return request.build_absolute_uri(url)
        return url


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


class ShopLandingTestimonialSerializer(serializers.ModelSerializer):
    class Meta:
        model = ShopLandingTestimonial
        fields = ('id', 'quote', 'author', 'role', 'rating', 'sort_order')


class ShopLandingPageSerializer(serializers.ModelSerializer):
    testimonials = ShopLandingTestimonialSerializer(many=True, read_only=True)

    class Meta:
        model = ShopLandingPage
        fields = (
            'id',
            'headline',
            'subheadline',
            'hero_image_url',
            'hero_cta_text',
            'hero_cta_url',
            'is_public',
            'is_published',
            'testimonials',
        )


class LandingPageField(serializers.Field):
    def to_representation(self, value):
        if not value:
            return {}
        return ShopLandingPageSerializer(value).data

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
    employee_slots = serializers.IntegerField(min_value=1, default=1)
    categories = ShopCategorySerializer(many=True, read_only=True)
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
            'image_url',
            'slug',
        )

    def get_image_url(self, obj):
        return obj.image_url

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
        for field in ('headline', 'subheadline', 'hero_image_url', 'hero_cta_text', 'hero_cta_url'):
            if field in payload:
                value = payload[field] or ''
                if getattr(landing_page, field, '') != value:
                    setattr(landing_page, field, value)
                    updated_fields.add(field)
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
        return super().create(validated_data)


class ShopVerificationRequestSerializer(serializers.ModelSerializer):
    class Meta:
        model = ShopVerificationRequest
        fields = '__all__'
        read_only_fields = ('status', 'risk_score', 'processed_at')


class ProductSerializer(serializers.ModelSerializer):
    image_file = serializers.ImageField(required=False, allow_null=True)
    image_url = serializers.SerializerMethodField()
    category = ShopCategorySerializer(read_only=True)
    category_id = serializers.UUIDField(write_only=True, required=False, allow_null=True)
    images = ProductImageSerializer(many=True, read_only=True)
    slug = serializers.CharField(required=False, allow_blank=True)
    sku = serializers.CharField(required=False, allow_blank=True)
    is_broadcasted = serializers.SerializerMethodField()
    broadcast_item_id = serializers.SerializerMethodField()
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
       )

    def get_image_url(self, obj):
        request = self.context.get('request')
        url_candidate = ''

        image_file = getattr(obj, 'image_file', None)
        if image_file:
            try:
                url_candidate = image_file.url or ''
            except (ValueError, AttributeError):
                url_candidate = ''

        if not url_candidate:
            url_candidate = getattr(obj, 'image_url', '') or ''

        if not url_candidate and hasattr(obj, 'images'):
            try:
                first_image = obj.images.order_by('order').first()
            except AttributeError:
                first_image = getattr(obj.images, 'first', lambda: None)()
            if first_image:
                try:
                    url_candidate = first_image.image_file.url or ''
                except (ValueError, AttributeError):
                    url_candidate = first_image.image_url or ''

        if url_candidate and request:
            return request.build_absolute_uri(url_candidate)

        return url_candidate

    def validate(self, attrs):
        attrs = super().validate(attrs)
        category_id = attrs.get("category_id")
        shop = attrs.get("shop") or getattr(self.instance, "shop", None)
        if category_id and shop is not None:
            try:
                category = ShopCategory.objects.get(id=category_id)
            except ShopCategory.DoesNotExist:
                raise serializers.ValidationError({"category_id": "Category does not exist."})
            if category.shop_id != shop.id:
                raise serializers.ValidationError({"category_id": "Category must belong to the same shop."})
            attrs["_category_obj"] = category
        attrs = self._hydrate_extended_attributes(attrs)
        attrs["currency"] = KIS_COIN_CODE
        attrs["attributes"] = self._sanitize_attributes(attrs.get("attributes"))
        return attrs

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

    def _get_market_broadcast_item(self, obj: Product):
        existing = getattr(obj, '_market_broadcast_item', None)
        if existing is not None:
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
        if normalized and normalized != KIS_COIN_CODE:
            raise serializers.ValidationError(f"Currency must be {KIS_COIN_CODE}.")
        return KIS_COIN_CODE

    def create(self, validated_data):
        category = validated_data.pop("_category_obj", None)
        validated_data.pop("category_id", None)
        if not validated_data.get("slug"):
            validated_data["slug"] = _generate_product_slug(validated_data.get("name"))
        if not validated_data.get("sku"):
            validated_data["sku"] = _generate_product_sku()
        product = super().create(validated_data)
        if category:
            product.category = category
            product.save(update_fields=["category"])
        return product

    def update(self, instance, validated_data):
        category = validated_data.pop("_category_obj", None)
        validated_data.pop("category_id", None)
        product = super().update(instance, validated_data)
        if category is not None:
            product.category = category
            product.save(update_fields=["category"])
        return product


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
    category = ShopCategorySerializer(read_only=True)
    category_id = serializers.UUIDField(write_only=True, required=False, allow_null=True)
    images = ServiceImageSerializer(many=True, read_only=True)
    currency = serializers.CharField(write_only=True, required=False, allow_blank=True)
    is_broadcasted = serializers.SerializerMethodField()
    broadcast_item_id = serializers.SerializerMethodField()
    packages = serializers.JSONField(required=False, default=list)
    addons = serializers.JSONField(required=False, default=list)
    requirements = serializers.JSONField(required=False, default=list)
    delivery_modes = serializers.ListField(child=serializers.CharField(), required=False, default=list)
    coverage = serializers.ListField(child=serializers.CharField(), required=False, default=list)
    remote_regions = serializers.ListField(child=serializers.CharField(), required=False, default=list)
    remote_meeting_link = serializers.CharField(required=False, allow_blank=True)

    class Meta:
        model = ShopService
        fields = '__all__'
        read_only_fields = ('created_at', 'updated_at', 'is_broadcasted', 'broadcast_item_id')
        validators: list = []

    def to_internal_value(self, data):
        normalized_slug = None
        slug_candidate = str(data.get('slug') or data.get('name') or '').strip()
        if slug_candidate:
            normalized_slug = slugify(slug_candidate) or 'service'
        mutable_data = (
            data.dict() if isinstance(data, QueryDict)
            else data.copy() if isinstance(data, dict)
            else dict(data)
        )
        mutable_data.pop('currency', None)
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
            'remoteMeetingLink': 'remote_meeting_link',
            'allowMultipleAttendeesPerSlot': 'allow_multiple_attendees_per_slot',
        }
        for camel, snake in mapper.items():
            if camel in mutable_data and snake not in mutable_data:
                mutable_data[snake] = mutable_data.pop(camel)
        print("ShopServiceSerializer payload:", mutable_data)
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
        return super().to_internal_value(mutable_data)

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
        category_id = attrs.get('category_id')
        if category_id and shop is not None:
            try:
                category = ShopCategory.objects.get(id=category_id)
            except ShopCategory.DoesNotExist:
                raise serializers.ValidationError({'category_id': 'Category does not exist.'})
            if category.shop_id != shop.id:
                raise serializers.ValidationError({'category_id': 'Category must belong to the same shop.'})
            attrs['_category_obj'] = category
        name_value = str(attrs.get('name') or getattr(self.instance, 'name', '')).strip()
        slug_value = str(attrs.get('slug') or getattr(self.instance, 'slug', '')).strip()
        slug_source = slug_value or name_value or ''
        if slug_source:
            normalized = slugify(slug_source) or 'service'
            if shop:
                attrs['slug'] = self._generate_unique_slug(normalized, shop)
            else:
                attrs['slug'] = normalized
        if 'price' in attrs:
            if attrs['price'] is None or attrs['price'] < 0:
                raise serializers.ValidationError({'price': 'Price must be greater than or equal to zero.'})
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
        data['availability'] = normalize_availability_payload(getattr(instance, 'availability', {}))
        if not self._user_can_view_remote_link(instance):
            data.pop('remote_meeting_link', None)
        return data

    def create(self, validated_data):
        category = validated_data.pop('_category_obj', None)
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
        if category:
            service.category = category
            service.save(update_fields=['category'])
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
        category = validated_data.pop('_category_obj', None)
        service = super().update(instance, validated_data)
        if category:
            service.category = category
            service.save(update_fields=['category'])
        return service

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
    class Meta:
        model = ServiceBookingPayment
        fields = (
            'id',
            'booking',
            'amount_cents',
            'currency',
            'payment_method',
            'payment_status',
            'paid_at',
            'transaction_reference',
            'notes',
            'satisfied_at',
            'created_at',
            'updated_at',
        )
        read_only_fields = fields


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
            'deposit_cents',
            'balance_cents',
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
            'short_summary': service.short_summary,
            'description': service.description,
            'service_type': service.service_type,
            'delivery_modes': service.delivery_modes or [],
            'duration_minutes': service.duration_minutes,
            'price_cents': price_cents,
            'deposit_cents': deposit_cents,
            'remote_meeting_link': service.remote_meeting_link,
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
        'status_display',
        'action_display',
        'booking_status',
        'escrow_status',
        'created_at',
        'updated_at',
        'resolved_at',
    )

    def create(self, validated_data):
        request = self.context.get('request')
        user = getattr(request, 'user', None)
        if user:
            validated_data['submitted_by'] = user
        booking = validated_data.get('booking')
        if booking:
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

    class Meta:
        model = CartItem
        fields = (
            'id',
            'cart',
            'product',
            'variant',
            'variant_snapshot',
            'quantity',
            'product_name',
            'product_image',
            'price_snapshot',
            'stock_snapshot',
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
        if url and request:
            return request.build_absolute_uri(url)
        return url

    def get_product_name(self, obj):
        return obj.product.name if obj.product else None

    def get_product_image(self, obj):
        return self._build_image_url(obj.product)


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
        return {
            'id': str(shop.id),
            'name': shop.name,
            'slug': shop.slug,
            'image_url': shop.image_url,
        }
