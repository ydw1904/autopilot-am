"""Delivery queue: pending ids, the put_up refusal, and the wait/claim loop.

Offline — no session, no network. What is worth pinning is the wait loop:
it has to work whether the server drops finished events by itself or holds
them until `event/validateended` is called, and it must not spin on a stuck
entry. See ticket 014.
"""

import pytest

import mobile_api
from mobile_api import AMClient, AMError, AMNotDelivered


def _event(event_id, aircraft_id, finish_at, kind="aircraft"):
    return {"id": event_id, "type": kind, "objectid": aircraft_id,
            "label": "DHC-6 Series 100", "amcoinsactionid": 101, "amount": 7,
            "createdAt": {"date": "2026-08-26 02:25:01.000000"},
            "finishAt": {"date": finish_at}, "isRental": False}


class FakeClient(AMClient):
    """AMClient with the transport replaced by a scripted event queue."""

    def __init__(self, frames, claim_clears=True):
        # Deliberately skips AMClient.__init__ — no session, no httpx client.
        self.frames = list(frames)          # [(now, [event, ...]), ...]
        self.claim_clears = claim_clears
        self.claims = 0
        self.put_ups = []
        self.put_up_error = None

    def _events(self):
        frame = self.frames[0] if len(self.frames) == 1 else self.frames.pop(0)
        return frame

    def deliver_finished(self):
        self.claims += 1
        if self.claim_clears:
            now, events = self.frames[0]
            self.frames = [(now, [e for e in events
                                  if e["finishAt"]["date"] > now])]
        return {}

    def _request(self, method, endpoint, params=None, data=None, allow_empty=False):
        assert endpoint == "auction/aircraft/put_up"
        self.put_ups.append(data)
        if self.put_up_error:
            raise self.put_up_error
        return {"auction": {"id": 999}}


NOW = "2026-08-26 02:30:00.000000"
LATER = "2026-08-26 02:55:01.000000"
EARLIER = "2026-08-26 02:20:00.000000"


def test_pending_aircraft_ids_ignores_other_event_types():
    c = FakeClient([(NOW, [_event(1, 111, LATER),
                           _event(2, 222, LATER, kind="iataTraining"),
                           _event(3, 333, LATER)])])
    assert c.pending_aircraft_ids() == {111, 333}


def test_is_delivered_is_true_once_the_id_leaves_the_queue():
    c = FakeClient([(NOW, [_event(1, 111, LATER)])])
    assert not c.is_delivered(111)
    assert c.is_delivered(222)


def test_put_up_names_the_empty_refusal_when_still_in_delivery():
    c = FakeClient([(NOW, [_event(1, 111, LATER)])])
    c.put_up_error = AMError("auction/aircraft/put_up: status=0 message=0")
    with pytest.raises(AMNotDelivered):
        c.put_up(111, 1, 2)


def test_put_up_leaves_other_refusals_alone():
    """A delivered plane refused for some other reason must not be relabelled."""
    c = FakeClient([(NOW, [])])
    c.put_up_error = AMError("auction/aircraft/put_up: status=0 message=0")
    with pytest.raises(AMError) as e:
        c.put_up(111, 1, 2)
    assert not isinstance(e.value, AMNotDelivered)


def test_wait_returns_immediately_when_nothing_is_pending():
    c = FakeClient([(NOW, [])])
    assert c.wait_for_delivery([111]) == []
    assert c.claims == 0


def test_wait_returns_when_the_event_disappears(monkeypatch):
    """However the entry leaves the queue (here: an ad speed-up), the wait ends."""
    monkeypatch.setattr(mobile_api.time, "sleep", lambda s: None)
    c = FakeClient([(NOW, [_event(1, 111, LATER)]),
                    (LATER, [])])
    assert c.wait_for_delivery([111], poll=0) == []
    assert c.claims == 0


def test_wait_claims_a_finished_event(monkeypatch):
    """The real server behaviour: past finishAt the entry sits there until
    claimed (verified 2026-08-26, 5 min past finishAt). The claim must fire."""
    monkeypatch.setattr(mobile_api.time, "sleep", lambda s: None)
    c = FakeClient([(LATER, [_event(1, 111, EARLIER)])])
    assert c.wait_for_delivery([111], poll=0) == []
    assert c.claims == 1


def test_wait_does_not_spin_on_a_stuck_entry(monkeypatch):
    """A claim that changes nothing must be tried once, not once per poll."""
    monkeypatch.setattr(mobile_api.time, "sleep", lambda s: None)
    times = iter([0, 1, 2, 3, 4, 5, 6, 7, 999])
    monkeypatch.setattr(mobile_api.time, "time", lambda: next(times))
    c = FakeClient([(LATER, [_event(1, 111, EARLIER)])], claim_clears=False)
    assert c.wait_for_delivery([111], timeout=10, poll=0) == [111]
    assert c.claims == 1
