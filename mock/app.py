"""Mock of Ridgeway HVAC's two booking endpoints.

The surface is exactly the one in the customer packet: a lookup that says
whether an area code is serviced and which tech windows are open, and a
booking write that returns a confirmation number. Nothing else. Every
request carries `X-Ridgeway-Key`; a wrong or missing key is a 401.

Status codes follow the packet:
    200  lookup, serviced or not
    201  booking created
    400  malformed body (area code not three digits, phone not ten digits)
    401  bad key
    409  booking an unserviced area code or an unknown slot id
"""

from __future__ import annotations

import json
import logging
import re
import secrets
import time
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel

log = logging.getLogger("ridgeway")
log.setLevel(logging.INFO)

RIDGEWAY_KEY = "ridgeway_hvac_fde"

# The three seeded area codes from the packet. Anything else is a real
# "not in service area" answer, same shape as 206.
SERVICE_AREAS: dict[str, dict[str, Any]] = {
    "503": {
        "region": "Portland metro",
        "slots": [
            {"id": "SLOT-TUE-AM", "label": "Tue 8:00am–12:00pm"},
            {"id": "SLOT-TUE-PM", "label": "Tue 1:00pm–5:00pm"},
        ],
    },
    "360": {
        "region": "Vancouver WA",
        "slots": [
            {"id": "SLOT-WED-AM", "label": "Wed 8:00am–12:00pm"},
            {"id": "SLOT-WED-PM", "label": "Wed 1:00pm–5:00pm"},
        ],
    },
    "206": {
        # Seattle: known to Ridgeway, explicitly not serviced.
        "region": None,
        "slots": [],
    },
}

AREA_CODE = re.compile(r"^[2-9]\d{2}$")
PHONE_10 = re.compile(r"^\d{10}$")

# Bookings live in process memory. The mock has no read endpoint by
# design (the packet says two endpoints), so this is only for uniqueness
# of confirmation numbers and for the structured log line per booking.
BOOKINGS: dict[str, dict[str, Any]] = {}

app = FastAPI(title="Ridgeway HVAC mock", docs_url=None, redoc_url=None, openapi_url=None)


class LookupRequest(BaseModel):
    areaCode: str | int


class BookRequest(BaseModel):
    areaCode: str | int
    slotId: str
    name: str
    phone: str | int
    symptom: str


def error(code: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=code, content={"ok": False, "error": message})


def normalise_area_code(raw: str | int) -> str | None:
    """Return the three-digit area code, or None when it is not one.

    `503` and `"503"` are fine. `"+1503"`, `"1503"`, `"5035550142"` and
    `"50"` are not: the packet is explicit that a full phone number is a 400.
    """
    value = str(raw).strip()
    return value if AREA_CODE.match(value) else None


def normalise_phone(raw: str | int) -> str | None:
    """Return the ten-digit NANP number, or None.

    Formatting characters (spaces, dashes, dots, parentheses) are the same
    number and are stripped. A country code is not: `+15035550142` and
    `15035550142` are rejected, as the packet says no country code.
    """
    value = str(raw).strip()
    if value.startswith("+"):
        return None
    digits = re.sub(r"[\s().\-]", "", value)
    return digits if PHONE_10.match(digits) else None


@app.exception_handler(RequestValidationError)
async def on_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    first = exc.errors()[0] if exc.errors() else {}
    loc = ".".join(str(p) for p in first.get("loc", []) if p != "body")
    return error(400, f"invalid request body: {loc or 'body'} {first.get('msg', 'is invalid')}")


@app.middleware("http")
async def require_key_and_log(request: Request, call_next):
    started = time.perf_counter()
    if request.headers.get("x-ridgeway-key") != RIDGEWAY_KEY:
        response = error(401, "unauthorized: missing or unknown X-Ridgeway-Key")
    else:
        response = await call_next(request)
    log.info(
        json.dumps(
            {
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "ms": round((time.perf_counter() - started) * 1000, 1),
            }
        )
    )
    return response


@app.post("/lookup_avail_area_codes_serviced")
async def lookup_avail_area_codes_serviced(body: LookupRequest) -> JSONResponse:
    area_code = normalise_area_code(body.areaCode)
    if area_code is None:
        return error(400, "areaCode must be exactly three digits, for example 503, with no leading 1 or plus sign")
    area = SERVICE_AREAS.get(area_code)
    if area is None or not area["slots"]:
        return JSONResponse(
            status_code=200,
            content={"ok": True, "serviced": False, "areaCode": area_code, "region": None, "slots": []},
        )
    return JSONResponse(
        status_code=200,
        content={
            "ok": True,
            "serviced": True,
            "areaCode": area_code,
            "region": area["region"],
            "slots": area["slots"],
        },
    )


def new_confirmation() -> str:
    while True:
        candidate = f"AC-{secrets.randbelow(90000) + 10000}"
        if candidate not in BOOKINGS:
            return candidate


@app.post("/book_appointment", status_code=status.HTTP_201_CREATED)
async def book_appointment(body: BookRequest) -> JSONResponse:
    area_code = normalise_area_code(body.areaCode)
    if area_code is None:
        return error(400, "areaCode must be exactly three digits, for example 503, with no leading 1 or plus sign")
    phone = normalise_phone(body.phone)
    if phone is None:
        return error(400, "phone must be a 10-digit NANP number with no country code, for example 5035550142")
    name = body.name.strip()
    symptom = body.symptom.strip()
    if not name:
        return error(400, "name must not be empty")
    if not symptom:
        return error(400, "symptom must not be empty")

    area = SERVICE_AREAS.get(area_code)
    if area is None or not area["slots"]:
        return error(409, f"area code {area_code} is not in Ridgeway's service area; nothing was booked")
    slot = next((s for s in area["slots"] if s["id"] == body.slotId), None)
    if slot is None:
        known = ", ".join(s["id"] for s in area["slots"])
        return error(409, f"unknown slotId {body.slotId!r} for area code {area_code}; known slots: {known}")

    confirmation = new_confirmation()
    appointment = {
        "confirmation": confirmation,
        "areaCode": area_code,
        "slotId": slot["id"],
        "label": slot["label"],
    }
    BOOKINGS[confirmation] = {**appointment, "name": name, "phone": phone, "symptom": symptom, "at": time.time()}
    log.info(json.dumps({"event": "booked", **BOOKINGS[confirmation]}, ensure_ascii=False))
    return JSONResponse(status_code=201, content={"ok": True, "appointment": appointment})
