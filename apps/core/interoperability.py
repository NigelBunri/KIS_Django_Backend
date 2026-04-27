from __future__ import annotations

from typing import Any

from django.utils import timezone

from . import models


def _iso(dt):
    return dt.isoformat() if dt else None


def _patient_reference(patient: models.PatientMasterRecord) -> str:
    return f"Patient/{patient.mrn}"


def export_patient_fhir_bundle(patient: models.PatientMasterRecord) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []

    patient_resource = {
        "resourceType": "Patient",
        "id": str(patient.id),
        "identifier": [{"system": "KIS-MRN", "value": patient.mrn}],
        "name": [{"family": patient.last_name, "given": [patient.first_name]}],
        "gender": patient.gender,
        "birthDate": patient.dob.isoformat() if patient.dob else None,
        "telecom": [
            {"system": "phone", "value": patient.primary_contact.get("phone") or patient.primary_contact.get("phone_number")}
            for _ in [0]
            if isinstance(patient.primary_contact, dict)
            and (patient.primary_contact.get("phone") or patient.primary_contact.get("phone_number"))
        ]
        + [
            {"system": "email", "value": patient.primary_contact.get("email")}
            for _ in [0]
            if isinstance(patient.primary_contact, dict) and patient.primary_contact.get("email")
        ],
        "contact": [
            {
                "name": {"text": patient.emergency_contact.get("name")},
                "telecom": [{"system": "phone", "value": patient.emergency_contact.get("phone")}],
            }
            for _ in [0]
            if isinstance(patient.emergency_contact, dict) and patient.emergency_contact.get("name")
        ],
    }
    entries.append({"resource": patient_resource})

    for allergy in patient.allergies.all().order_by("-recorded_at"):
        entries.append(
            {
                "resource": {
                    "resourceType": "AllergyIntolerance",
                    "id": str(allergy.id),
                    "clinicalStatus": {"text": allergy.status},
                    "criticality": allergy.severity,
                    "code": {"text": allergy.agent},
                    "patient": {"reference": _patient_reference(patient)},
                    "recordedDate": _iso(allergy.recorded_at),
                    "reaction": [{"description": allergy.reaction}] if allergy.reaction else [],
                    "extension": [{"url": "kis:category", "valueString": allergy.category}] if allergy.category else [],
                }
            }
        )

    for medication in patient.medications.all().order_by("-created_at"):
        entries.append(
            {
                "resource": {
                    "resourceType": "MedicationRequest",
                    "id": str(medication.id),
                    "status": medication.status,
                    "subject": {"reference": _patient_reference(patient)},
                    "medicationCodeableConcept": {"text": medication.drug_name},
                    "dosageInstruction": [
                        {
                            "text": " ".join(
                                part
                                for part in [medication.route, medication.dosage, medication.frequency]
                                if part
                            ).strip(),
                        }
                    ],
                    "note": [{"text": medication.notes}] if medication.notes else [],
                }
            }
        )

    for vital in patient.vitals.all().order_by("-recorded_at"):
        entries.append(
            {
                "resource": {
                    "resourceType": "Observation",
                    "id": str(vital.id),
                    "status": "final",
                    "subject": {"reference": _patient_reference(patient)},
                    "code": {"text": vital.vital_type},
                    "valueQuantity": {"value": float(vital.value), "unit": vital.units},
                    "effectiveDateTime": _iso(vital.recorded_at),
                    "note": [{"text": vital.notes}] if vital.notes else [],
                }
            }
        )

    for problem in patient.problems.all().order_by("-created_at"):
        entries.append(
            {
                "resource": {
                    "resourceType": "Condition",
                    "id": str(problem.id),
                    "clinicalStatus": {"text": problem.clinical_status},
                    "verificationStatus": {"text": problem.verification_status},
                    "severity": {"text": problem.severity},
                    "code": {"text": problem.title, "coding": [{"system": problem.code_system, "code": problem.code}] if problem.code else []},
                    "subject": {"reference": _patient_reference(patient)},
                    "onsetDateTime": problem.onset_date.isoformat() if problem.onset_date else None,
                    "recordedDate": _iso(problem.created_at),
                    "note": [{"text": problem.notes}] if problem.notes else [],
                }
            }
        )

    for immunization in patient.immunizations.all().order_by("-administered_at", "-created_at"):
        entries.append(
            {
                "resource": {
                    "resourceType": "Immunization",
                    "id": str(immunization.id),
                    "status": immunization.status,
                    "vaccineCode": {"text": immunization.vaccine_name, "coding": [{"code": immunization.vaccine_code}] if immunization.vaccine_code else []},
                    "patient": {"reference": _patient_reference(patient)},
                    "occurrenceDateTime": _iso(immunization.administered_at),
                    "manufacturer": {"display": immunization.manufacturer} if immunization.manufacturer else None,
                    "lotNumber": immunization.lot_number or None,
                    "protocolApplied": [{"doseNumberPositiveInt": immunization.dose_number}] if immunization.dose_number else [],
                    "note": [{"text": immunization.notes}] if immunization.notes else [],
                }
            }
        )

    for procedure in patient.procedures.all().order_by("-performed_at", "-created_at"):
        entries.append(
            {
                "resource": {
                    "resourceType": "Procedure",
                    "id": str(procedure.id),
                    "status": procedure.status,
                    "code": {"text": procedure.procedure_name, "coding": [{"code": procedure.procedure_code}] if procedure.procedure_code else []},
                    "subject": {"reference": _patient_reference(patient)},
                    "performedDateTime": _iso(procedure.performed_at),
                    "location": {"display": procedure.location} if procedure.location else None,
                    "note": [{"text": procedure.notes}] if procedure.notes else [],
                }
            }
        )

    for encounter in patient.encounters.all().order_by("-created_at"):
        entries.append(
            {
                "resource": {
                    "resourceType": "Encounter",
                    "id": str(encounter.id),
                    "status": "finished",
                    "class": {"code": encounter.encounter_type},
                    "subject": {"reference": _patient_reference(patient)},
                    "period": {"start": _iso(encounter.created_at)},
                    "reasonCode": [{"text": encounter.summary}] if encounter.summary else [],
                }
            }
        )

    for document in patient.documents.all().order_by("-issued_at", "-created_at"):
        entries.append(
            {
                "resource": {
                    "resourceType": "DocumentReference",
                    "id": str(document.id),
                    "status": "current" if document.status == models.HealthDocument.STATUS_ACTIVE else "superseded",
                    "type": {"text": document.category},
                    "subject": {"reference": _patient_reference(patient)},
                    "date": _iso(document.issued_at or document.created_at),
                    "description": document.title,
                    "content": [
                        {
                            "attachment": {
                                "url": document.file_url,
                                "contentType": document.mime_type,
                                "title": document.title,
                            }
                        }
                    ] if document.file_url else [],
                }
            }
        )

    return {
        "resourceType": "Bundle",
        "type": "collection",
        "timestamp": timezone.now().isoformat(),
        "entry": entries,
    }


