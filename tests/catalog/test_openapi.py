import copy

import pytest
import yaml
from samples import OPENAPI_OVERLAY, OPENAPI_SPEC

from openrx.catalog.loader import CATALOG_DIR, load_source
from openrx.catalog.openapi import (
    OpenAPIImportError,
    Overlay,
    generate,
    generated_text,
    overlay_path,
    render,
    spec_path,
    write_source,
)

SPEC = OPENAPI_SPEC
OVERLAY = OPENAPI_OVERLAY


def overlay(**changes):
    data = copy.deepcopy(OVERLAY)
    data.update(changes)
    return Overlay.model_validate(data)


def operation(**changes):
    op = copy.deepcopy(OVERLAY["operations"][0])
    op.update(changes)
    return overlay(operations=[op])


def params_by_name(data, endpoint_index=0):
    return {p["name"]: p for p in data["endpoints"][endpoint_index]["params"]}


# --- generate ------------------------------------------------------------------


def test_generate_builds_a_source_from_spec_and_overlay():
    data = generate(overlay(), SPEC)

    assert data["id"] == "trials"
    assert data["base_url"] == "https://api.example.gov/v2"  # trailing "/" removed
    assert data["guide"] == "a\nb\n"
    search, study = data["endpoints"]
    assert (search["id"], search["path"]) == ("search_studies", "/studies")
    assert search["fixed_params"] == {"format": "json"}
    assert search["example_params"] == {"query.cond": "asthma"}
    assert search["response_notes"] == "studies is a list."
    assert study["params"] == [
        {"name": "nctId", "in": "path", "required": True, "description": "NCT id."}
    ]


def test_fixed_and_excluded_params_are_not_caller_params():
    names = params_by_name(generate(overlay(), SPEC))

    assert "format" not in names  # fixed (reached through a $ref)
    assert "geoDecay" not in names  # excluded
    assert set(names) == {"query.cond", "filter.overallStatus", "pageSize", "countTotal"}


def test_description_is_plain_text_without_relative_links():
    cond = params_by_name(generate(overlay(), SPEC))["query.cond"]

    assert cond["description"] == "Conditions in Essie syntax query."
    assert cond["example"] == "lung cancer"  # first of `examples`


def test_list_params_become_pipe_separated_strings_with_allowed_values():
    status = params_by_name(generate(overlay(), SPEC))["filter.overallStatus"]

    assert "type" not in status  # string is the default
    assert status["description"] == (
        "Statuses. Allowed: RECRUITING, COMPLETED. Several values: separate with |."
    )
    assert status["example"] == "RECRUITING|COMPLETED"


def test_scalar_types_and_missing_descriptions():
    names = params_by_name(generate(overlay(), SPEC))

    assert names["pageSize"]["type"] == "integer" and names["pageSize"]["example"] == 10
    assert names["countTotal"] == {
        "name": "countTotal",
        "type": "boolean",
        "description": "countTotal.",
    }


def test_generated_source_passes_the_schema():
    from openrx.catalog.schema import Source

    Source.model_validate(generate(overlay(), SPEC))


@pytest.mark.parametrize(
    ("make", "message"),
    [
        (lambda: (operation(operation="nope"), SPEC), "no operation 'nope'"),
        (lambda: (operation(operation="createStudy"), SPEC), "only GET operations"),
        (lambda: (operation(exclude_params=["gone"]), SPEC), "exclude_params not in the spec"),
        (lambda: (overlay(), {**SPEC, "servers": []}), "has no servers"),
        (lambda: (operation(example_params={"typo": 1}), SPEC), "generated source is invalid"),
    ],
)
def test_import_problems_are_reported(make, message):
    ov, spec = make()

    with pytest.raises(OpenAPIImportError, match=message):
        generate(ov, spec)


def test_only_local_refs_are_followed():
    spec = copy.deepcopy(SPEC)
    spec["paths"]["/studies"]["get"]["parameters"][0] = {"$ref": "https://evil.example/p.yaml"}

    with pytest.raises(OpenAPIImportError, match="only local"):
        generate(overlay(), spec)


# --- render and files -----------------------------------------------------------


def test_render_adds_header_and_writes_multiline_text_as_blocks():
    text = render(generate(overlay(), SPEC), SPEC)

    assert text.startswith("# GENERATED FILE: do not edit.")
    assert "spec, version 9.9." in text
    assert "guide: |\n  a\n  b\n" in text
    assert yaml.safe_load(text)["id"] == "trials"


def make_root(tmp_path, overlay_data=OVERLAY):
    (tmp_path / "openapi").mkdir()
    (tmp_path / "sources").mkdir()
    overlay_path(tmp_path, "trials").write_text(yaml.safe_dump(overlay_data), encoding="utf-8")
    spec_path(tmp_path, "trials").write_text(yaml.safe_dump(SPEC), encoding="utf-8")


def test_write_source_produces_a_file_the_loader_accepts(tmp_path):
    make_root(tmp_path)

    out = write_source(tmp_path, "trials")

    assert out == tmp_path / "sources" / "trials.yaml"
    assert b"\r\n" not in out.read_bytes()
    assert [e.id for e in load_source(out).endpoints] == ["search_studies", "study"]


def test_overlay_id_must_match_its_file(tmp_path):
    make_root(tmp_path, {**OVERLAY, "source": {**OVERLAY["source"], "id": "other"}})

    with pytest.raises(OpenAPIImportError, match="must be 'trials'"):
        generated_text(tmp_path, "trials")


def test_invalid_overlay_names_the_file(tmp_path):
    make_root(tmp_path, {**OVERLAY, "operatons": []})

    with pytest.raises(OpenAPIImportError, match="trials.yaml"):
        generated_text(tmp_path, "trials")


# --- the shipped generated source ------------------------------------------------


def test_committed_clinicaltrials_yaml_matches_its_inputs():
    # Fails if sources/clinicaltrials.yaml was edited by hand, or if the overlay or the
    # saved spec changed without regenerating. Fix: openrx catalog import-openapi clinicaltrials
    committed = (CATALOG_DIR / "sources" / "clinicaltrials.yaml").read_text(encoding="utf-8")

    assert committed == generated_text(CATALOG_DIR, "clinicaltrials")
