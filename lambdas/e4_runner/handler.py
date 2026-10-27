# TEMP(M0): E4 stand-in for the execute-runner (docs/m0-findings.md, E4).
# Proves one Lambda can invoke another on Floci and times the hop.
# Delete once the real runner and bridge Lambdas exist (M3).
import json
import os
import time

import boto3

# boto3 uses the AWS_ENDPOINT_URL that Floci injects (http://floci:4566), so
# these calls go to Floci, not real AWS. Created once per container.
_LAMBDA = boto3.client("lambda")
_BRIDGE = os.environ["BRIDGE_FUNCTION"]


def handler(event, context):
    timings_ms = []
    reply = None
    for _ in range(event.get("calls", 1)):
        started = time.perf_counter()
        response = _LAMBDA.invoke(FunctionName=_BRIDGE, Payload=b"{}")
        reply = json.loads(response["Payload"].read())
        timings_ms.append(round((time.perf_counter() - started) * 1000, 2))
    return {
        "endpoint": os.environ.get("AWS_ENDPOINT_URL"),
        "bridge_reply": reply,
        "hop_ms": timings_ms,
    }
