from django.urls import path
from rest_framework.routers import DefaultRouter
from . import views

router = DefaultRouter()
router.register(r'shops', views.ShopViewSet)
router.register(r'shop-verifications', views.ShopVerificationRequestViewSet)
router.register(r'products', views.ProductViewSet)
router.register(r'product-ratings', views.ProductRatingViewSet)
router.register(r'product-categories', views.ProductCategoryViewSet)
router.register(r'product-auth-checks', views.ProductAuthenticityCheckViewSet)
router.register(r'orders', views.OrderViewSet)
router.register(r'payments', views.PaymentViewSet)
router.register(r'promotions', views.PromotionViewSet)
router.register(r'subscriptions', views.SubscriptionViewSet)
router.register(r'loyalty', views.LoyaltyPointViewSet)
router.register(r'follows', views.ShopFollowViewSet)
router.register(r'shop-members', views.ShopTeamMemberViewSet)
router.register(r'shop-services', views.ShopServiceViewSet)
router.register(r'service-bookings', views.ServiceBookingViewSet)
router.register(r'service-booking-complaints', views.ServiceBookingComplaintViewSet)
router.register(r'shares', views.ProductShareViewSet)
router.register(r'recommendations', views.AIRecommendationViewSet)
router.register(r'audit-logs', views.AuditLogViewSet)
router.register(r'fraud-signals', views.FraudSignalViewSet)
router.register(r'carts', views.CartViewSet)
router.register(r'cart-items', views.CartItemViewSet)

urlpatterns = [
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
