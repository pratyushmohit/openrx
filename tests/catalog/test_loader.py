import textwrap

import pytest

from openrx.catalog.loader import (
    SOURCES_DIR,
    CatalogError,
    Problem,
    lint_catalog,
    load_catalog,
    load_source,
)

VALID = """\
id: {id}
name: Example
base_url: https://api.example.gov
rate_limit: {{per_second: 4}}
endpoints:
  - id: drug_event
    path: /drug/event.json
    description: Adverse event reports.
    example_response: {{results: []}}
  - id: drug_label
    path: /drug/label.json
    description: Drug labels.
    example_response: {{results: []}}
"""


def write(directory, name, text):
    path = directory / name
    path.write_text(textwrap.dedent(text), encoding="utf-8")
    return path


def problems_of(path):
    with pytest.raises(CatalogError) as excinfo:
        load_source(path)
    return excinfo.value.problems


# --- happy path --------------------------------------------------------------


def test_loads_every_yaml_file_by_id(tmp_path):
    write(tmp_path, "openfda.yaml", VALID.format(id="openfda"))
    write(tmp_path, "rxnorm.yaml", VALID.format(id="rxnorm"))
    write(tmp_path, "notes.txt", "not a catalog file")

    catalog = load_catalog(tmp_path)

    assert sorted(catalog.sources) == ["openfda", "rxnorm"]
    assert catalog.source("rxnorm") is catalog.sources["rxnorm"]
    assert catalog.source("missing") is None
    assert [s.id for s in catalog] == ["openfda", "rxnorm"]


def test_loaded_catalog_is_read_only(tmp_path):
    write(tmp_path, "openfda.yaml", VALID.format(id="openfda"))

    catalog = load_catalog(tmp_path)

    with pytest.raises(TypeError):
        catalog.sources["evil"] = catalog.sources["openfda"]  # type: ignore[index]


def test_problem_formats_as_one_line():
    assert str(Problem("a.yaml", "endpoints[0].path", "bad")) == "a.yaml: endpoints[0].path: bad"
    assert str(Problem("a.yaml", "", "bad")) == "a.yaml: bad"


# --- file-level problems ------------------------------------------------------


def test_invalid_yaml_reports_the_line(tmp_path):
    path = write(tmp_path, "openfda.yaml", "id: openfda\nendpoints: [unclosed\n")

    [problem] = problems_of(path)

    assert problem.file == "openfda.yaml"
    assert "invalid YAML" in problem.message
    assert "line" in problem.message


def test_duplicate_keys_are_rejected(tmp_path):
    path = write(tmp_path, "openfda.yaml", "id: openfda\nid: other\n")

    [problem] = problems_of(path)

    assert "duplicate key 'id'" in problem.message
    assert "line 2" in problem.message


def test_top_level_must_be_a_mapping(tmp_path):
    path = write(tmp_path, "openfda.yaml", "- just\n- a list\n")

    [problem] = problems_of(path)

    assert problem.message == "top level must be a mapping (key: value pairs)"


def test_yaml_cannot_construct_python_objects(tmp_path):
    path = write(tmp_path, "openfda.yaml", "id: !!python/object/apply:os.system [echo hi]\n")

    [problem] = problems_of(path)

    assert "invalid YAML" in problem.message


def test_file_name_must_match_source_id(tmp_path):
    write(tmp_path, "openfda.yaml", VALID.format(id="rxnorm"))

    with pytest.raises(CatalogError) as excinfo:
        load_catalog(tmp_path)

    [problem] = excinfo.value.problems
    assert problem.location == "id"
    assert "must match the file name" in problem.message


@pytest.mark.parametrize("make_dir", [True, False])
def test_empty_or_missing_folder_is_a_problem(tmp_path, make_dir):
    directory = tmp_path / "sources"
    if make_dir:
        directory.mkdir()

    with pytest.raises(CatalogError, match="no catalog files"):
        load_catalog(directory)


# --- schema problems, formatted for humans -----------------------------------


def test_schema_errors_name_the_file_field_and_endpoint_id(tmp_path):
    text = VALID.format(id="openfda").replace("path: /drug/label.json", "path: drug/label.json")
    path = write(tmp_path, "openfda.yaml", text)

    [problem] = problems_of(path)

    assert problem.file == "openfda.yaml"
    assert problem.location == "endpoints[1] (drug_label).path"
    assert problem.message == "must start with '/'"  # "Value error, " prefix removed


def test_typo_is_reported_as_unknown_field(tmp_path):
    text = VALID.format(id="openfda").replace(
        "description: Drug labels.", "descripton: Drug labels."
    )
    path = write(tmp_path, "openfda.yaml", text)

    messages = {(p.location, p.message) for p in problems_of(path)}

    assert ("endpoints[1] (drug_label).descripton", "unknown field (typo?)") in messages
    assert ("endpoints[1] (drug_label).description", "required field is missing") in messages


def test_knock_on_too_short_error_is_dropped(tmp_path):
    text = """\
    id: openfda
    name: Example
    base_url: https://api.example.gov
    rate_limit: {per_second: 4}
    endpoints:
      - id: only_one
        path: no-slash
        description: Broken.
    """
    path = write(tmp_path, "openfda.yaml", text)

    problems = problems_of(path)

    assert [p.location for p in problems] == ["endpoints[0] (only_one).path"]


def test_genuinely_empty_endpoint_list_is_still_reported(tmp_path):
    text = VALID.format(id="openfda").split("endpoints:")[0] + "endpoints: []\n"
    path = write(tmp_path, "openfda.yaml", text)

    [problem] = problems_of(path)

    assert problem.location == "endpoints"
    assert "at least 1 item" in problem.message


def test_all_files_are_checked_before_failing(tmp_path):
    write(tmp_path, "openfda.yaml", "id: [broken\n")
    write(tmp_path, "pubmed.yaml", VALID.format(id="pubmed").replace("https://", "http://"))
    write(tmp_path, "rxnorm.yaml", VALID.format(id="rxnorm"))

    with pytest.raises(CatalogError) as excinfo:
        load_catalog(tmp_path)

    assert {p.file for p in excinfo.value.problems} == {"openfda.yaml", "pubmed.yaml"}


# --- lint: stricter than loading -----------------------------------------------


def test_lint_requires_example_responses_but_loading_does_not(tmp_path):
    text = VALID.format(id="openfda").replace("    example_response: {results: []}\n", "", 1)
    write(tmp_path, "openfda.yaml", text)

    assert len(load_catalog(tmp_path).sources) == 1  # drafts load fine
    assert lint_catalog(tmp_path) == [
        Problem("openfda.yaml", "endpoints[0] (drug_event)", "missing example_response")
    ]


def test_lint_returns_load_problems_too(tmp_path):
    write(tmp_path, "openfda.yaml", "id: [broken\n")

    [problem] = lint_catalog(tmp_path)

    assert "invalid YAML" in problem.message


def test_lint_of_a_valid_catalog_is_clean(tmp_path):
    write(tmp_path, "openfda.yaml", VALID.format(id="openfda"))

    assert lint_catalog(tmp_path) == []


def test_default_folder_is_inside_the_package():
    assert SOURCES_DIR.parts[-3:] == ("openrx", "catalog", "sources")
