# TEMP(M0): E4 stand-in for the bridge (docs/m0-findings.md, E4).
# Reads the openFDA key from Secrets Manager and reports whether it got one.
# It never returns or logs the value itself (README NFR-3).
# Delete once the real bridge Lambda exists (M3).
import os

import boto3

_SECRETS = boto3.client("secretsmanager")
_SECRET_ID = os.environ["OPENFDA_SECRET_ID"]
_cached_key = None  # read once per container, then reused while warm


def handler(event, context):
    global _cached_key
    was_cached = _cached_key is not None
    if not was_cached:
        _cached_key = _SECRETS.get_secret_value(SecretId=_SECRET_ID)["SecretString"]
    return {
        "secret_found": bool(_cached_key),
        "secret_length": len(_cached_key),
        "cached": was_cached,
    }
