"""Every row of the packet's rubric table, plus the error codes it lists."""

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "mock"))

from app import BOOKINGS, app  # noqa: E402

KEY = {"X-Ridgeway-Key": "ridgeway_hvac_fde"}
LOOKUP = "/lookup_avail_area_codes_serviced"
BOOK = "/book_appointment"


@pytest.fixture
def client():
    BOOKINGS.clear()
    return TestClient(app)


def good_booking(**overrides):
    body = {
        "areaCode": "503",
        "slotId": "SLOT-TUE-AM",
        "name": "Marcus Hale",
        "phone": "5035550142",
        "symptom": "AC blowing warm air",
    }
    body.update(overrides)
    return body


# Auth


@pytest.mark.parametrize("path", [LOOKUP, BOOK])
def test_missing_key_is_401(client, path):
    r = client.post(path, json={"areaCode": "503"})
    assert r.status_code == 401
    assert r.json()["ok"] is False


@pytest.mark.parametrize("path", [LOOKUP, BOOK])
def test_unknown_key_is_401(client, path):
    r = client.post(path, json={"areaCode": "503"}, headers={"X-Ridgeway-Key": "nope"})
    assert r.status_code == 401


def test_key_header_is_case_insensitive(client):
    r = client.post(LOOKUP, json={"areaCode": "503"}, headers={"x-ridgeway-key": "ridgeway_hvac_fde"})
    assert r.status_code == 200


# Lookup


def test_lookup_503_is_portland_with_two_labeled_slots(client):
    r = client.post(LOOKUP, json={"areaCode": "503"}, headers=KEY)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["serviced"] is True
    assert body["areaCode"] == "503"
    assert body["region"] == "Portland metro"
    assert len(body["slots"]) >= 2
    assert body["slots"][0] == {"id": "SLOT-TUE-AM", "label": "Tue 8:00am–12:00pm"}
    assert body["slots"][1] == {"id": "SLOT-TUE-PM", "label": "Tue 1:00pm–5:00pm"}
    for slot in body["slots"]:
        assert slot["id"] and slot["label"]


def test_lookup_360_is_vancouver_with_two_labeled_slots(client):
    body = client.post(LOOKUP, json={"areaCode": "360"}, headers=KEY).json()
    assert body["serviced"] is True
    assert body["region"] == "Vancouver WA"
    assert len(body["slots"]) >= 2
    assert all(s["id"] and s["label"] for s in body["slots"])


def test_lookup_206_is_not_serviced_with_no_slots(client):
    r = client.post(LOOKUP, json={"areaCode": "206"}, headers=KEY)
    assert r.status_code == 200
    assert r.json() == {"ok": True, "serviced": False, "areaCode": "206", "region": None, "slots": []}


def test_lookup_unknown_area_code_is_not_serviced(client):
    body = client.post(LOOKUP, json={"areaCode": "415"}, headers=KEY).json()
    assert body["serviced"] is False
    assert body["slots"] == []


def test_lookup_accepts_numeric_area_code(client):
    body = client.post(LOOKUP, json={"areaCode": 503}, headers=KEY).json()
    assert body["serviced"] is True
    assert body["areaCode"] == "503"


@pytest.mark.parametrize("bad", ["5035550142", "+1503", "1503", "50", "", "abc", " 5035550142 "])
def test_lookup_rejects_non_three_digit_area_code(client, bad):
    r = client.post(LOOKUP, json={"areaCode": bad}, headers=KEY)
    assert r.status_code == 400, bad
    assert r.json()["ok"] is False


def test_lookup_rejects_missing_body(client):
    r = client.post(LOOKUP, headers=KEY)
    assert r.status_code == 400


def test_lookup_rejects_wrong_field(client):
    r = client.post(LOOKUP, json={"area_code": "503"}, headers=KEY)
    assert r.status_code == 400


def test_lookup_is_post_only(client):
    assert client.get(LOOKUP, headers=KEY).status_code == 405


