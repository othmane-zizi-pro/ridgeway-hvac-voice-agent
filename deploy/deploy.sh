#!/usr/bin/env bash
# Deploy the mock to AWS Lambda behind a public Function URL. Idempotent:
# run it again to ship a new build. Needs the AWS CLI with credentials that
# can manage IAM roles and Lambda functions (set AWS_PROFILE if you use one).
#
#   ./deploy/deploy.sh            # prints the public base URL at the end
set -euo pipefail

FUNCTION_NAME="${FUNCTION_NAME:-ridgeway-hvac-mock}"
ROLE_NAME="${ROLE_NAME:-ridgeway-hvac-mock-lambda}"
REGION="${AWS_REGION:-us-east-1}"
RUNTIME="python3.12"
ARCH="x86_64"
TAGS="Project=ridgeway-hvac-mock"

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BUILD="$ROOT/build"
rm -rf "$BUILD" && mkdir -p "$BUILD/pkg"

echo "Packaging dependencies for $RUNTIME/$ARCH"
python3 -m pip install -q \
  --target "$BUILD/pkg" \
  --platform manylinux2014_x86_64 \
  --implementation cp \
  --python-version 3.12 \
  --only-binary=:all: \
  -r "$ROOT/mock/requirements.txt"
cp "$ROOT/mock/app.py" "$ROOT/mock/handler.py" "$BUILD/pkg/"
(cd "$BUILD/pkg" && zip -qr "$BUILD/function.zip" . -x '*.pyc' -x '__pycache__/*')
echo "Zip: $(du -h "$BUILD/function.zip" | cut -f1)"

if ! aws iam get-role --role-name "$ROLE_NAME" >/dev/null 2>&1; then
  echo "Creating IAM role $ROLE_NAME"
  aws iam create-role --role-name "$ROLE_NAME" \
    --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}' \
    --tags Key=Project,Value=ridgeway-hvac-mock >/dev/null
  aws iam attach-role-policy --role-name "$ROLE_NAME" \
    --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
  echo "Waiting for the role to propagate"; sleep 10
fi
ROLE_ARN="$(aws iam get-role --role-name "$ROLE_NAME" --query Role.Arn --output text)"

if aws lambda get-function --function-name "$FUNCTION_NAME" --region "$REGION" >/dev/null 2>&1; then
  echo "Updating $FUNCTION_NAME"
  aws lambda update-function-code --function-name "$FUNCTION_NAME" --region "$REGION" \
    --zip-file "fileb://$BUILD/function.zip" >/dev/null
else
  echo "Creating $FUNCTION_NAME"
  aws lambda create-function --function-name "$FUNCTION_NAME" --region "$REGION" \
    --runtime "$RUNTIME" --architectures "$ARCH" --handler handler.handler \
    --role "$ROLE_ARN" --zip-file "fileb://$BUILD/function.zip" \
    --timeout 10 --memory-size 512 --tags "$TAGS" >/dev/null
fi
aws lambda wait function-updated --function-name "$FUNCTION_NAME" --region "$REGION"

if ! aws lambda get-function-url-config --function-name "$FUNCTION_NAME" --region "$REGION" >/dev/null 2>&1; then
  echo "Creating public Function URL"
  aws lambda create-function-url-config --function-name "$FUNCTION_NAME" --region "$REGION" \
    --auth-type NONE >/dev/null
  aws lambda add-permission --function-name "$FUNCTION_NAME" --region "$REGION" \
    --statement-id public-url --action lambda:InvokeFunctionUrl \
    --principal '*' --function-url-auth-type NONE >/dev/null
  # Newer accounts also need plain InvokeFunction for a public URL, or every call is a 403.
  aws lambda add-permission --function-name "$FUNCTION_NAME" --region "$REGION" \
    --statement-id public-invoke --action lambda:InvokeFunction --principal '*' >/dev/null
fi

URL="$(aws lambda get-function-url-config --function-name "$FUNCTION_NAME" --region "$REGION" --query FunctionUrl --output text)"
echo "BASE=${URL%/}"
