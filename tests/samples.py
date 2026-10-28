"""Sample data shared by several test files."""

# A small OpenAPI spec and the overlay that imports it (see openrx.catalog.openapi).
OPENAPI_SPEC = {
    "openapi": "3.0.3",
    "info": {"version": "9.9"},
    "servers": [{"url": "https://api.example.gov/v2/"}],
    "paths": {
        "/studies": {
            "get": {
                "operationId": "listStudies",
                "parameters": [
                    {"$ref": "#/components/parameters/Format"},
                    {
                        "name": "query.cond",
                        "in": "query",
                        "description": "Conditions in [Essie syntax](/find-studies/x)\n  query",
                        "schema": {"type": "string"},
                        "examples": {"one": {"value": "lung cancer"}},
                    },
                    {
                        "name": "filter.overallStatus",
                        "in": "query",
                        "description": "Statuses",
                        "schema": {
                            "type": "array",
                            "items": {"$ref": "#/components/schemas/Status"},
                        },
                        "example": ["RECRUITING", "COMPLETED"],
                    },
                    {
                        "name": "pageSize",
                        "in": "query",
                        "schema": {"type": "integer"},
                        "example": 10,
                    },
                    {"name": "countTotal", "in": "query", "schema": {"type": "boolean"}},
                    {"name": "geoDecay", "in": "query", "schema": {"type": "string"}},
                ],
            },
            "post": {"operationId": "createStudy"},
        },
        "/studies/{nctId}": {
            "get": {
                "operationId": "fetchStudy",
                "parameters": [
                    {
                        "name": "nctId",
                        "in": "path",
                        "required": True,
                        "description": "NCT id.",
                        "schema": {"type": "string"},
                    }
                ],
            }
        },
    },
    "components": {
        "parameters": {
            "Format": {
                "name": "format",
                "in": "query",
                "schema": {"type": "string", "enum": ["json", "csv"]},
            }
        },
        "schemas": {"Status": {"type": "string", "enum": ["RECRUITING", "COMPLETED"]}},
    },
}

OPENAPI_OVERLAY = {
    "spec_url": "https://api.example.gov/oas",
    "source": {
        "id": "trials",
        "name": "Trials",
        "rate_limit": {"per_second": 1},
        "guide": "a\nb\n",
    },
    "operations": [
        {
            "operation": "listStudies",
            "id": "search_studies",
            "description": "Search studies.",
            "fixed_params": {"format": "json"},
            "exclude_params": ["geoDecay"],
            "example_params": {"query.cond": "asthma"},
            "response_notes": "studies is a list.",
        },
        {"operation": "fetchStudy", "id": "study", "description": "One study."},
    ],
}