# Book


def test_book_503_returns_201_with_ac_confirmation(client):
    r = client.post(BOOK, json=good_booking(), headers=KEY)
    assert r.status_code == 201
    body = r.json()
    assert body["ok"] is True
    appt = body["appointment"]
    assert appt["confirmation"].startswith("AC-")
    assert appt["confirmation"][3:].isdigit() and len(appt["confirmation"]) == 8
    assert appt["areaCode"] == "503"
    assert appt["slotId"] == "SLOT-TUE-AM"
    assert appt["label"] == "Tue 8:00am–12:00pm"
    assert set(appt) == {"confirmation", "areaCode", "slotId", "label"}


def test_confirmations_are_unique(client):
    seen = {client.post(BOOK, json=good_booking(), headers=KEY).json()["appointment"]["confirmation"] for _ in range(50)}
    assert len(seen) == 50


def test_book_360(client):
    r = client.post(BOOK, json=good_booking(areaCode="360", slotId="SLOT-WED-PM", phone="3605550100"), headers=KEY)
    assert r.status_code == 201
    assert r.json()["appointment"]["label"] == "Wed 1:00pm–5:00pm"


def test_book_206_is_409_and_books_nothing(client):
    r = client.post(BOOK, json=good_booking(areaCode="206", phone="2065550142"), headers=KEY)
    assert r.status_code == 409
    assert r.json()["ok"] is False
    assert BOOKINGS == {}


def test_book_unknown_area_code_is_409(client):
    r = client.post(BOOK, json=good_booking(areaCode="415"), headers=KEY)
    assert r.status_code == 409


def test_book_unknown_slot_is_409(client):
    r = client.post(BOOK, json=good_booking(slotId="SLOT-FRI-AM"), headers=KEY)
    assert r.status_code == 409
    assert BOOKINGS == {}


def test_book_slot_from_other_area_is_409(client):
    r = client.post(BOOK, json=good_booking(slotId="SLOT-WED-AM"), headers=KEY)
    assert r.status_code == 409


@pytest.mark.parametrize("bad", ["5035550142", "+1503", "50"])
def test_book_rejects_bad_area_code(client, bad):
    assert client.post(BOOK, json=good_booking(areaCode=bad), headers=KEY).status_code == 400


@pytest.mark.parametrize("bad", ["+15035550142", "15035550142", "555-0142", "503555014", ""])
def test_book_rejects_non_ten_digit_phone(client, bad):
    r = client.post(BOOK, json=good_booking(phone=bad), headers=KEY)
    assert r.status_code == 400, bad


@pytest.mark.parametrize("formatted", ["(503) 555-0142", "503-555-0142", "503.555.0142", "503 555 0142"])
def test_book_accepts_formatted_ten_digit_phone(client, formatted):
    r = client.post(BOOK, json=good_booking(phone=formatted), headers=KEY)
    assert r.status_code == 201, formatted
    assert list(BOOKINGS.values())[0]["phone"] == "5035550142"


@pytest.mark.parametrize("field", ["slotId", "name", "phone", "symptom", "areaCode"])
def test_book_rejects_missing_field(client, field):
    body = good_booking()
    del body[field]
    r = client.post(BOOK, json=body, headers=KEY)
    assert r.status_code == 400, field


def test_book_rejects_blank_name(client):
    assert client.post(BOOK, json=good_booking(name="  "), headers=KEY).status_code == 400


def test_error_bodies_are_json_with_ok_false(client):
    for r in (
        client.post(LOOKUP, json={"areaCode": "x"}, headers=KEY),
        client.post(BOOK, json=good_booking(areaCode="206"), headers=KEY),
        client.post(LOOKUP, json={"areaCode": "503"}),
    ):
        assert r.headers["content-type"].startswith("application/json")
        assert r.json()["ok"] is False
        assert isinstance(r.json()["error"], str)
