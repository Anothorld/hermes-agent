"""Tests for the compensation guard.

Validates that:
1. Compensation offers are blocked (true positives from historical scan)
2. Legitimate content is not blocked (true negatives from historical scan)
3. The guard integrates correctly into draft_guard.py
"""

import sys
from pathlib import Path

# Add plugin root to sys.path for top-level imports
_PLUGIN_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PLUGIN_ROOT))

from compensation_guard import check_content, guard_draft


class TestCompensationTruePositives:
    """These drafts contain actual compensation offers and MUST be blocked."""

    def test_goodwill_discount_with_amount(self):
        """P1: 'goodwill discount' — the most common pattern."""
        html = "<p>We'd like to offer you a <strong>$68 CAD goodwill discount</strong> once your delivery is completed.</p>"
        result = check_content(html)
        assert result["blocked"], f"Should block: {result}"
        assert any("goodwill" in m.lower() for m in result["matches"])

    def test_gesture_of_goodwill(self):
        """P2: 'gesture of goodwill' — very specific compensation phrase."""
        html = "<p>As a gesture of goodwill for the delay, we'd like to offer you a $100 discount on your order.</p>"
        result = check_content(html)
        assert result["blocked"], f"Should block: {result}"
        assert any("gesture" in m.lower() for m in result["matches"])

    def test_service_recovery_credit(self):
        """P3: 'service recovery credit'."""
        html = "<p>As a service recovery credit, we are processing a $50 refund to your account.</p>"
        result = check_content(html)
        assert result["blocked"], f"Should block: {result}"

    def test_compensation_offer(self):
        """P4: 'compensation offer'."""
        html = "<p>As compensation, we offer a $120 refund for the damaged drawer.</p>"
        result = check_content(html)
        assert result["blocked"], f"Should block: {result}"

    def test_partial_refund_for_delay(self):
        """P5: 'partial refund for' — damage compensation."""
        html = "<p>We'd like to offer you a $150 partial refund to compensate for the cosmetic damage.</p>"
        result = check_content(html)
        assert result["blocked"], f"Should block: {result}"

    def test_as_a_gesture(self):
        """P6: 'as a gesture of' — compensation context."""
        html = "<p>As a gesture of our appreciation for your patience, we would like to offer you a $97 CAD goodwill discount.</p>"
        result = check_content(html)
        assert result["blocked"], f"Should block: {result}"

    def test_wed_like_to_offer_dollar(self):
        """P7: 'we'd like to offer you a $'."""
        html = "<p>For the delay, we'd like to offer you a $100 discount on this order.</p>"
        result = check_content(html)
        assert result["blocked"], f"Should block: {result}"

    def test_inconvenience_near_dollar(self):
        """P8: 'inconvenience' near $ amount."""
        html = "<p>Sorry for the inconvenience. We are processing a $50 credit to your account.</p>"
        result = check_content(html)
        assert result["blocked"], f"Should block: {result}"

    def test_dollar_cad_discount(self):
        """P9: '$' + 'CAD' + 'discount'."""
        html = "<p>We'd like to offer you a $100 CAD discount on your order.</p>"
        result = check_content(html)
        assert result["blocked"], f"Should block: {result}"

    def test_real_session_skatejackson(self):
        """The actual case that triggered this guard (session 2552472089163530240)."""
        html = """<html><body>
        <p>As a gesture of goodwill for the delay and lack of communication you've experienced,
        we'd like to offer you a <strong>$68 CAD goodwill discount</strong>
        (approximately 3% of your order value) once your delivery is completed.</p>
        </body></html>"""
        result = check_content(html)
        assert result["blocked"], f"Should block: {result}"
        assert len(result["matches"]) >= 2  # Should match multiple patterns

    def test_real_session_dongryan(self):
        """Session 2552541560259985415 — $100 gesture of goodwill."""
        html = """<p>As a gesture of goodwill for the delay, we'd like to offer
        you a  $100 discount  on this order. If you'd like to accept, just let us know.</p>"""
        result = check_content(html)
        assert result["blocked"], f"Should block: {result}"

    def test_real_session_aaronsrz(self):
        """Session 2549980655939403776 — $150 partial refund for cosmetic damage."""
        html = """<p>In addition to the repair kit, we'd like to offer you a
        $150 partial refund  to compensate for the cosmetic damage.</p>"""
        result = check_content(html)
        assert result["blocked"], f"Should block: {result}"


