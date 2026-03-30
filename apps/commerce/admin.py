from django.contrib import admin

from .models import (
    AIRecommendation,
    AuditLog,
    Cart,
    CartItem,
    LoyaltyPoint,
    Order,
    OrderItem,
    Payment,
    Product,
    ProductAuthenticityCheck,
    ProductImage,
    ProductRating,
    ProductShare,
    ProductSubscription,
    Promotion,
    Shop,
    ShopCategory,
    ShopFollow,
    ShopLandingPage,
    ShopLandingTestimonial,
    ShopService,
    ShopServiceImage,
    ShopTeamMember,
    ServiceBooking,
    ServiceBookingEscrow,
    ServiceBookingPayment,
    ServiceBookingReceipt,
)


@admin.register(Shop)
class ShopAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'slug', 'owner', 'is_verified', 'rating_avg')
    list_filter = ('is_verified',)
    search_fields = ('name', 'slug', 'owner__email')


@admin.register(ShopCategory)
class ShopCategoryAdmin(admin.ModelAdmin):
    list_display = ('id', 'shop', 'name', 'slug')
    search_fields = ('name', 'slug')


@admin.register(ShopLandingPage)
class ShopLandingPageAdmin(admin.ModelAdmin):
    list_display = ('id', 'shop', 'headline', 'is_public', 'is_published')


@admin.register(ShopLandingTestimonial)
class ShopLandingTestimonialAdmin(admin.ModelAdmin):
    list_display = ('id', 'landing_page', 'author', 'rating', 'sort_order')


@admin.register(ShopServiceImage)
class ShopServiceImageAdmin(admin.ModelAdmin):
    list_display = ('id', 'service', 'order')


@admin.register(ShopFollow)
class ShopFollowAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'shop', 'followed_at')
    list_filter = ('shop',)


@admin.register(ShopTeamMember)
class ShopTeamMemberAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'shop', 'role', 'is_active')
    list_filter = ('role', 'is_active')


@admin.register(ShopService)
class ShopServiceAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'shop', 'name', 'price', 'service_type', 'status', 'visibility',
        'is_active', 'is_featured', 'availability', 'availability_summary'
    )
    list_filter = ('service_type', 'status', 'visibility', 'is_active', 'is_featured', 'shop')
    search_fields = ('name', 'description', 'slug')
    readonly_fields = ('created_at', 'updated_at')
    exclude = ('id',)
    fieldsets = (
        ('Identity', {'fields': ('shop', 'category', 'name', 'slug')}),
        ('Pricing & Visibility', {'fields': ('price', 'service_type', 'status', 'visibility', 'is_active', 'is_featured', 'other_shops_discount')}),
        ('Details', {'fields': (
            'short_summary', 'description', 'delivery_modes', 'duration_minutes',
            'max_bookings_per_slot', 'availability_rules', 'coverage', 'packages', 'addons', 'requirements',
        )}),
        ('Media', {'fields': ('image_url', 'image_file')}),
        ('Audit', {'fields': ('created_at', 'updated_at', 'is_deleted')}),
    )

    def availability_summary(self, obj):
        rules = getattr(obj, 'availability_rules', [])
        if not rules:
            return '—'
        return '; '.join(
            f"{rule.get('scope')} → {len(rule.get('targets', []))} days @ {', '.join(rule.get('times', [])[:3]) + ('...' if len(rule.get('times', [])) > 3 else '')}"
            for rule in rules
        )
    availability_summary.short_description = 'Availability rules'


@admin.register(ServiceBooking)
class ServiceBookingAdmin(admin.ModelAdmin):
    list_display = ('id', 'service', 'shop', 'user', 'status', 'price_cents')
    list_filter = ('status',)
    search_fields = ('remote_meeting_link',)
    exclude = ('id',)


@admin.register(ServiceBookingReceipt)
class ServiceBookingReceiptAdmin(admin.ModelAdmin):
    list_display = ('id', 'booking', 'phase', 'amount_cents')
    list_filter = ('phase',)


