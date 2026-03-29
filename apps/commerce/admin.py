from django.contrib import admin

from .models import Product, ServiceBooking, ServiceBookingPayment, ShopService


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    readonly_fields = (
        'id',
        'created_at',
        'updated_at',
        'rating_avg',
        'rating_count',
        'ai_score',
    )
    list_display = (
        'id',
        'name',
        'sku',
        'shop',
        'inventory_type',
        'price',
        'currency',
        'stock_qty',
        'is_active',
        'is_featured',
        'authenticity_status',
        'service_type',
        'availability',
        'other_shops_discount',
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


@admin.register(ShopService)
class ShopServiceAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'shop', 'name', 'price', 'service_type', 'status', 'visibility', 'is_active', 'is_featured', 'availability_summary'
    )
    list_filter = ('service_type', 'status', 'visibility', 'is_active', 'is_featured', 'shop')
    search_fields = ('name', 'description', 'slug')
    readonly_fields = ('created_at', 'updated_at')
    exclude = ('id',)
    fieldsets = (
        ('Identity', {'fields': ('shop', 'category', 'name', 'slug')}),
        ('Pricing & Visibility', {'fields': ('price', 'service_type', 'status', 'visibility', 'is_active', 'is_featured', 'other_shops_discount')}),
        ('Details', {
            'fields': (
                'short_summary', 'description', 'delivery_modes', 'duration_minutes',
                'max_bookings_per_slot', 'availability_rules', 'coverage', 'packages', 'addons', 'requirements',
            ),
        }),
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

@admin.register(ServiceBookingPayment)
class ServiceBookingPaymentAdmin(admin.ModelAdmin):
    list_display = ('id', 'booking', 'amount_cents', 'currency', 'payment_method', 'payment_status')
    list_filter = ('payment_status', 'payment_method')
    search_fields = ('paid_at',)
    exclude = ('id',)