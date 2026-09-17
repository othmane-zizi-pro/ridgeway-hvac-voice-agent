#!/usr/bin/env bash
# Print the mock's recent request and booking log lines from CloudWatch.
#   ./deploy/logs.sh [minutes-back, default 30]
set -euo pipefail
MINUTES="${1:-30}"
FUNCTION_NAME="${FUNCTION_NAME:-ridgeway-hvac-mock}"
REGION="${AWS_REGION:-us-east-1}"
SINCE=$(( ($(date +%s) - MINUTES * 60) * 1000 ))
aws logs filter-log-events --region "$REGION" \
  --log-group-name "/aws/lambda/$FUNCTION_NAME" \
  --start-time "$SINCE" \
  --filter-pattern '{ $.path = * || $.event = "booked" }' \
  --output json \
  | python3 -c '
import json, sys, datetime
for e in json.load(sys.stdin)["events"]:
    ts = datetime.datetime.fromtimestamp(e["timestamp"] / 1000).strftime("%H:%M:%S")
    print(ts, e["message"].strip().split("\t")[-1])
'
