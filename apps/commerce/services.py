# Heavy-lifting: verification adapters, fraud engine, recommendation engine, payment hooks
import hashlib, uuid
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, Any, Iterable, List
from io import BytesIO
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from apps.billing.services import (
    get_wallet_account,
    lock_wallet_funds_for_booking,
    release_locked_booking_funds,
    refund_locked_booking_funds,
)
from apps.billing.models import WalletTransaction
from apps.notifications.services import create_notification
from apps.core.money import frontend_kisc_major_to_usd_cents, parse_decimal_amount
from .constants import KIS_COIN_CODE
from .models import (
    ShopVerificationRequest,
    ProductAuthenticityCheck,
    Order,
    Product,
    Shop,
    ShopRole,
    ShopTeamMember,
    Cart,
    CartItem,
    MarketplaceOrder,
    MarketplaceOrderItem,
    MarketplaceOrderStatus,
    MarketplaceComplaint,
)


def run_shop_verification_checks(req: ShopVerificationRequest) -> Dict[str, Any]:
    """
    Combine multiple checks:
    - Identity document OCR & match
    - Business registry verification (adapter)
    - Social signals (followers, age)
    - AI-based image forensics on uploaded docs
    - Risk scoring
    Returns dict with status, risk_score, badges
    """
    # Stubbed implementation for free tier: basic heuristic checks
    docs = req.documents or []
    badges = []
    risk = 0.0
    if len(docs) >= 2:
        badges.append('documents-submitted')
        risk -= 0.2
    # check owner's history - stub
    risk += 0.1
    # if business reg doc exists, lower risk
    if any(d.get('type')=='BUSINESS_REG' for d in docs):
        badges.append('business-registered')
        risk -= 0.3
    status = 'APPROVED' if risk < 0.5 else 'PENDING'
    return {'status': status, 'risk_score': max(0.0, min(1.0, risk)), 'badges': badges}


def run_product_auth_check(pac: ProductAuthenticityCheck) -> Dict[str, Any]:
    """
    Composite authenticity checks:
    - Image similarity checks against known-good catalog (adapter)
    - Text & metadata cross-check (brand info)
    - Blockchain certificate verification (adapter)
    - AI-based multimodal detector (image + text) for counterfeits
    Returns: status, confidence, result dict with proof
    """
    prod = pac.product
    # free stub: if product.sku contains 'auth' treat as verified
    confidence = 0.0
    result = {'proof': {}}
    status = 'FLAGGED'
    if 'auth' in prod.sku.lower():
        status = 'VERIFIED'
        confidence = 0.92
        result['proof'] = {'method': 'sku_heuristic', 'note': 'SKU contains auth'}
    else:
        # simple hash of name as deterministic placeholder
        h = hashlib.sha1(prod.name.encode('utf-8')).hexdigest()
        confidence = int(h[:2],16)/255.0
        status = 'VERIFIED' if confidence > 0.7 else 'FLAGGED'
        result['proof'] = {'method': 'name_hash', 'hash': h}
    return {'status': status, 'confidence': confidence, 'result': result}


def compute_fraud_for_order(order: Order) -> (float, Dict[str, Any]): # type: ignore
    """
    Simple fraud scoring combining heuristics:
    - high order value relative to average
    - new account / little history
    - mismatched shipping & billing
    Returns score 0..1 and detail dict
    """
    score = 0.0
    details = {}
    # heuristic: large orders
    if float(order.total) > 1000000:
        score += 0.5
        details['large_order'] = True
    # new account heuristic placeholder
    # TODO integrate with user profile
    score = min(1.0, score)
    return score, details


