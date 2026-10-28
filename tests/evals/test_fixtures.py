import io

from openrx.evals.fixtures import export_fixtures, import_fixtures, list_keys


class FakeS3:
    """In-memory S3 that pages its listings two keys at a time."""

    def __init__(self, objects=None):
        self.objects = dict(objects or {})

    def list_objects_v2(self, Bucket, Prefix, ContinuationToken=None):  # noqa: N803
        keys = sorted(k for k in self.objects if k.startswith(Prefix))
        start = int(ContinuationToken or 0)
        page = keys[start : start + 2]
        more = start + 2 < len(keys)
        return {
            "Contents": [{"Key": k} for k in page],
            "IsTruncated": more,
            **({"NextContinuationToken": str(start + 2)} if more else {}),
        }

    def get_object(self, Bucket, Key):  # noqa: N803
        return {"Body": io.BytesIO(self.objects[Key])}

    def put_object(self, Bucket, Key, Body, ContentType):  # noqa: N803
        self.objects[Key] = Body


OBJECTS = {
    "fixtures/openfda/aaa.json": b'{"version": 1}\r\n',
    "fixtures/openfda/bbb.json": b'{"version": 1}',
    "fixtures/rxnorm/ccc.json": b'{"version": 1}',
    "other/ignored.json": b"{}",
}


def test_list_keys_follows_every_page_and_only_fixtures():
    assert list_keys(FakeS3(OBJECTS), "b") == {
        "fixtures/openfda/aaa.json",
        "fixtures/openfda/bbb.json",
        "fixtures/rxnorm/ccc.json",
    }


def test_export_writes_one_file_per_fixture_with_lf_endings(tmp_path):
    count = export_fixtures(FakeS3(OBJECTS), "b", tmp_path)

    assert count == 3
    assert sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*.json")) == [
        "openfda/aaa.json",
        "openfda/bbb.json",
        "rxnorm/ccc.json",
    ]
    assert b"\r\n" not in (tmp_path / "openfda" / "aaa.json").read_bytes()


def test_import_round_trips_an_export(tmp_path):
    export_fixtures(FakeS3(OBJECTS), "b", tmp_path)
    fresh = FakeS3()

    count = import_fixtures(fresh, "b", tmp_path)

    assert count == 3
    assert set(fresh.objects) == {k for k in OBJECTS if k.startswith("fixtures/")}


def test_import_of_an_empty_folder(tmp_path):
    assert import_fixtures(FakeS3(), "b", tmp_path) == 0