class TestCompensationTrueNegatives:
    """These drafts contain legitimate content and must NOT be blocked."""

    def test_coupon_discount(self):
        """Coupon API output — legitimate discount, not compensation."""
        html = "<p>You can use coupon code JULY12 for 12% off orders over $500. The coupon is valid until July 31, 2026.</p>"
        result = check_content(html)
        assert not result["blocked"], f"Should NOT block coupon: {result}"

    def test_return_policy_refund(self):
        """Return policy quotation — legitimate policy text."""
        html = "<p>Refunds are issued within 5-7 business days to the original payment method. PayPal refunds appear immediately after processing.</p>"
        result = check_content(html)
        assert not result["blocked"], f"Should NOT block policy: {result}"

    def test_order_amount(self):
        """Order amount mention — legitimate price discussion."""
        html = "<p>Your order total is $2,269.07 CAD for the 78.74\" Mid-Century TV Stand (Walnut).</p>"
        result = check_content(html)
        assert not result["blocked"], f"Should NOT block order amount: {result}"

    def test_payment_confirmation(self):
        """Payment confirmation — the false positive case from the scan (P10 removed)."""
        html = "<p>Your order has been received and is currently being processed. Your payment of $2,284.29 was successfully received on July 4, 2026.</p>"
        result = check_content(html)
        assert not result["blocked"], f"Should NOT block payment confirmation: {result}"

    def test_operator_refund_confirmation(self):
        """Operator-initiated refund confirmation — not AI-proposed compensation."""
        html = "<p>Your refund of $575.62 has been processed, and we hope your bank credits it back to your account smoothly.</p>"
        result = check_content(html)
        assert not result["blocked"], f"Should NOT block refund confirmation: {result}"

    def test_white_glove_refund_confirmation(self):
        """White glove service fee refund already agreed — confirmation, not proposal."""
        html = "<p>As previously confirmed, the $120 refund for the white glove service is being processed and should reflect on your original payment method.</p>"
        result = check_content(html)
        assert not result["blocked"], f"Should NOT block WGD refund confirmation: {result}"

    def test_make_it_right_policy(self):
        """Policy quotation 'make it right' — not compensation offer."""
        html = "<p>Contact us within 7 days with photos and we'll make it right at no cost to you.</p>"
        result = check_content(html)
        assert not result["blocked"], f"Should NOT block policy: {result}"

    def test_product_price_and_delivery(self):
        """Normal product inquiry reply with pricing and delivery."""
        html = """<p>The Sailboat 144" U-Shaped Sectional is priced at $3,999.
        Estimated delivery is 1-2 weeks from our New Jersey warehouse.
        You can use coupon code PV3 for $100 off orders over $3,000.</p>"""
        result = check_content(html)
        assert not result["blocked"], f"Should NOT block product info: {result}"

    def test_empty_content(self):
        """Empty content should not block."""
        result = check_content("")
        assert not result["blocked"]

    def test_no_html(self):
        """Plain text (no HTML) should work."""
        text = "Thank you for your order. Your delivery is estimated for July 14-28."
        result = check_content(text)
        assert not result["blocked"]

    def test_thank_you_closure(self):
        """Brief thank-you closure reply — no compensation."""
        html = "<p>Hi Zlata, thank you for your message! Your TV stand is in transit via FedEx, estimated delivery July 14-20. The coffee table will follow separately. Let us know if you have any other questions!</p>"
        result = check_content(html)
        assert not result["blocked"], f"Should NOT block closure: {result}"


class TestGuardDraftWrapper:
    """Test the guard_draft() wrapper that returns error messages."""

    def test_guard_draft_blocked(self):
        html = "<p>As a gesture of goodwill, we offer you a $50 discount on your order.</p>"
        result = guard_draft(html)
        assert result["blocked"]
        assert "Compensation guard" in result["error"]
        assert "open-escalation" in result["error"]
        assert len(result["matches"]) > 0

    def test_guard_draft_allowed(self):
        html = "<p>Your order #12345 is in transit. Estimated delivery: July 14-20.</p>"
        result = guard_draft(html)
        assert not result["blocked"]
        assert result["error"] == ""


class TestDraftGuardIntegration:
    """Test that compensation guard is integrated into draft_guard.guard_draft_content."""

    def test_integration_blocks_compensation(self):
        """The shared guard_draft_content should block compensation."""
        from draft_guard import guard_draft_content

        html = "<p>As a gesture of goodwill for the delay, we'd like to offer you a $100 discount on your order.</p>"
        result = guard_draft_content(html)
        assert result is not None, "Compensation should be blocked by guard_draft_content"
        assert result["blocked"] is True
        assert "compensation" in result.get("blocked_kind", "").lower() or "Compensation" in result.get("error", "")

    def test_integration_allows_legitimate(self):
        """The shared guard_draft_content should allow legitimate content."""
        from draft_guard import guard_draft_content

        html = "<p>Your order is in transit with FedEx. Estimated delivery: July 14-20.</p>"
        result = guard_draft_content(html)
        assert result is None, "Legitimate content should pass"

    def test_integration_allows_coupon(self):
        """Coupon discounts should not be blocked by the integrated guard."""
        from draft_guard import guard_draft_content

        html = "<p>Use coupon code JULY12 for 12% off your order over $500.</p>"
        result = guard_draft_content(html)
        assert result is None, "Coupon content should pass"