def build_recommendations(user_id) -> List[Dict[str, Any]]:
    """
    Use simple collaborative/content hybrid stub:
    - fetch popular products
    - score by ai_score and shop trust
    Returns list of {type:'Product', id:uuid, score:float, reason:str}
    """
    popular = Product.objects.filter(is_active=True).order_by('-ai_score')[:10]
    out = []
    for p in popular:
        score = float(p.ai_score or 0.0)
        if p.shop.is_verified:
            score += 0.1
        out.append({'type':'Product','id':p.id,'score':score,'reason':'popular/ai_score'})
    return out


def place_marketplace_order(*, buyer, shop_id, items, metadata=None):
    """Create a marketplace order, lock the buyer funds, and persist the order/items."""
    if not items:
        raise ValidationError('At least one order item is required.')
    shop = Shop.objects.filter(id=shop_id).first()
    if not shop:
        raise ValidationError('Shop not found for this marketplace order.')

    normalized_items = _normalize_marketplace_items(items, shop)
    total_cents = _calculate_items_total_cents(normalized_items)
    if total_cents <= 0:
        raise ValidationError('Order total must be greater than zero.')

    wallet = get_wallet_account(buyer)
    if wallet.balance_cents < total_cents:
        raise ValidationError('Insufficient wallet balance for this marketplace order.')
    metadata_payload = {**(metadata or {})}
    cart_id = metadata_payload.get('cart_id')
    metadata_payload.setdefault('items', len(normalized_items))
    metadata_payload.setdefault('total_amount_cents', total_cents)
    reference = f'marketplace-{buyer.id}-{uuid.uuid4().hex}'

    with transaction.atomic():
        _, buyer_tx = lock_wallet_funds_for_booking(
            user=buyer,
            amount_cents=total_cents,
            reference=reference,
            meta={'source': 'marketplace_order', 'shop_id': str(shop_id), **metadata_payload},
        )
        order = MarketplaceOrder.objects.create(
            buyer=buyer,
            shop=shop,
            total_amount=Decimal(total_cents) / Decimal('100'),
            currency=KIS_COIN_CODE,
            status=MarketplaceOrderStatus.TEMPORAL,
            metadata=metadata_payload,
            buyer_debit_transaction=buyer_tx,
        )
        buyer_tx.meta = {**(buyer_tx.meta or {}), 'order_id': str(order.id)}
        buyer_tx.save(update_fields=['meta'])
        items_to_create = [
            MarketplaceOrderItem(
                order=order,
                product=item['product'],
                variant_id=item['variant_id'],
                quantity=item['quantity'],
                unit_price_cents=item['unit_price_cents'],
                selected_attributes=item['selected_attributes'],
                custom_description=item['custom_description'],
            )
            for item in normalized_items
        ]
        MarketplaceOrderItem.objects.bulk_create(items_to_create)
        if cart_id:
            Cart.objects.filter(id=cart_id, status='active').update(status='checked_out')
    return order


def _normalize_marketplace_items(items, shop):
    """Ensure items are tied to the provided shop and collect needed metadata."""
    product_ids = {str(item.get('product_id')) for item in items if item.get('product_id')}
    if not product_ids:
        raise ValidationError('Each marketplace order item must reference a product_id.')
    products = Product.objects.filter(id__in=product_ids, shop=shop).select_related('shop')
    if products.count() != len(product_ids):
        raise ValidationError('One or more products are invalid or not part of this shop.')
    product_map = {str(product.id): product for product in products}
    normalized_items = []
    seen = set()
    for entry in items:
        product_id = str(entry.get('product_id') or '')
        variant_id = str(entry.get('variant_id') or '')
        if (product_id, variant_id) in seen:
            raise ValidationError('Duplicate product + variant combinations are not allowed.')
        seen.add((product_id, variant_id))
        product = product_map.get(product_id)
        if not product:
            raise ValidationError(f'Product {product_id} is invalid or unavailable in this shop.')
        quantity = max(1, int(entry.get('quantity') or 1))
        unit_price_cents = _ensure_unit_price_cents(entry)
        if unit_price_cents <= 0:
            raise ValidationError('Each marketplace order item must have a positive price.')
        normalized_items.append({
            'product': product,
            'variant_id': variant_id,
            'quantity': quantity,
            'unit_price_cents': unit_price_cents,
            'selected_attributes': entry.get('selected_attributes') or {},
            'custom_description': entry.get('custom_description') or '',
        })
    return normalized_items


