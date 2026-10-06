"""Tests for scripts.regenerate_client — the client generation step (#618).

``openapi-python-client`` meets a construct it cannot model by printing a
warning, leaving the endpoint out, and exiting 0. A route could then vanish
from the client while the regeneration and the drift gate both stayed green, so
``generate`` treats any warning as a failure and says what the generator said.
"""

import json

import pytest

from scripts.regenerate_client import GenerationError, generate

_XML_ONLY = {
    "openapi": "3.1.0",
    "info": {"title": "t", "version": "1"},
    "paths": {
        "/x": {
            "post": {
                "operationId": "postX",
                "requestBody": {"content": {"application/xml": {"schema": {"type": "object"}}}},
                "responses": {"200": {"description": "ok"}},
            }
        }
    },
}


def test_a_skipped_endpoint_fails_generation(tmp_path):
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps(_XML_ONLY))
    with pytest.raises(GenerationError) as exc:
        generate(spec, tmp_path / "out")
    assert "Endpoint will not be generated" in str(exc.value)


def test_a_clean_spec_generates(tmp_path):
    clean = json.loads(json.dumps(_XML_ONLY))
    clean["paths"]["/x"]["post"].pop("requestBody")
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps(clean))
    generate(spec, tmp_path / "out")
    assert (tmp_path / "out" / "api" / "default" / "post_x.py").is_file()
