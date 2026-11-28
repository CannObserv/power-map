"""Every review-queue reason has a label (#877 phase 3b).

Jinja renders a missing ``REASON_LABELS`` key as an empty string, so a reason
added to ``classification.ReviewReason`` without one would show a blank badge.
"""

from src.api.admin.classification_review import REASON_LABELS
from src.core import classification


def test_every_review_reason_has_a_label():
    assert set(REASON_LABELS) == set(classification.REVIEW_REASONS)