def _calculate_items_total_cents(normalized_items):
    return sum(item['quantity'] * item['unit_price_cents'] for item in normalized_items)


def _ensure_unit_price_cents(entry):
    """Round and convert the incoming price to whole cents."""
    raw_cents = entry.get('unit_price_cents')
    if raw_cents is not None:
        try:
            return int(Decimal(raw_cents).to_integral_value(rounding=ROUND_HALF_UP))
        except Exception:
            return 0
    raw_price = entry.get('unit_price') or entry.get('price') or entry.get('amount') or 0
    return _decimal_price_to_cents(raw_price)


def _order_total_cents(order):
    buyer_tx = getattr(order, 'buyer_debit_transaction', None)
    if buyer_tx and int(getattr(buyer_tx, 'amount_cents', 0) or 0) > 0:
        return int(buyer_tx.amount_cents)

    order_items = getattr(order, 'items', None)
    if order_items is not None:
        try:
            item_total = sum(
                int(getattr(item, 'unit_price_cents', 0) or 0) * int(getattr(item, 'quantity', 0) or 0)
                for item in order_items.all()
            )
        except Exception:
            item_total = 0
        if item_total > 0:
            return item_total

    return _decimal_price_to_cents(order.total_amount)


def _decimal_price_to_cents(value):
    """Convert frontend major-unit KISC values to USD cents, rounding half-up."""
    parsed = parse_decimal_amount(value)
    if parsed is None:
        return 0
    return int(frontend_kisc_major_to_usd_cents(parsed) or 0)


def cancel_marketplace_order(order):
    """Return locked buyer funds and mark the order as cancelled."""
    if order.status != MarketplaceOrderStatus.TEMPORAL:
        raise ValidationError('Only temporal marketplace orders can be cancelled.')
    total_cents = _order_total_cents(order)
    reference = f'marketplace-refund-{order.id}'
    refund_locked_booking_funds(
        payer=order.buyer,
        amount_cents=total_cents,
        reference=reference,
        meta={'order_id': str(order.id), 'source': 'marketplace_refund'},
    )
    order.status = MarketplaceOrderStatus.CANCELLED
    order.metadata = {**(order.metadata or {}), 'cancelled_at': timezone.now().isoformat()}
    order.save(update_fields=['status', 'metadata'])
    transaction.on_commit(lambda: _schedule_marketplace_order_deletion(order.id))
    return order


def satisfy_marketplace_order(order):
    """Release locked order funds to the provider and mark the order satisfied."""
    allowed_statuses = {
        MarketplaceOrderStatus.TEMPORAL,
        MarketplaceOrderStatus.AWAITING_SATISFACTION,
    }
    if order.status not in allowed_statuses:
        raise ValidationError('Only pending orders may be satisfied.')
    total_cents = _order_total_cents(order)
    reference = f'marketplace-payout-{order.id}'
    release_locked_booking_funds(
        payer=order.buyer,
        provider=order.shop.owner,
        amount_cents=total_cents,
        reference=reference,
        meta={'order_id': str(order.id), 'source': 'marketplace_payout'},
    )
    provider_tx = WalletTransaction.objects.create(
        user=order.shop.owner,
        provider='internal',
        method='marketplace_payout',
        amount_cents=total_cents,
        currency=order.currency,
        status='success',
        tx_ref=reference,
        meta={'order_id': str(order.id)},
    )
    order.status = MarketplaceOrderStatus.SATISFIED
    order.provider_credit_transaction = provider_tx
    order.metadata = {
        **(order.metadata or {}),
        'satisfied_at': timezone.now().isoformat(),
        'provider_completed_at': order.metadata.get('provider_completed_at'),
    }
    order.save(update_fields=['status', 'metadata', 'provider_credit_transaction'])
    return order