@admin.register(ServiceBookingPayment)
class ServiceBookingPaymentAdmin(admin.ModelAdmin):
    list_display = ('id', 'booking', 'amount_cents', 'currency', 'payment_method', 'payment_status')
    list_filter = ('payment_status', 'payment_method')
    search_fields = ('paid_at',)
    exclude = ('id',)


@admin.register(ServiceBookingEscrow)
class ServiceBookingEscrowAdmin(admin.ModelAdmin):
    list_display = ('id', 'booking', 'status')
    list_filter = ('status',)


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    readonly_fields = (
        'id', 'created_at', 'updated_at', 'rating_avg', 'rating_count', 'ai_score',
    )
    list_display = (
        'id', 'name', 'sku', 'shop', 'inventory_type', 'price', 'currency', 'stock_qty',
        'is_active', 'is_featured', 'authenticity_status', 'service_type', 'availability', 'other_shops_discount',
    )
    list_filter = ('inventory_type', 'is_active', 'is_featured', 'authenticity_status', 'shop')
    search_fields = ('name', 'sku', 'description', 'slug')
    fieldsets = (
        ('Identification', {'fields': ('id', 'shop', 'name', 'sku', 'slug', 'category')}),
        ('Description & Media', {'fields': ('description', 'image_url', 'image_file', 'ar_preview_url')}),
        ('Pricing & Inventory', {'fields': ('price', 'currency', 'inventory_type', 'stock_qty', 'variants', 'categories')}),
        ('Service metadata', {'fields': ('service_type', 'availability', 'coverage', 'location', 'other_shops_discount', 'availability_rules')}),
        ('Attributes & Flags', {'fields': ('attributes', 'is_active', 'is_featured')}),
        ('Ratings & Signals', {'fields': ('rating_avg', 'rating_count', 'ai_score', 'authenticity_status', 'authenticity_proof')}),
        ('Audit', {'fields': ('created_at', 'updated_at', 'is_deleted')}),
    )


@admin.register(ProductImage)
class ProductImageAdmin(admin.ModelAdmin):
    list_display = ('id', 'product', 'order')


@admin.register(ProductRating)
class ProductRatingAdmin(admin.ModelAdmin):
    list_display = ('id', 'product', 'user', 'score')


@admin.register(ProductAuthenticityCheck)
class ProductAuthenticityCheckAdmin(admin.ModelAdmin):
    list_display = ('id', 'product', 'provider', 'status', 'confidence')
    list_filter = ('status',)


@admin.register(ProductShare)
class ProductShareAdmin(admin.ModelAdmin):
    list_display = ('id', 'product', 'user', 'shared_at')


@admin.register(ProductSubscription)
class ProductSubscriptionAdmin(admin.ModelAdmin):
    list_display = ('id', 'product', 'user', 'platform', 'subscribed_at')


@admin.register(Cart)
class CartAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'shop', 'status', 'subtotal')
    list_filter = ('status', 'shop')


@admin.register(CartItem)
class CartItemAdmin(admin.ModelAdmin):
    list_display = ('id', 'cart', 'product', 'variant', 'quantity', 'price_snapshot')
    list_filter = ('cart__shop',)


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'shop', 'status', 'total')
    list_filter = ('status', 'shop')


@admin.register(OrderItem)
class OrderItemAdmin(admin.ModelAdmin):
    list_display = ('id', 'order', 'product', 'name', 'quantity')


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ('id', 'order', 'provider', 'amount', 'status')
    list_filter = ('status',)


@admin.register(Promotion)
class PromotionAdmin(admin.ModelAdmin):
    list_display = ('id', 'shop', 'code', 'discount_type', 'start_date', 'end_date')
    list_filter = ('shop', 'discount_type')


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'shop', 'plan_name', 'status')
    list_filter = ('status',)


@admin.register(LoyaltyPoint)
class LoyaltyPointAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'shop', 'points', 'earned_at')


@admin.register(AIRecommendation)
class AIRecommendationAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'target_type', 'target_id', 'score')


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ('id', 'actor', 'action', 'target_type', 'target_id')


@admin.register(FraudSignal)
class FraudSignalAdmin(admin.ModelAdmin):
    list_display = ('id', 'source', 'entity_type', 'entity_id', 'score', 'processed')
