#!/usr/bin/env python3
"""Offline teaching fixture; no provider, network, file writes, or real delivery.

Source-kind labels are supplied by the fixture, not authenticated by this code.
Review is an in-memory record, not a production identity/authorization system.
Run with Python 3.10+ from any directory.
"""

from dataclasses import asdict, dataclass, replace
from datetime import date
import hashlib
import json
import unittest


AS_OF = date(2026, 10, 3)
SURFACES = {"api", "consumer_app", "cli", "local_weights"}
KINDS = {"official", "local_measurement", "radar_unverified"}
AVAILABILITY = {"ga", "preview", "announced", "retired"}


@dataclass(frozen=True)
class Claim:
    claim_id: str
    product: str
    surface: str
    text: str
    source_kind: str
    reference: str
    effective_on: str
    checked_on: str
    valid_until: str
    availability: str


def select_context(claims, surface, as_of=AS_OF):
    """Select official GA/preview candidates on one surface, not adopt a tool.

    valid_until is inclusive. A date window is a teaching policy, not proof that
    an official specification cannot change during that window.
    Identical duplicates collapse; conflicting IDs fail the whole selection.
    """
    if surface not in SURFACES:
        raise ValueError("unknown target surface")
    by_id = {}
    for item in claims:
        if item.claim_id in by_id and by_id[item.claim_id] != item:
            raise ValueError("conflicting claim ID: " + item.claim_id)
        by_id[item.claim_id] = item
    selected, held = [], []
    for item in sorted(by_id.values(), key=lambda row: row.claim_id):
        reasons = []
        if not item.claim_id or not item.product or not item.text:
            reasons.append("missing_identity_or_text")
        if item.surface not in SURFACES or item.surface != surface:
            reasons.append("surface_mismatch")
        if item.source_kind not in KINDS or item.source_kind != "official":
            reasons.append("not_official_for_this_question")
        if not item.reference.strip():
            reasons.append("missing_reference")
        if item.availability not in AVAILABILITY or item.availability not in {"ga", "preview"}:
            reasons.append("not_available_candidate")
        try:
            effective = date.fromisoformat(item.effective_on)
            checked = date.fromisoformat(item.checked_on)
            valid = date.fromisoformat(item.valid_until)
            if effective > checked or checked > valid:
                reasons.append("invalid_date_order")
            if effective > as_of or checked > as_of:
                reasons.append("future_record")
            if valid < as_of:
                reasons.append("expired_review_window")
        except (ValueError, TypeError):
            reasons.append("invalid_date")
        if reasons:
            held.append({"claim_id": item.claim_id, "reasons": reasons})
        else:
            selected.append(item)
    return selected, held


@dataclass(frozen=True)
class Packet:
    packet_id: str
    producer: str
    candidate_owner: str
    claim_ids: tuple[str, ...]
    next_test: str
    boundary: str = "proposal_only_no_adoption_or_publication"


