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
  --query 'events[].[timestamp,message]' --output text \
  | while IFS=$'\t' read -r ts msg; do
      printf '%s  %s\n' "$(date -r $((ts/1000)) '+%H:%M:%S')" "$msg"
    done
