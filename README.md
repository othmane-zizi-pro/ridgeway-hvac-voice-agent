# Ridgeway HVAC after-hours booking agent

A homeowner whose air
conditioning has died calls Ridgeway HVAC after hours, a voice agent checks
whether Ridgeway covers their area code, offers a repair window and books it
against Ridgeway's API. The API is mocked here because production sits on the
customer's VPN.

**Dial: +1 (971) 338-7356**

| Piece | Where | Notes |
|---|---|---|
| Voice agent | Vapi, assistant "Ridgeway HVAC dispatcher" | GPT-4.1, Deepgram nova-3, inbound on the number above, barge-in on |
| Mock API | `https://pkzrruq3lwmi7iqsf5rlt5dhhu0lauui.lambda-url.us-east-1.on.aws` | FastAPI on AWS Lambda behind a public Function URL |
| Agent definition | `agent/` | Prompt, both tools, assistant settings, provisioning script |
| Mock source and tests | `mock/`, `tests/` | 46 unit tests plus a live smoke script |

## The call

The agent answers as the Ridgeway dispatcher. It takes the caller's area code
from caller ID (the three digits after +1) or asks for it when there is no
caller ID, calls `lookup_avail_area_codes_serviced`, and:

- if the area is serviced, confirms the region ("we cover Portland metro") and
  offers the windows by their human labels ("Tuesday morning, eight to noon"),
  never by slot id;
- if it is not (206, Seattle), says so, does not offer windows, and never calls
  `book_appointment`.

Once the caller picks a window and gives a name, the agent calls
`book_appointment` with the area code, the slot id behind the chosen label, the
name, the 10-digit phone from caller ID, and the symptom in the caller's words.
It reads back `appointment.confirmation` from the response, character by
character, repeats the window, and hangs up. The prompt forbids inventing a
confirmation number: if the booking call fails, the agent says so and offers
to retry.

Both tools are Vapi "API request" tools that POST to the mock's exact paths
with `X-Ridgeway-Key: ridgeway_hvac_fde` as a fixed header and a JSON-schema
body the model fills. There is no adapter in between, so what the mock
receives is what the agent decided.

## The mock

Two endpoints, nothing else. `POST /lookup_avail_area_codes_serviced` and
`POST /book_appointment`, request and response bodies exactly as in the
packet.

| Case | Status |
|---|---|
| Missing or unknown `X-Ridgeway-Key` | 401 |
| `areaCode` not exactly three digits (`+1503`, `5035550142`) | 400 |
| `phone` not ten digits, or carrying a country code | 400 |
| Lookup 503 | 200, Portland metro, `SLOT-TUE-AM` and `SLOT-TUE-PM` |
| Lookup 360 | 200, Vancouver WA, `SLOT-WED-AM` and `SLOT-WED-PM` |
| Lookup 206 | 200, `serviced: false`, `region: null`, `slots: []` |
| Lookup any other area code | 200, `serviced: false` |
| Book a serviced area with a known slot | 201, `appointment.confirmation` like `AC-44109` |
| Book 206 or an unknown slot | 409, nothing booked |

Confirmation numbers are random five-digit `AC-` numbers, unique per running
instance. Bookings are held in memory and logged as one JSON line each to
CloudWatch, which is how the confirmation read on a call is matched to the
write. Formatting characters in a phone number (`(503) 555-0142`) are
accepted since they are the same number; a country code is not.

```bash
curl -sS -X POST "$BASE/lookup_avail_area_codes_serviced" \
  -H "Content-Type: application/json" \
  -H "X-Ridgeway-Key: ridgeway_hvac_fde" \
  -d '{"areaCode":"503"}'
```

## Running it yourself

```bash
uv sync && uv run pytest              # 46 tests against the app in-process
./deploy/smoke.sh "$BASE"             # the rubric table against a live URL
./deploy/deploy.sh                    # build and ship the Lambda, prints BASE
VAPI_API_KEY=... MOCK_BASE_URL="$BASE" python3 agent/provision.py
```

`deploy.sh` needs AWS credentials that can create an IAM role and a Lambda
function; it is idempotent and prints the public base URL. `provision.py`
creates the two tools, the assistant and a free Vapi number on first run and
updates them in place afterwards, remembering ids in `agent/state.json`.

## Layout

```
mock/app.py          the two endpoints
mock/handler.py      Lambda entry point (Mangum)
tests/test_mock.py   every rubric row and error code
deploy/deploy.sh     Lambda + Function URL, idempotent
deploy/smoke.sh      live check of the rubric table
agent/system_prompt.md   what the dispatcher is told
agent/provision.py       tools, assistant, phone number via the Vapi API
agent/state.json         the ids Vapi assigned
```

## Verification

See VERIFICATION.md for the transcripts of the golden-path call, the
interruption, the 206 refusal, and the matching mock log lines.
