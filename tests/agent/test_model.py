from openrx.agent.model import DEFAULT_MODEL_ID, make_model


def test_model_points_at_the_configured_bedrock_endpoint():
    model = make_model(
        {"AWS_ENDPOINT_URL": "http://127.0.0.1:4566", "AWS_DEFAULT_REGION": "us-east-1"}
    )

    assert model.model_id == DEFAULT_MODEL_ID
    assert model.endpoint_url == "http://127.0.0.1:4566"
    assert model.temperature == 0


def test_model_id_can_be_changed():
    assert make_model({"OPENRX_MODEL_ID": "other-model"}).model_id == "other-model"
