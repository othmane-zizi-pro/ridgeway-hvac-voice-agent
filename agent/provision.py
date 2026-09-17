"""Create (or update) the Ridgeway dispatcher on Vapi from this folder.

Everything the agent is made of lives here: two API-request tools that hit
the mock with the Ridgeway key, the assistant (prompt in system_prompt.md),
and the inbound phone number. Run it once to create, run it again to push
prompt or tool changes; ids are remembered in agent/state.json.

    export VAPI_API_KEY=...           # Vapi dashboard, Org settings, API keys
    export MOCK_BASE_URL=https://...   # printed by deploy/deploy.sh
    python3 agent/provision.py

Only the standard library is used, so it runs anywhere python3 does.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
STATE = HERE / "state.json"
API = "https://api.vapi.ai"

RIDGEWAY_KEY = "ridgeway_hvac_fde"
MOCK = os.environ.get("MOCK_BASE_URL", "").rstrip("/")
TOKEN = os.environ.get("VAPI_API_KEY", "")
if not MOCK or not TOKEN:
    sys.exit("set VAPI_API_KEY and MOCK_BASE_URL first")


def vapi(method: str, path: str, body: dict | None = None, fatal: bool = True) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"{API}{path}",
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Content-Type": "application/json",
            # Vapi sits behind Cloudflare, which rejects urllib's default user agent.
            "User-Agent": "ridgeway-provision/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        detail = f"{method} {path} -> {e.code}: {e.read().decode()}"
        if not fatal:
            raise RuntimeError(detail) from None
        sys.exit(detail)


def fixed_header(value: str) -> dict:
    # A one-value enum plus a value: the request always carries exactly this.
    return {"type": "string", "value": value, "enum": [value]}


LOOKUP_TOOL = {
    "type": "apiRequest",
    "name": "lookup_avail_area_codes_serviced",
    "description": (
        "Check whether Ridgeway HVAC services a caller's area code and get the open repair windows. "
        "Call this before offering any appointment. Pass exactly three digits, for example 503, "
        "never a full phone number and never a leading 1 or plus sign."
    ),
    "method": "POST",
    "url": f"{MOCK}/lookup_avail_area_codes_serviced",
    "timeoutSeconds": 15,
    "headers": {
        "type": "object",
        "properties": {
            "X-Ridgeway-Key": fixed_header(RIDGEWAY_KEY),
            "Content-Type": fixed_header("application/json"),
        },
    },
    "body": {
        "type": "object",
        "properties": {
            "areaCode": {
                "type": "string",
                "description": "The caller's three-digit area code, digits only, for example 503.",
                "pattern": "^[2-9][0-9]{2}$",
            }
        },
        "required": ["areaCode"],
    },
    "messages": [
        {"type": "request-start", "content": "One moment, let me check your area."},
    ],
}

BOOK_TOOL = {
    "type": "apiRequest",
    "name": "book_appointment",
    "description": (
        "Book the AC repair visit. Only call this after lookup_avail_area_codes_serviced returned "
        "serviced true, and only with a slotId that lookup returned. The response contains "
        "appointment.confirmation, which is the confirmation number to read back to the caller."
    ),
    "method": "POST",
    "url": f"{MOCK}/book_appointment",
    "timeoutSeconds": 15,
    "headers": {
        "type": "object",
        "properties": {
            "X-Ridgeway-Key": fixed_header(RIDGEWAY_KEY),
            "Content-Type": fixed_header("application/json"),
        },
    },
    "body": {
        "type": "object",
        "properties": {
            "areaCode": {
                "type": "string",
                "description": "The caller's three-digit area code, digits only, the same one that was looked up.",
                "pattern": "^[2-9][0-9]{2}$",
            },
            "slotId": {
                "type": "string",
                "description": "The id of the window the caller chose, exactly as returned by the lookup (for example SLOT-TUE-AM).",
            },
            "name": {"type": "string", "description": "The caller's full name as they gave it."},
            "phone": {
                "type": "string",
                "description": "The caller's 10-digit phone number, digits only, no country code, for example 5035550142. Take it from caller ID when available.",
                "pattern": "^[0-9]{10}$",
            },
            "symptom": {
                "type": "string",
                "description": "The AC problem in the caller's words, in a short phrase, for example 'AC blowing warm air'.",
            },
        },
        "required": ["areaCode", "slotId", "name", "phone", "symptom"],
    },
    "messages": [
        {"type": "request-start", "content": "Booking that for you now."},
    ],
}


def assistant_body(tool_ids: list[str]) -> dict:
    prompt = (HERE / "system_prompt.md").read_text()
    return {
        "name": "Ridgeway HVAC dispatcher",
        "firstMessage": "Ridgeway HVAC after-hours line, this is the dispatcher. What's going on with your AC?",
        "firstMessageMode": "assistant-speaks-first",
        "firstMessageInterruptionsEnabled": True,
        "model": {
            "provider": "openai",
            "model": "gpt-4.1",
            "temperature": 0.3,
            "maxTokens": 200,
            "messages": [{"role": "system", "content": prompt}],
            "toolIds": tool_ids,
            "tools": [{"type": "endCall"}],
        },
        "voice": {"provider": "vapi", "voiceId": "Elliot"},
        "transcriber": {
            "provider": "deepgram",
            "model": "nova-3",
            "language": "en",
            # Words the phone line garbles: "AC" came through as "HC", "Ridgeway" as "Bridgeway".
            "keyterm": ["Ridgeway", "AC", "HVAC", "air conditioning", "area code", "morning", "afternoon"],
        },
        "endCallMessage": "Thanks for calling Ridgeway. Goodbye.",
        # Belt and braces: if the model says goodbye without invoking endCall, the line still drops.
        "endCallPhrases": ["goodbye", "good bye", "have a good evening", "have a good night"],
        "backgroundSound": "off",
        "maxDurationSeconds": 600,
        # Barge-in: the caller can cut the agent off with a couple of words.
        "stopSpeakingPlan": {"numWords": 2, "voiceSeconds": 0.2, "backoffSeconds": 0.8},
        "startSpeakingPlan": {"waitSeconds": 0.5, "smartEndpointingPlan": {"provider": "livekit"}},
        "analysisPlan": {
            "summaryPlan": {"enabled": True},
            "successEvaluationPlan": {
                "enabled": True,
                "rubric": "PassFail",
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "Pass only if the assistant called lookup_avail_area_codes_serviced before "
                            "offering windows, and either booked through book_appointment and read back the "
                            "returned confirmation number, or correctly refused an unserviced area code "
                            "without booking. Fail if a confirmation number was said that no tool returned."
                        ),
                    },
                    {"role": "user", "content": "Transcript:\n\n{{transcript}}\n\nTool calls and results are in the transcript."},
                ],
            },
        },
    }


def main() -> None:
    state = json.loads(STATE.read_text()) if STATE.exists() else {}

    tool_ids = []
    for key, spec in (("lookupToolId", LOOKUP_TOOL), ("bookToolId", BOOK_TOOL)):
        if state.get(key):
            body = {k: v for k, v in spec.items() if k != "type"}
            vapi("PATCH", f"/tool/{state[key]}", body)
            print(f"updated tool {spec['name']} {state[key]}")
        else:
            state[key] = vapi("POST", "/tool", spec)["id"]
            print(f"created tool {spec['name']} {state[key]}")
        tool_ids.append(state[key])
        STATE.write_text(json.dumps(state, indent=2) + "\n")

    body = assistant_body(tool_ids)
    if state.get("assistantId"):
        vapi("PATCH", f"/assistant/{state['assistantId']}", body)
        print(f"updated assistant {state['assistantId']}")
    else:
        state["assistantId"] = vapi("POST", "/assistant", body)["id"]
        print(f"created assistant {state['assistantId']}")
    STATE.write_text(json.dumps(state, indent=2) + "\n")

    if not state.get("phoneNumberId"):
        # A free Vapi number. Portland's 503 first, then anything US.
        number = None
        for area in ("503", "971", "360", "425", "206", "415", "212"):
            try:
                number = vapi(
                    "POST",
                    "/phone-number",
                    {
                        "provider": "vapi",
                        "numberDesiredAreaCode": area,
                        "name": "Ridgeway HVAC after-hours",
                        "assistantId": state["assistantId"],
                    },
                    fatal=False,
                )
                break
            except RuntimeError as e:
                print(f"area code {area}: {e}")
        if number is None:
            sys.exit("could not get a free number in any tried area code")
        state["phoneNumberId"] = number["id"]
        state["phoneNumber"] = number.get("number")
        print(f"created phone number {number.get('number')} {number['id']}")
    else:
        vapi("PATCH", f"/phone-number/{state['phoneNumberId']}", {"assistantId": state["assistantId"]})
        print(f"phone number {state.get('phoneNumber')} points at the assistant")

    STATE.write_text(json.dumps(state, indent=2) + "\n")
    print(f"\nDial: {state.get('phoneNumber')}")


if __name__ == "__main__":
    main()
