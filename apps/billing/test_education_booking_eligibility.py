"""resolve_payout_entity's TARGET_EDUCATION_BOOKING case previously
required booking.course_id to be set, which silently exempted
program/class_session/event bookings (course_id is only ever set for
course purchases) from the payment-setup eligibility check in
create_direct_payment_intent — they'd sail through to
_ensure_provider_payment_link with no institution payout account
configured, land on payment_url=None, and the buyer would just see a
generic "waiting for a provider checkout link" message instead of a
clear "this seller hasn't finished setting up payments" one."""

from django.test import TestCase

from apps.accounts.models import User
from apps.billing.direct_payments import create_direct_payment_intent, resolve_payout_entity
from apps.billing.eligibility import PaymentSetupRequiredError
from apps.billing.models import DirectPaymentIntent
from apps.broadcasts.models import (
    EducationInstitution,
    EducationInstitutionBooking,
    EducationInstitutionBroadcast,
)


class EducationBookingPayoutEligibilityTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(phone="+237670008001", country="CM", password="pass1234")
        self.buyer = User.objects.create_user(phone="+237670008002", country="CM", password="pass1234")
        self.institution = EducationInstitution.objects.create(
            owner=self.owner,
            name="No Payout Academy",
        )
        self.broadcast = EducationInstitutionBroadcast.objects.create(
            institution=self.institution,
            created_by=self.owner,
            broadcast_kind="class_session",
            title="Live Q&A",
            summary="A live session.",
            description="A paid live session with no course attached.",
            booking_enabled=True,
            price_amount="10.00",
            price_currency="USD",
            status="published",
        )

    def _create_booking(self):
        return EducationInstitutionBooking.objects.create(
            institution=self.institution,
            broadcast=self.broadcast,
            user=self.buyer,
            amount_cents=1000,
            currency="USD",
        )

    def test_resolve_payout_entity_resolves_institution_without_course_id(self):
        booking = self._create_booking()
        self.assertIsNone(booking.course_id)

        entity = resolve_payout_entity(DirectPaymentIntent.TARGET_EDUCATION_BOOKING, booking.id)

        self.assertEqual(entity, self.institution)

    def test_checkout_without_course_id_is_gated_on_missing_payout(self):
        booking = self._create_booking()

        with self.assertRaises(PaymentSetupRequiredError) as ctx:
            create_direct_payment_intent(
                user=self.buyer,
                target_type=DirectPaymentIntent.TARGET_EDUCATION_BOOKING,
                target_id=booking.id,
            )

        self.assertIn("hasn't finished setting up how they get paid", ctx.exception.detail["message"])
