from typing import Any

import pytest
from botocore.exceptions import ClientError

from openrx.bridge.secrets import KeyLookup, KeyStore, KeyUnavailable, parse_secret_arns
from openrx.catalog.loader import load_catalog
from openrx.catalog.schema import Source

CATALOG = load_catalog()


def shipped(source_id: str) -> Source:
    source = CATALOG.source(source_id)
    assert source is not None
    return source


OPENFDA = shipped("openfda")
RXNORM = shipped("rxnorm")
ARN = "arn:aws:secretsmanager:us-east-1:000000000000:secret:openrx/openfda_api_key-AbC123"
ARNS = {"openrx/openfda_api_key": ARN}


class FakeSecrets:
    """Stands in for boto3's Secrets Manager client."""

    def __init__(self, answer: Any) -> None:
        self.answer = answer
        self.calls: list[str] = []

    def get_secret_value(self, *, SecretId: str) -> dict[str, Any]:  # noqa: N803 - boto3's name
        self.calls.append(SecretId)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def client_error(code):
    return ClientError({"Error": {"Code": code, "Message": "x"}}, "GetSecretValue")


def required_source():
    data = OPENFDA.model_dump(by_alias=True)
    data["auth"]["optional"] = False
    return Source.model_validate(data)


# --- reading keys -----------------------------------------------------------------


def test_key_is_read_by_arn():
    secrets = FakeSecrets({"SecretString": "k-123"})

    lookup = KeyStore(secrets, ARNS).key_for(OPENFDA)

    assert lookup == KeyLookup("k-123")
    assert secrets.calls == [ARN]  # the ARN, never the name (D-007)


def test_key_is_cached_per_container():
    secrets = FakeSecrets({"SecretString": "k-123"})
    store = KeyStore(secrets, ARNS)

    store.key_for(OPENFDA)
    store.key_for(OPENFDA)

    assert len(secrets.calls) == 1


def test_source_without_auth_needs_no_key():
    secrets = FakeSecrets({"SecretString": "unused"})

    assert KeyStore(secrets, ARNS).key_for(RXNORM) == KeyLookup(None)
    assert secrets.calls == []


# --- missing optional keys: send without, with a note ------------------------------


@pytest.mark.parametrize(
    "answer",
    [
        {"ARN": ARN, "Name": "openrx/openfda_api_key"},  # Floci: no SecretString
        client_error("ResourceNotFoundException"),  # real AWS: no value yet
        {"SecretString": ""},
    ],
)
def test_secret_without_a_value_means_no_key(answer):
    lookup = KeyStore(FakeSecrets(answer), ARNS).key_for(OPENFDA)

    assert lookup.key is None
    assert "secret 'openrx/openfda_api_key' has no value set" in (lookup.note or "")
    assert "optional, so requests work without it" in (lookup.note or "")


def test_missing_arn_means_no_key():
    secrets = FakeSecrets({"SecretString": "k"})

    lookup = KeyStore(secrets, {}).key_for(OPENFDA)

    assert lookup.key is None and "no ARN configured" in (lookup.note or "")
    assert secrets.calls == []


def test_access_denied_is_reported_but_does_not_fail_an_optional_key():
    lookup = KeyStore(FakeSecrets(client_error("AccessDeniedException")), ARNS).key_for(OPENFDA)

    assert lookup.key is None
    assert "could not read secret 'openrx/openfda_api_key': AccessDeniedException" in (
        lookup.note or ""
    )


# --- required keys ----------------------------------------------------------------


def test_required_key_that_cannot_be_read_raises():
    store = KeyStore(FakeSecrets({"SecretString": ""}), ARNS)

    with pytest.raises(KeyUnavailable) as excinfo:
        store.key_for(required_source())

    assert excinfo.value.code == "missing_api_key"


def test_required_key_that_exists_is_returned():
    store = KeyStore(FakeSecrets({"SecretString": "k-9"}), ARNS)

    assert store.key_for(required_source()).key == "k-9"


def test_key_never_appears_in_notes_or_errors():
    store = KeyStore(FakeSecrets(client_error("AccessDeniedException")), ARNS)

    with pytest.raises(KeyUnavailable) as excinfo:
        store.key_for(required_source())

    assert "k-" not in str(excinfo.value)


# --- configuration ----------------------------------------------------------------


def test_parse_secret_arns():
    raw = '{"openrx/openfda_api_key": "arn:1", "openrx/ncbi_api_key": "arn:2"}'

    assert parse_secret_arns(raw) == {
        "openrx/openfda_api_key": "arn:1",
        "openrx/ncbi_api_key": "arn:2",
    }
    assert parse_secret_arns(None) == {} and parse_secret_arns("") == {}


@pytest.mark.parametrize(
    ("raw", "message"),
    [("{not json", "not valid JSON"), ('["a"]', "JSON object"), ('{"a": 1}', "JSON object")],
)
def test_bad_secret_arns_config(raw, message):
    with pytest.raises(ValueError, match=message):
        parse_secret_arns(raw)


def test_shipped_sources_with_keys_are_optional():
    # Every keyed source works without its key (at lower rate limits), so a fresh
    # setup runs before anyone has registered for API keys.
    for source in CATALOG:
        if source.auth:
            assert source.auth.optional, source.id
