import json
import textwrap

import pytest

from openrx.catalog.loader import (
    CATALOG_DIR,
    CatalogError,
    Problem,
    example_path,
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
  - id: drug_label
    path: /drug/label.json
    description: Drug labels.
"""


def write(root, name, text):
    """Write a source file into root/sources/."""
    path = root / "sources" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text), encoding="utf-8")
    return path


def write_example(root, source_id, endpoint_id, content):
    path = example_path(root, source_id, endpoint_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = content if isinstance(content, str) else json.dumps(content)
    path.write_text(text, encoding="utf-8")
    return path


def write_examples_for_all(root, source_id):
    for endpoint_id in ("drug_event", "drug_label"):
        write_example(root, source_id, endpoint_id, {"response": {"results": [endpoint_id]}})


def problems_of(path, examples_root=None):
    with pytest.raises(CatalogError) as excinfo:
        load_source(path, examples_root=examples_root)
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


def test_default_catalog_is_inside_the_package():
    assert CATALOG_DIR.parts[-2:] == ("openrx", "catalog")
    assert (CATALOG_DIR / "sources").is_dir()


# --- examples ----------------------------------------------------------------


def test_examples_are_attached_from_their_own_files(tmp_path):
    write(tmp_path, "openfda.yaml", VALID.format(id="openfda"))
    write_example(tmp_path, "openfda", "drug_event", {"request": {}, "response": {"results": [1]}})

    source = load_catalog(tmp_path).source("openfda")

    assert source is not None
    assert source.endpoint("drug_event").example_response == {"results": [1]}  # type: ignore[union-attr]
    assert source.endpoint("drug_label").example_response is None  # type: ignore[union-attr]


def test_example_in_yaml_is_rejected_so_examples_have_one_home(tmp_path):
    text = VALID.format(id="openfda").replace(
        "    description: Drug labels.\n",
        "    description: Drug labels.\n    example_response: {}\n",
    )
    path = write(tmp_path, "openfda.yaml", text)

    [problem] = problems_of(path)

    assert problem.location == "endpoints[1] (drug_label).example_response"
    assert "examples live in examples/" in problem.message


def test_broken_example_json_names_the_example_file(tmp_path):
    path = write(tmp_path, "openfda.yaml", VALID.format(id="openfda"))
    write_example(tmp_path, "openfda", "drug_event", '{"response": [1, }')

    [problem] = problems_of(path, examples_root=tmp_path)

    assert problem.file == "examples/openfda/drug_event.json"
    assert problem.message.startswith("invalid JSON")


def test_example_file_needs_a_response_key(tmp_path):
    path = write(tmp_path, "openfda.yaml", VALID.format(id="openfda"))
    write_example(tmp_path, "openfda", "drug_event", {"results": []})

    [problem] = problems_of(path, examples_root=tmp_path)

    assert problem.message == "must be an object with a 'response' key"


def test_loading_without_examples_root_ignores_example_files(tmp_path):
    path = write(tmp_path, "openfda.yaml", VALID.format(id="openfda"))
    write_example(tmp_path, "openfda", "drug_event", "not json")

    assert load_source(path).endpoints[0].example_response is None


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
def test_empty_or_missing_sources_folder_is_a_problem(tmp_path, make_dir):
    if make_dir:
        (tmp_path / "sources").mkdir()

    with pytest.raises(CatalogError, match="no catalog files"):
        load_catalog(tmp_path)


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


def test_lint_requires_an_example_for_every_endpoint(tmp_path):
    write(tmp_path, "openfda.yaml", VALID.format(id="openfda"))
    write_example(tmp_path, "openfda", "drug_event", {"response": {}})

    assert len(load_catalog(tmp_path).sources) == 1  # drafts load fine
    assert lint_catalog(tmp_path) == [
        Problem(
            "openfda.yaml",
            "endpoints[1] (drug_label)",
            "missing example: run 'openrx catalog capture-examples openfda'",
        )
    ]


def test_lint_reports_examples_for_removed_endpoints(tmp_path):
    write(tmp_path, "openfda.yaml", VALID.format(id="openfda"))
    write_examples_for_all(tmp_path, "openfda")
    write_example(tmp_path, "openfda", "drug_old_name", {"response": {}})

    assert lint_catalog(tmp_path) == [
        Problem(
            "examples/openfda/drug_old_name.json", "", "no endpoint 'drug_old_name' in openfda.yaml"
        )
    ]


def test_lint_reports_examples_for_removed_sources(tmp_path):
    write(tmp_path, "openfda.yaml", VALID.format(id="openfda"))
    write_examples_for_all(tmp_path, "openfda")
    write_example(tmp_path, "gone", "anything", {"response": {}})

    assert lint_catalog(tmp_path) == [Problem("examples/gone", "", "no source with this id")]


def test_lint_does_not_double_report_examples_of_a_broken_source(tmp_path):
    write(tmp_path, "openfda.yaml", "id: [broken\n")
    write_example(tmp_path, "openfda", "drug_event", {"response": {}})

    [problem] = lint_catalog(tmp_path)

    assert problem.file == "openfda.yaml"


def test_lint_of_a_complete_catalog_is_clean(tmp_path):
    write(tmp_path, "openfda.yaml", VALID.format(id="openfda"))
    write_examples_for_all(tmp_path, "openfda")

    assert lint_catalog(tmp_path) == []
