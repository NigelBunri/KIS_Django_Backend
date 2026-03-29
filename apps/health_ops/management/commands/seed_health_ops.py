from django.core.management.base import BaseCommand

from apps.health_ops.models import EngineRegistry, EngineStepDefinition


ENGINE_DEFINITIONS = [
    ("video", "Video Consultation Engine", "video", 5),
    ("secure_messaging", "Secure Messaging Engine", "chat", 4),
    ("e_prescription", "E-Prescription Engine", "file", 4),
    ("lab_order", "Lab Order Engine", "file", 4),
    ("imaging_order", "Imaging Order Engine", "list", 4),
    ("admission_bed", "Admission & Bed Management Engine", "list", 4),
    ("emergency_dispatch", "Emergency Dispatch Engine", "bell", 4),
    ("pharmacy_fulfillment", "Pharmacy & Fulfillment Engine", "cart", 4),
    ("payment_billing", "Payment & Billing Engine", "cart", 4),
    ("ehr_records", "EHR / Health Records Engine", "file", 4),
    ("home_logistics", "Home Logistics Engine", "list", 4),
    ("wellness_program", "Wellness Program Engine", "heart", 4),
    ("notification_reminder", "Notification & Reminder Engine", "bell", 4),
]

ENGINE_STEP_BLUEPRINTS = {
    "video": [
        ("confirm_identity", "Confirm identity", "Confirm participant identity."),
        ("test_mic_camera", "Test mic/camera", "Complete pre-join device checks."),
        ("confirm_consent", "Confirm consent", "Accept consent and recording policy."),
        ("join_session", "Join session", "Enter the live consultation room."),
        ("post_session_summary", "Post-session summary", "Capture summary before completion."),
    ],
    "secure_messaging": [
        ("open_thread", "Open thread", "Open secure case thread."),
        ("send_message", "Send first message", "Send first secure message."),
        ("attach_files", "Attach files", "Attach files or voice notes if needed."),
        ("close_thread", "Close thread", "Close thread after case resolution."),
    ],
    "ehr_records": [
        ("review_timeline", "Review timeline", "Review historical timeline."),
        ("add_clinical_note", "Add clinical note", "Add and validate clinical note."),
        ("attach_document", "Attach document", "Upload records and attachments."),
        ("finalize_ehr_entry", "Finalize entry", "Finalize and lock EHR entry."),
    ],
    "lab_order": [
        ("select_tests", "Select tests", "Choose required lab tests."),
        ("set_priority", "Set priority", "Define order priority and instructions."),
        ("confirm_collection", "Confirm collection", "Confirm sample collection workflow."),
        ("submit_order", "Submit order", "Submit the lab order for processing."),
    ],
    "imaging_order": [
        ("select_scan", "Select scan", "Choose imaging scan type."),
        ("screen_contraindications", "Screen contraindications", "Complete pre-scan screening."),
        ("book_slot", "Book slot", "Book imaging slot."),
        ("track_report", "Track report", "Track radiologist report completion."),
    ],
    "admission_bed": [
        ("admission_reason", "Admission reason", "Capture primary reason for admission."),
        ("insurance_verification", "Insurance verification", "Verify insurance or payment details."),
        ("bed_assignment", "Bed assignment", "Assign ward and bed."),
        ("admission_confirmation", "Admission confirmation", "Confirm admission details."),
    ],
    "emergency_dispatch": [
        ("capture_location", "Capture location", "Capture emergency location."),
        ("triage_form", "Triage form", "Complete emergency triage form."),
        ("dispatch_ambulance", "Dispatch ambulance", "Dispatch ambulance and crew."),
        ("track_response", "Track response", "Track in-transit and arrival updates."),
    ],
    "pharmacy_fulfillment": [
        ("verify_prescription", "Verify prescription", "Verify prescription validity."),
        ("validate_inventory", "Validate inventory", "Validate stock and substitutions."),
        ("confirm_delivery", "Confirm delivery", "Confirm pickup or delivery preference."),
        ("fulfillment_tracking", "Fulfillment tracking", "Track preparation and delivery state."),
    ],
    "payment_billing": [
        ("review_charges", "Review charges", "Review line items and insurance offsets."),
        ("select_payment_method", "Select payment method", "Select payment provider and preferred method."),
        ("authorize_payment", "Authorize payment", "Authorize and capture payment."),
        ("issue_receipt", "Issue receipt", "Issue receipt and finalize billing."),
    ],
    "home_logistics": [
        ("select_logistics_mode", "Select logistics mode", "Choose visit, pickup, or delivery mode."),
        ("schedule_window", "Schedule window", "Set requested service window."),
        ("assign_route", "Assign route", "Assign route, rider, or nurse."),
        ("track_eta", "Track ETA", "Track transit and arrival updates."),
    ],
    "wellness_program": [
        ("enroll_program", "Enroll program", "Enroll into wellness program."),
        ("set_goals", "Set goals", "Set wellness goals and milestones."),
        ("track_habits", "Track habits", "Log habits, streaks, and progress."),
        ("review_progress", "Review progress", "Review and confirm wellness progress."),
    ],
    "notification_reminder": [
        ("select_channels", "Select channels", "Select SMS, email, push, or WhatsApp channels."),
        ("configure_rules", "Configure rules", "Configure reminder rules and cadence."),
        ("schedule_reminders", "Schedule reminders", "Schedule next reminder windows."),
        ("confirm_delivery", "Confirm delivery", "Confirm reminder delivery state."),
    ],
}


class Command(BaseCommand):
    help = "Seed fixed health engines and base steps for DHOS."

    def handle(self, *args, **options):
        created = 0
        step_created = 0
        for code, name, category, step_count in ENGINE_DEFINITIONS:
            engine, was_created = EngineRegistry.objects.get_or_create(
                code=code,
                defaults={
                    "name": name,
                    "category": category,
                    "description": f"Core {name} workflow engine.",
                    "is_fixed": True,
                    "is_active": True,
                    "schema_version": 1,
                    "default_step_count": step_count,
                },
            )
            if was_created:
                created += 1

            blueprints = ENGINE_STEP_BLUEPRINTS.get(code) or [
                (f"step_{idx + 1}", f"Step {idx + 1}", f"Guided step {idx + 1} for {engine.name}.")
                for idx in range(step_count)
            ]
            existing_by_key = {
                str(row.step_key): row
                for row in EngineStepDefinition.objects.filter(engine=engine).only("id", "step_key", "step_order")
            }
            used_orders = {
                int(row.step_order)
                for row in existing_by_key.values()
                if row.step_order is not None
            }
            for idx, (step_key, step_title, step_description) in enumerate(blueprints):
                if step_key in existing_by_key:
                    continue
                preferred_order = idx + 1
                final_order = preferred_order
                if final_order in used_orders:
                    final_order = (max(used_orders) + 1) if used_orders else 1
                EngineStepDefinition.objects.create(
                    engine=engine,
                    step_key=step_key,
                    title=step_title,
                    description=step_description,
                    step_order=final_order,
                    validation_schema={},
                    completion_rule={"required": True},
                    is_required=True,
                )
                used_orders.add(final_order)
                step_created += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Seed complete. Engines created: {created}. Steps created: {step_created}."
            )
        )
