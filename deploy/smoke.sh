#!/usr/bin/env bash
# Walk the packet's rubric table against a live base URL.
#   ./deploy/smoke.sh https://your-mock-host
set -uo pipefail
BASE="${1:?usage: smoke.sh BASE_URL}"
KEY="X-Ridgeway-Key: ridgeway_hvac_fde"
fail=0

check() { # label expected_status method path [key-header] body
  local label="$1" want="$2" path="$3" hdr="$4" body="$5"
  local out code
  out="$(curl -sS -o /dev/stderr -w '%{http_code}' -X POST "$BASE$path" -H 'Content-Type: application/json' -H "$hdr" -d "$body" 2>/tmp/smoke_body)"
  code="$out"
  if [ "$code" = "$want" ]; then echo "PASS $code  $label"; else echo "FAIL got $code want $want  $label"; fail=1; fi
  echo "     $(cat /tmp/smoke_body)"
}

check "lookup 503 serviced, Portland, 2 slots"  200 /lookup_avail_area_codes_serviced "$KEY" '{"areaCode":"503"}'
check "lookup 360 serviced, Vancouver WA"       200 /lookup_avail_area_codes_serviced "$KEY" '{"areaCode":"360"}'
check "lookup 206 not serviced, no slots"       200 /lookup_avail_area_codes_serviced "$KEY" '{"areaCode":"206"}'
check "lookup full phone as areaCode is 400"    400 /lookup_avail_area_codes_serviced "$KEY" '{"areaCode":"5035550142"}'
check "lookup +1503 is 400"                     400 /lookup_avail_area_codes_serviced "$KEY" '{"areaCode":"+1503"}'
check "lookup without key is 401"               401 /lookup_avail_area_codes_serviced "X-Nope: 1" '{"areaCode":"503"}'
check "lookup with wrong key is 401"            401 /lookup_avail_area_codes_serviced "X-Ridgeway-Key: wrong" '{"areaCode":"503"}'
check "book 503 morning is 201 with AC-"        201 /book_appointment "$KEY" '{"areaCode":"503","slotId":"SLOT-TUE-AM","name":"Marcus Hale","phone":"5035550142","symptom":"AC blowing warm air"}'
check "book 206 is 409"                         409 /book_appointment "$KEY" '{"areaCode":"206","slotId":"SLOT-TUE-AM","name":"Marcus Hale","phone":"2065550142","symptom":"AC blowing warm air"}'
check "book unknown slot is 409"                409 /book_appointment "$KEY" '{"areaCode":"503","slotId":"SLOT-FRI-AM","name":"Marcus Hale","phone":"5035550142","symptom":"AC blowing warm air"}'
check "book with country code phone is 400"     400 /book_appointment "$KEY" '{"areaCode":"503","slotId":"SLOT-TUE-AM","name":"Marcus Hale","phone":"+15035550142","symptom":"AC blowing warm air"}'
check "book without key is 401"                 401 /book_appointment "X-Nope: 1" '{"areaCode":"503","slotId":"SLOT-TUE-AM","name":"Marcus Hale","phone":"5035550142","symptom":"AC blowing warm air"}'

[ $fail = 0 ] && echo "ALL PASS" || { echo "SOME FAILED"; exit 1; }