def import_patient_fhir_bundle(
    *,
    patient: models.PatientMasterRecord,
    bundle: dict[str, Any],
    actor,
) -> dict[str, Any]:
    entries = bundle.get("entry") if isinstance(bundle, dict) else None
    if not isinstance(entries, list):
        raise ValueError("Bundle entry list is required.")

    created = {
        "allergies": 0,
        "medications": 0,
        "problems": 0,
        "immunizations": 0,
        "procedures": 0,
        "documents": 0,
        "encounters": 0,
    }

    for entry in entries:
        resource = entry.get("resource") if isinstance(entry, dict) else None
        if not isinstance(resource, dict):
            continue
        resource_type = str(resource.get("resourceType") or "").strip()

        if resource_type == "AllergyIntolerance":
            agent = str((resource.get("code") or {}).get("text") or "").strip()
            if not agent:
                continue
            _, was_created = models.AllergyRecord.objects.get_or_create(
                patient=patient,
                agent=agent,
                defaults={
                    "severity": str(resource.get("criticality") or models.AllergyRecord.SEVERITY_MODERATE),
                    "status": str((resource.get("clinicalStatus") or {}).get("text") or models.AllergyRecord.STATUS_ACTIVE),
                    "reaction": str(((resource.get("reaction") or [{}])[0] or {}).get("description") or "").strip(),
                    "metadata": {"imported": True, "source": "fhir_bundle"},
                },
            )
            if was_created:
                created["allergies"] += 1

        elif resource_type == "MedicationRequest":
            drug_name = str((resource.get("medicationCodeableConcept") or {}).get("text") or "").strip()
            if not drug_name:
                continue
            _, was_created = models.MedicationOrder.objects.get_or_create(
                patient=patient,
                drug_name=drug_name,
                defaults={
                    "status": str(resource.get("status") or models.MedicationOrder.STATUS_REQUESTED),
                    "notes": str(((resource.get("note") or [{}])[0] or {}).get("text") or "").strip(),
                    "clinician": actor if getattr(actor, "is_authenticated", False) else None,
                    "metadata": {"imported": True, "source": "fhir_bundle"},
                },
            )
            if was_created:
                created["medications"] += 1

        elif resource_type == "Condition":
            title = str((resource.get("code") or {}).get("text") or "").strip()
            if not title:
                continue
            coding = (resource.get("code") or {}).get("coding") or []
            first_coding = coding[0] if coding else {}
            _, was_created = models.ProblemRecord.objects.get_or_create(
                patient=patient,
                title=title,
                defaults={
                    "code": str(first_coding.get("code") or "").strip(),
                    "code_system": str(first_coding.get("system") or "").strip(),
                    "clinical_status": str((resource.get("clinicalStatus") or {}).get("text") or models.ProblemRecord.STATUS_ACTIVE),
                    "verification_status": str((resource.get("verificationStatus") or {}).get("text") or models.ProblemRecord.VERIFICATION_PROVISIONAL),
                    "severity": str((resource.get("severity") or {}).get("text") or models.ProblemRecord.SEVERITY_MEDIUM),
                    "notes": str(((resource.get("note") or [{}])[0] or {}).get("text") or "").strip(),
                    "diagnosed_by": actor if getattr(actor, "is_authenticated", False) else None,
                    "metadata": {"imported": True, "source": "fhir_bundle"},
                },
            )
            if was_created:
                created["problems"] += 1

        elif resource_type == "Immunization":
            vaccine_name = str((resource.get("vaccineCode") or {}).get("text") or "").strip()
            if not vaccine_name:
                continue
            coding = (resource.get("vaccineCode") or {}).get("coding") or []
            first_coding = coding[0] if coding else {}
            _, was_created = models.ImmunizationRecord.objects.get_or_create(
                patient=patient,
                vaccine_name=vaccine_name,
                defaults={
                    "vaccine_code": str(first_coding.get("code") or "").strip(),
                    "status": str(resource.get("status") or models.ImmunizationRecord.STATUS_COMPLETED),
                    "manufacturer": str((resource.get("manufacturer") or {}).get("display") or "").strip(),
                    "lot_number": str(resource.get("lotNumber") or "").strip(),
                    "administered_by": actor if getattr(actor, "is_authenticated", False) else None,
                    "metadata": {"imported": True, "source": "fhir_bundle"},
                },
            )
            if was_created:
                created["immunizations"] += 1

        elif resource_type == "Procedure":
            procedure_name = str((resource.get("code") or {}).get("text") or "").strip()
            if not procedure_name:
                continue
            coding = (resource.get("code") or {}).get("coding") or []
            first_coding = coding[0] if coding else {}
            _, was_created = models.ProcedureRecord.objects.get_or_create(
                patient=patient,
                procedure_name=procedure_name,
                defaults={
                    "procedure_code": str(first_coding.get("code") or "").strip(),
                    "status": str(resource.get("status") or models.ProcedureRecord.STATUS_COMPLETED),
                    "location": str((resource.get("location") or {}).get("display") or "").strip(),
                    "performed_by": actor if getattr(actor, "is_authenticated", False) else None,
                    "metadata": {"imported": True, "source": "fhir_bundle"},
                },
            )
            if was_created:
                created["procedures"] += 1

        elif resource_type == "DocumentReference":
            title = str(resource.get("description") or "").strip()
            attachment = ((resource.get("content") or [{}])[0] or {}).get("attachment") or {}
            if not title:
                title = str(attachment.get("title") or "").strip()
            if not title:
                continue
            _, was_created = models.HealthDocument.objects.get_or_create(
                patient=patient,
                title=title,
                defaults={
                    "category": str((resource.get("type") or {}).get("text") or models.HealthDocument.CATEGORY_GENERAL),
                    "file_url": str(attachment.get("url") or "").strip(),
                    "mime_type": str(attachment.get("contentType") or "").strip(),
                    "source_type": models.HealthDocument.SOURCE_IMPORT,
                    "uploaded_by": actor if getattr(actor, "is_authenticated", False) else None,
                    "metadata": {"imported": True, "source": "fhir_bundle"},
                },
            )
            if was_created:
                created["documents"] += 1

        elif resource_type == "Encounter":
            encounter_type = str((resource.get("class") or {}).get("code") or "clinical").strip() or "clinical"
            summary = str(((resource.get("reasonCode") or [{}])[0] or {}).get("text") or "").strip()
            _, was_created = models.EncounterNote.objects.get_or_create(
                patient=patient,
                encounter_type=encounter_type,
                summary=summary,
                defaults={
                    "clinician": actor if getattr(actor, "is_authenticated", False) else None,
                    "ai_insights": {"imported": True, "source": "fhir_bundle"},
                },
            )
            if was_created:
                created["encounters"] += 1

    return created
