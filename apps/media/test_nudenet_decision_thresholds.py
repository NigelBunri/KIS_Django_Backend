"""
Direct coverage for build_nudenet_decision's confidence-threshold ladder -
the policy layer that turns a raw (label, score) into approved/rejected/
needs_review. Purely synthetic inputs (a label string + a float score) -
no image, video, or any real/simulated illicit content is used or needed,
matching the explicit instruction to test the DECISION MODEL, not chase
a live positive trigger against real content.
"""
from django.test import SimpleTestCase

from apps.media.safety import NUDENET_AUTO_BLOCK_THRESHOLD, build_nudenet_decision


class BuildNudenetDecisionThresholdTests(SimpleTestCase):
    def test_clearly_safe_no_detection_is_passed(self):
        decision = build_nudenet_decision(None, 0.0)
        self.assertEqual(decision.status, "passed")
        self.assertFalse(decision.quarantine)
        self.assertFalse(decision.requires_review)

    def test_clearly_prohibited_high_confidence_is_blocked(self):
        decision = build_nudenet_decision("EXPOSED_GENITALIA_F", 0.98)
        self.assertEqual(decision.status, "blocked")
        self.assertTrue(decision.quarantine)
        self.assertFalse(decision.requires_review)
        self.assertIn("nudenet_explicit", decision.reason)

    def test_exactly_at_threshold_is_blocked(self):
        # >= is the documented boundary, not > - a score exactly at the
        # threshold must not slip through as "ambiguous".
        decision = build_nudenet_decision("EXPOSED_GENITALIA_F", NUDENET_AUTO_BLOCK_THRESHOLD)
        self.assertEqual(decision.status, "blocked")

    def test_ambiguous_low_confidence_detection_needs_review(self):
        decision = build_nudenet_decision("EXPOSED_GENITALIA_F", NUDENET_AUTO_BLOCK_THRESHOLD - 0.01)
        self.assertEqual(decision.status, "pending_review")
        self.assertTrue(decision.quarantine)
        self.assertTrue(decision.requires_review)
        self.assertIn("nudenet_low_confidence", decision.reason)

    def test_very_low_confidence_detection_still_needs_review_not_auto_pass(self):
        # A model failure mode worth guarding against explicitly: ANY
        # detected label, however low-confidence, must still route to a
        # human, never silently auto-pass just because the score is small.
        decision = build_nudenet_decision("EXPOSED_GENITALIA_F", 0.01)
        self.assertEqual(decision.status, "pending_review")

    def test_score_is_always_carried_through_for_audit(self):
        decision = build_nudenet_decision("EXPOSED_GENITALIA_F", 0.42)
        self.assertEqual(decision.score, 0.42)