def complete_marketplace_order(order):
    """Provider marks the dispute-free order as completed."""
    previous_status = order.status
    if previous_status == MarketplaceOrderStatus.CANCELLED:
        raise ValidationError('Cancelled orders cannot be completed.')
    metadata = {**(order.metadata or {})}
    metadata['provider_completed_at'] = timezone.now().isoformat()
    order.status = MarketplaceOrderStatus.COMPLETED
    order.metadata = metadata
    order.save(update_fields=['status', 'metadata'])
    if previous_status not in (MarketplaceOrderStatus.SATISFIED, MarketplaceOrderStatus.COMPLETED):
        _notify_buyer_provider_completed(order)
    return order


def create_marketplace_complaint(*, order, user, text, attachment=None):
    """Record a buyer/provider complaint against a marketplace order."""
    complaint = MarketplaceComplaint.objects.create(
        order=order,
        user=user,
        text=text,
        attachment=attachment,
    )
    if order.status != MarketplaceOrderStatus.COMPLAINT:
        order.status = MarketplaceOrderStatus.COMPLAINT
        order.metadata = {**(order.metadata or {}), 'complaint_at': timezone.now().isoformat()}
        order.save(update_fields=['status', 'metadata'])
    return complaint


def _normalize_cart_items(cart_items):
    if isinstance(cart_items, Cart):
        cart_items = cart_items.items.select_related('product').all()
    if isinstance(cart_items, Iterable):
        items = list(cart_items)
    else:
        raise ValidationError('Cart items must be provided as an iterable.')
    if not items:
        raise ValidationError('Cannot place an order with an empty cart.')
    first_shop = items[0].cart.shop
    for item in items:
        if getattr(item.cart, 'shop_id') != first_shop.id:
            raise ValidationError('All cart items must belong to the same shop.')
    return items


def _cart_item_payload(item: CartItem):
    unit_price = item.price_snapshot or Decimal('0')
    unit_price_cents = _decimal_price_to_cents(unit_price)
    return {
        'product_id': item.product_id,
        'variant_id': item.variant or '',
        'quantity': max(1, item.quantity or 1),
        'unit_price_cents': unit_price_cents,
        'selected_attributes': item.selected_attributes or {},
        'custom_description': item.custom_description or '',
    }


def place_marketplace_order_from_cart(*, cart_items, buyer, metadata=None):
    items = _normalize_cart_items(cart_items)
    shop = items[0].cart.shop
    payload = [ _cart_item_payload(item) for item in items ]
    metadata_payload = {**(metadata or {}), 'cart_id': str(items[0].cart_id)}
    return place_marketplace_order(buyer=buyer, shop_id=shop.id, items=payload, metadata=metadata_payload)


def _fetch_buyer_order(order_id, buyer):
    order = MarketplaceOrder.objects.filter(id=order_id, buyer=buyer).first()
    if not order:
        raise ValidationError('Marketplace order not found.')
    return order


def cancel_marketplace_order_by_id(*, order_id, buyer):
    order = _fetch_buyer_order(order_id, buyer)
    return cancel_marketplace_order(order)


def satisfy_marketplace_order_by_id(*, order_id, buyer):
    order = _fetch_buyer_order(order_id, buyer)
    return satisfy_marketplace_order(order)


def _provider_can_manage_shop(user, shop):
    if shop.owner_id == user.id:
        return True
    return ShopTeamMember.objects.filter(
        shop=shop,
        user=user,
        role__in=[ShopRole.ADMIN, ShopRole.MANAGER],
    ).exists()


