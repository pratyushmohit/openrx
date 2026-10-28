"""Keep the eval fixtures in the repo, not only in Floci's S3 (D-013).

`make up` starts Floci empty (D-003), which would lose the recorded fixtures. So they
are exported from S3 to evals/fixtures/ (committed and reviewable) and imported back
after `make up`. Fixture files never hold API keys or placeholder values (tested in
the bridge), so they are safe to commit.
"""

from pathlib import Path
from typing import Any

FIXTURES_DIR = Path(__file__).parents[3] / "evals" / "fixtures"
PREFIX = "fixtures/"


def list_keys(s3: Any, bucket: str) -> set[str]:
    """Every fixture key in the bucket."""
    keys: set[str] = set()
    token = None
    while True:
        page = s3.list_objects_v2(
            Bucket=bucket, Prefix=PREFIX, **({"ContinuationToken": token} if token else {})
        )
        keys |= {item["Key"] for item in page.get("Contents", [])}
        if not page.get("IsTruncated"):
            return keys
        token = page.get("NextContinuationToken")


def export_fixtures(s3: Any, bucket: str, directory: Path = FIXTURES_DIR) -> int:
    """Copy every fixture from S3 into `directory`; returns how many were written."""
    count = 0
    for key in sorted(list_keys(s3, bucket)):
        body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
        path = directory / key.removeprefix(PREFIX)
        path.parent.mkdir(parents=True, exist_ok=True)
        # LF endings, matching .gitattributes; the bridge already writes \n only.
        path.write_bytes(body.replace(b"\r\n", b"\n"))
        count += 1
    return count


def import_fixtures(s3: Any, bucket: str, directory: Path = FIXTURES_DIR) -> int:
    """Upload every fixture in `directory` to S3; returns how many were uploaded."""
    count = 0
    for path in sorted(directory.glob("*/*.json")):
        key = PREFIX + path.relative_to(directory).as_posix()
        s3.put_object(
            Bucket=bucket, Key=key, Body=path.read_bytes(), ContentType="application/json"
        )
        count += 1
    return count
