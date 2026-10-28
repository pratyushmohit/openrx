"""Read sources' API keys from Secrets Manager (README FR-7).

Only the bridge's IAM role may read these secrets (D-007, E5), and the keys never
leave the bridge: not in results, errors, notes or logs (NFR-3).

Secrets are read by full ARN (D-007; Floci denies a permitted read by name). The
bridge gets a map from each catalog secret name to its ARN from Terraform, as JSON in
OPENRX_SECRET_ARNS. A key is read once per container and reused while it stays warm.
"""

import json
from dataclasses import dataclass
from typing import Any, Protocol

from botocore.exceptions import ClientError

from openrx.catalog.schema import Source

# Error codes that mean "this secret has no value" rather than "something is wrong".
# Real AWS raises ResourceNotFoundException for a secret with no value yet.
_NOT_SET_CODES = frozenset({"ResourceNotFoundException"})


class SecretsClient(Protocol):
    """The one boto3 Secrets Manager call the key store needs."""

    def get_secret_value(self, *, SecretId: str) -> dict[str, Any]:  # noqa: N803 - boto3's name
        """Return the secret's current value (boto3's response dict)."""
        ...


class KeyUnavailable(Exception):  # noqa: N818 - reads as a state, like RequestRejected
    """A required API key could not be read. Never contains the key itself."""

    def __init__(self, code: str, message: str) -> None:
        """Create the error with a short machine-readable code and a message."""
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class KeyLookup:
    """The key for a source (None if there isn't one) and why, when it's missing."""

    key: str | None
    note: str | None = None


def parse_secret_arns(raw: str | None) -> dict[str, str]:
    """Parse OPENRX_SECRET_ARNS: a JSON object of secret name -> ARN ("" or unset: {}).

    Raises:
        ValueError: if it isn't a JSON object of strings.
    """
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"OPENRX_SECRET_ARNS is not valid JSON: {e.msg}") from None
    if not isinstance(data, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in data.items()
    ):
        raise ValueError("OPENRX_SECRET_ARNS must be a JSON object of secret name to ARN")
    return data


class KeyStore:
    """Looks up and caches each source's API key."""

    def __init__(self, client: SecretsClient, secret_arns: dict[str, str]) -> None:
        """Create a store reading from `client`, with ARNs keyed by catalog secret name."""
        self._client = client
        self._arns = dict(secret_arns)
        self._cache: dict[str, KeyLookup] = {}

    def key_for(self, source: Source) -> KeyLookup:
        """The API key to send for `source`.

        Optional keys that are missing or unreadable give KeyLookup(None, note): the
        request still goes out, with the source's lower rate limit.

        Raises:
            KeyUnavailable: if the source's key is required and can't be read.
        """
        if source.auth is None:
            return KeyLookup(None)
        name = source.auth.secret
        if name not in self._cache:
            # Cached either way: a key added later is picked up by the next container.
            self._cache[name] = self._read(name)
        lookup = self._cache[name]
        if lookup.key is None and not source.auth.optional:
            raise KeyUnavailable("missing_api_key", f"{source.id} needs an API key: {lookup.note}")
        return lookup

    def _read(self, name: str) -> KeyLookup:
        arn = self._arns.get(name)
        if arn is None:
            return KeyLookup(None, f"no ARN configured for secret {name!r}")
        try:
            response = self._client.get_secret_value(SecretId=arn)
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "Unknown")
            if code in _NOT_SET_CODES:
                return KeyLookup(None, f"secret {name!r} has no value set")
            # e.g. AccessDeniedException: a misconfigured role. Say so, but don't fail
            # optional-key requests over it.
            return KeyLookup(None, f"could not read secret {name!r}: {code}")
        # Floci returns no SecretString for a secret with no value; real AWS raises.
        key = response.get("SecretString") or None
        return KeyLookup(key, None if key else f"secret {name!r} has no value set")