def provider_complete_marketplace_order_by_id(*, order_id, provider):
    order = MarketplaceOrder.objects.select_related('shop').filter(id=order_id).first()
    if not order:
        raise ValidationError('Marketplace order not found.')
    if not _provider_can_manage_shop(provider, order.shop):
        raise ValidationError('Only the provider or managers can complete this order.')
    completed_order = mark_provider_completed(order)
    return completed_order


def create_marketplace_complaint_from_data(*, order_id, user, text, attachment=None):
    order = MarketplaceOrder.objects.filter(id=order_id).first()
    if not order:
        raise ValidationError('Marketplace order not found.')
    if order.buyer_id != user.id and not _provider_can_manage_shop(user, order.shop):
        raise ValidationError('You are not authorized to complain about this order.')
    return create_marketplace_complaint(order=order, user=user, text=text, attachment=attachment)


def _notify_buyer_provider_completed(order):
    create_notification(
        user_id=str(order.buyer_id),
        type='marketplace.order.provider_completed',
        title=f'{order.shop.name} marked your order as complete',
        body=(
            f"The provider says your order is complete. Please confirm satisfaction within 3 days "
            "or file a complaint from the order details page."
        ),
        target_type='marketplace_order',
        target_id=str(order.id),
        context={'order_id': str(order.id), 'shop_id': str(order.shop_id)},
        dedup_key=f'marketplace-provider-completed-{order.id}',
    )


def mark_provider_completed(order):
    if order.status != MarketplaceOrderStatus.TEMPORAL:
        raise ValidationError('Only temporal orders may be marked complete by the provider.')
    now = timezone.now()
    deadline = now + timezone.timedelta(days=3)
    metadata = {
        **(order.metadata or {}),
        'provider_completed_at': now.isoformat(),
        'awaiting_satisfaction_until': deadline.isoformat(),
    }
    order.status = MarketplaceOrderStatus.AWAITING_SATISFACTION
    order.metadata = metadata
    order.save(update_fields=['status', 'metadata'])
    _notify_buyer_provider_completed(order)
    _schedule_marketplace_order_auto_satisfaction(order.id)
    return order


def _schedule_marketplace_order_auto_satisfaction(order_id):
    import importlib

    tasks_module = importlib.import_module('apps.commerce.tasks')
    auto_task = getattr(tasks_module, 'auto_satisfy_marketplace_order', None)
    if auto_task:
        auto_task.apply_async(args=[str(order_id)], countdown=timezone.timedelta(days=3).total_seconds())


def build_marketplace_receipt(order):
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=letter)
    c.setFont('Helvetica-Bold', 16)
    c.drawString(60, 750, f'Marketplace Receipt - Order {order.id}')
    c.setFont('Helvetica', 11)
    c.drawString(60, 730, f'Buyer: {order.buyer}')
    c.drawString(60, 716, f'Provider: {order.shop.name}')
    c.drawString(60, 702, f'Status: {order.get_status_display()}')
    c.drawString(60, 688, f'Total: {order.total_amount} {order.currency}')
    c.drawString(60, 674, f'Created: {order.created_at.strftime("%Y-%m-%d %H:%M:%S")}')
    y = 640
    c.setFont('Helvetica-Bold', 12)
    c.drawString(60, y, 'Items:')
    y -= 18
    c.setFont('Helvetica', 11)
    for item in order.items.all():
        line = f'- {item.product.name} x{item.quantity} @ {item.unit_price_cents / Decimal('100')} {order.currency}'
        c.drawString(70, y, line)
        y -= 14
        if y < 80:
            c.showPage()
            y = 750
    c.showPage()
    c.save()
    buffer.seek(0)
    return buffer


def _schedule_marketplace_order_deletion(order_id):
    """Enqueue deletion of a cancelled marketplace order after a delay."""
    import importlib

    tasks_module = importlib.import_module('apps.commerce.tasks')
    delete_task = getattr(tasks_module, 'delete_cancelled_marketplace_order', None)
    if delete_task:
        delete_task.apply_async(args=[str(order_id)], countdown=7200)
