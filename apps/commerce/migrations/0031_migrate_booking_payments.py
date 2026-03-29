from __future__ import annotations

from django.db import migrations


def migrate_payments(apps, schema_editor):
    ServiceBooking = apps.get_model('commerce', 'ServiceBooking')
    ServiceBookingPayment = apps.get_model('commerce', 'ServiceBookingPayment')
    ServiceBookingEscrow = apps.get_model('commerce', 'ServiceBookingEscrow')

    ESCROW_STATUS_PENDING = 'pending'
    ESCROW_STATUS_DISPUTE = 'dispute'
    ESCROW_STATUS_RELEASED = 'released'
    ESCROW_STATUS_REFUNDED = 'refunded'
    ESCROW_STATUS_AWAITING = 'awaiting_satisfaction'

    PAYMENT_STATUS_PENDING = 'pending'
    PAYMENT_STATUS_PAID = 'paid'
    PAYMENT_STATUS_REFUNDED = 'refunded'
    PAYMENT_STATUS_SATISFIED = 'satisfied'

    pending_statuses = {
        ESCROW_STATUS_PENDING,
        ESCROW_STATUS_DISPUTE,
    }

    for booking in ServiceBooking.objects.select_related('escrow').all():
        if ServiceBookingPayment.objects.filter(booking=booking).exists():
            continue

        escrow = getattr(booking, 'escrow', None)
        payment_status = PAYMENT_STATUS_PENDING
        paid_at = None
        satisfied_at = booking.payer_satisfied_at
        if booking.payer_satisfied_at:
            payment_status = PAYMENT_STATUS_SATISFIED
        elif escrow:
            if escrow.status == ESCROW_STATUS_RELEASED:
                payment_status = PAYMENT_STATUS_PAID
                paid_at = escrow.released_at
            elif escrow.status == ESCROW_STATUS_REFUNDED:
                payment_status = PAYMENT_STATUS_REFUNDED
                paid_at = escrow.refunded_at
            elif escrow.status == ESCROW_STATUS_AWAITING:
                payment_status = PAYMENT_STATUS_PAID
                paid_at = escrow.locked_at
            elif escrow.status not in pending_statuses:
                payment_status = PAYMENT_STATUS_PAID
                paid_at = escrow.locked_at

        ServiceBookingPayment.objects.create(
            booking=booking,
            amount_cents=booking.deposit_cents or booking.price_cents or 0,
            payment_status=payment_status,
            paid_at=paid_at,
            transaction_reference=booking.payment_tx_ref,
            satisfied_at=satisfied_at,
        )


class Migration(migrations.Migration):

    dependencies = [
        ('commerce', '0030_servicebookingpayment'),
    ]

    operations = [
        migrations.RunPython(
            migrate_payments,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
