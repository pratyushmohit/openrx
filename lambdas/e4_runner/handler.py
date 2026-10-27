# TEMP(M0): E4/E5 stand-in for the execute-runner (docs/m0-findings.md, E4, E5).
# E4: proves one Lambda can invoke another on Floci and times the hop.
# E5: attempts actions its role does / does not allow, to see what Floci blocks.
# Delete once the real runner and bridge Lambdas exist (M3).
import json
import os
import time
import urllib.request

import boto3
from botocore.exceptions import ClientError

# boto3 uses the AWS_ENDPOINT_URL that Floci injects (http://floci:4566), so
# these calls go to Floci, not real AWS. Created once per container.
_LAMBDA = boto3.client("lambda")
_SECRETS = boto3.client("secretsmanager")
_STS = boto3.client("sts")
_BRIDGE = os.environ["BRIDGE_FUNCTION"]
_SECRET_ID = os.environ["OPENFDA_SECRET_ID"]


def _attempt(action):
    """Try one AWS action; report allowed/denied without returning secret values."""
    try:
        if action == "invoke_bridge":  # allowed by the role
            _LAMBDA.invoke(FunctionName=_BRIDGE, Payload=b"{}")
        elif action == "read_secret":  # NOT allowed: only the bridge may
            _SECRETS.get_secret_value(SecretId=_SECRET_ID)
        elif action == "invoke_self":  # NOT allowed: only the bridge may be invoked
            _LAMBDA.invoke(
                FunctionName=os.environ["AWS_LAMBDA_FUNCTION_NAME"],
                Payload=b'{"noop": true}',
            )
        elif action == "list_functions":  # NOT allowed
            _LAMBDA.list_functions()
        elif action == "list_secrets":  # NOT allowed
            _SECRETS.list_secrets()
        elif action == "internet":  # network, not IAM: can the container reach out?
            urllib.request.urlopen(
                "https://api.fda.gov/drug/event.json?limit=1", timeout=5
            )
        return "allowed"
    except ClientError as e:
        return f"denied ({e.response['Error']['Code']})"
    except OSError as e:
        return f"blocked ({type(e).__name__})"


def handler(event, context):
    if event.get("noop"):
        return {}
    if "attempts" in event:
        return {
            "caller": _STS.get_caller_identity()["Arn"],
            "has_role_credentials": "AWS_SESSION_TOKEN" in os.environ,
            "results": {action: _attempt(action) for action in event["attempts"]},
        }

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
