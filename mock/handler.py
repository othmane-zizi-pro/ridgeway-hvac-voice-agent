"""AWS Lambda entry point. Mangum adapts the Lambda Function URL event to ASGI."""

from mangum import Mangum

from app import app

handler = Mangum(app, lifespan="off")