def digest(packet):
    encoded = json.dumps(asdict(packet), sort_keys=True, ensure_ascii=False,
                         separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class Review:
    reviewer: str
    verdict: str
    packet_hash: str


class DemoSink:
    """An in-memory receipt ledger; it sends nothing and is lost on restart."""

    def __init__(self):
        self.receipts = {}

    def deliver(self, packet, review):
        if not packet.packet_id or not packet.producer or not packet.candidate_owner:
            raise ValueError("missing packet identity or destination")
        if not packet.claim_ids or not packet.next_test.strip():
            raise ValueError("missing claims or next test")
        if packet.boundary != "proposal_only_no_adoption_or_publication":
            raise ValueError("scope expansion is not allowed")
        if review is None or not review.reviewer or review.reviewer == packet.producer:
            raise ValueError("separate reviewer required")
        if review.verdict != "PASS_SOURCE_CHECK":
            raise ValueError("source review has not passed")
        packet_hash = digest(packet)
        if packet_hash != review.packet_hash:
            raise ValueError("review applies to a different packet revision")
        key = packet.packet_id
        if key in self.receipts:
            if self.receipts[key]["packet_hash"] != packet_hash:
                raise ValueError("packet ID reused for different content")
            return self.receipts[key]
        receipt = {"packet_id": key, "packet_hash": packet_hash,
                   "candidate_owner": packet.candidate_owner,
                   "state": "DEMO_DELIVERED_NOT_ADOPTED"}
        self.receipts[key] = receipt
        return receipt


def fixtures():
    # Entirely fictional product and sources. No claim about a real provider.
    base = Claim("official-api", "ExampleAI", "api", "API preview is available",
                 "official", "fixture:vendor-release-1", "2026-10-01",
                 "2026-10-03", "2026-10-10", "preview")
    return [base,
            replace(base, claim_id="x-rumor", source_kind="radar_unverified",
                    reference="fixture:social-post"),
            replace(base, claim_id="consumer-app", surface="consumer_app"),
            replace(base, claim_id="expired", effective_on="2026-09-01",
                    checked_on="2026-09-20", valid_until="2026-09-30"),
            replace(base, claim_id="no-source", reference=""),
            replace(base, claim_id="announcement-only", availability="announced")]


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.base = fixtures()[0]
        self.packet = Packet("demo-001", "demo-producer", "demo-domain-owner",
                             ("official-api",), "Compare on synthetic inputs")
        self.review = Review("demo-reviewer", "PASS_SOURCE_CHECK", digest(self.packet))

    def test_one_official_candidate(self):
        selected, held = select_context(fixtures(), "api")
        self.assertEqual([row.claim_id for row in selected], ["official-api"])
        self.assertEqual(len(held), 5)

    def test_radar_remains_out_of_fact_context(self):
        selected, held = select_context([fixtures()[1]], "api")
        self.assertFalse(selected)
        self.assertIn("not_official_for_this_question", held[0]["reasons"])

    def test_surface_mismatch(self):
        self.assertFalse(select_context([fixtures()[2]], "api")[0])

    def test_expired_window(self):
        self.assertFalse(select_context([fixtures()[3]], "api")[0])

    def test_missing_reference(self):
        self.assertFalse(select_context([fixtures()[4]], "api")[0])

    def test_announced_is_not_available(self):
        self.assertFalse(select_context([fixtures()[5]], "api")[0])

    def test_future_and_invalid_date(self):
        for value in ("2026-10-04", "not-a-date"):
            self.assertFalse(select_context([replace(self.base, checked_on=value)], "api")[0])

    def test_invalid_date_order(self):
        self.assertFalse(select_context([replace(self.base, effective_on="2026-10-09")], "api")[0])

    def test_identical_duplicates_collapse(self):
        self.assertEqual(len(select_context([self.base, self.base], "api")[0]), 1)

    def test_conflicting_id_is_fatal(self):
        with self.assertRaises(ValueError):
            select_context([self.base, replace(self.base, text="Different assertion")], "api")

    def test_unknown_surface_is_fatal(self):
        with self.assertRaises(ValueError):
            select_context([self.base], "unknown")

    def test_review_required(self):
        with self.assertRaises(ValueError):
            DemoSink().deliver(self.packet, None)

    def test_self_review_rejected(self):
        with self.assertRaises(ValueError):
            DemoSink().deliver(self.packet, replace(self.review, reviewer=self.packet.producer))

    def test_changed_content_invalidates_review(self):
        with self.assertRaises(ValueError):
            DemoSink().deliver(replace(self.packet, next_test="A different test"), self.review)

    def test_failed_review_rejected(self):
        with self.assertRaises(ValueError):
            DemoSink().deliver(self.packet, replace(self.review, verdict="FAIL"))

    def test_delivery_is_not_adoption(self):
        receipt = DemoSink().deliver(self.packet, self.review)
        self.assertEqual(receipt["state"], "DEMO_DELIVERED_NOT_ADOPTED")

    def test_replay_deduplicates_in_memory(self):
        sink = DemoSink()
        first = sink.deliver(self.packet, self.review)
        self.assertEqual(first, sink.deliver(self.packet, self.review))
        self.assertEqual(len(sink.receipts), 1)

    def test_same_id_different_payload_rejected(self):
        sink = DemoSink()
        sink.deliver(self.packet, self.review)
        changed = replace(self.packet, candidate_owner="different-owner")
        with self.assertRaises(ValueError):
            sink.deliver(changed, replace(self.review, packet_hash=digest(changed)))

    def test_scope_expansion_rejected(self):
        changed = replace(self.packet, boundary="publish_now")
        with self.assertRaises(ValueError):
            DemoSink().deliver(changed, replace(self.review, packet_hash=digest(changed)))


if __name__ == "__main__":
    selected, held = select_context(fixtures(), "api")
    print(json.dumps({"as_of": AS_OF.isoformat(), "selected_claim_ids":
                      [item.claim_id for item in selected], "held": held},
                     ensure_ascii=False, indent=2))
    unittest.main(verbosity=2)
