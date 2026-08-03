from django.urls import path
from rest_framework.routers import DefaultRouter
from . import views

router = DefaultRouter()
router.register(r'shops', views.ShopViewSet)
router.register(r'shop-verifications', views.ShopVerificationRequestViewSet)
router.register(r'products', views.ProductViewSet)
router.register(r'product-ratings', views.ProductRatingViewSet)
router.register(r'product-reviews', views.ProductReviewViewSet)
router.register(r'product-questions', views.ProductQuestionViewSet)
router.register(r'product-categories', views.ProductCategoryViewSet)
router.register(r'product-auth-checks', views.ProductAuthenticityCheckViewSet, basename='product-auth-checks')
router.register(r'orders', views.OrderViewSet, basename='orders')
router.register(r'payments', views.PaymentViewSet, basename='payments')
router.register(r'promotions', views.PromotionViewSet, basename='promotions')
router.register(r'subscriptions', views.SubscriptionViewSet, basename='subscriptions')
router.register(r'loyalty', views.LoyaltyPointViewSet, basename='loyalty')
router.register(r'follows', views.ShopFollowViewSet, basename='follows')
router.register(r'shop-members', views.ShopTeamMemberViewSet)
router.register(r'shop-services', views.ShopServiceViewSet)
router.register(r'service-bookings', views.ServiceBookingViewSet)
router.register(r'service-booking-complaints', views.ServiceBookingComplaintViewSet)
router.register(r'shares', views.ProductShareViewSet, basename='shares')
router.register(r'recommendations', views.AIRecommendationViewSet, basename='recommendations')
router.register(r'audit-logs', views.AuditLogViewSet)
router.register(r'fraud-signals', views.FraudSignalViewSet)
router.register(r'carts', views.CartViewSet)
router.register(r'cart-items', views.CartItemViewSet)
router.register(r'marketplace-orders', views.MarketplaceOrderViewSet, basename='marketplace-orders')
router.register(r'marketplace-complaints', views.MarketplaceComplaintViewSet)
router.register(r'marketplace-provider-orders', views.MarketplaceProviderOrderViewSet, basename='marketplace-provider-orders')

urlpatterns = [
    path(
        'discovery/',
        views.CommerceDiscoveryView.as_view(),
        name='commerce-discovery',
    ),
    path(
        'uploads/initiate/',
        views.CommerceUploadInitiateView.as_view(),
        name='commerce-upload-initiate',
    ),
    *router.urls,
    path(
        'payments/<uuid:payment_id>/satisfy/',
        views.ServiceBookingPaymentSatisfyView.as_view(),
        name='service-booking-payment-satisfy',
    ),
    path(
        'shops/<uuid:shop_id>/members/',
        views.ShopMembersByShopView.as_view(),
        name='shop-members',
    ),
]
