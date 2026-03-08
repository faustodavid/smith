from __future__ import annotations

from smith.formatting import dumps_json, make_envelope, render_text


def test_json_envelope_shape() -> None:
    payload = make_envelope(ok=True, command="orgs", data=[{"name": "A"}], meta={}, error=None)
    rendered = dumps_json(payload)

    assert '"ok": true' in rendered
    assert '"command": "orgs"' in rendered
    assert '"data"' in rendered
    assert '"error": null' in rendered


def test_single_provider_rendering_flattens() -> None:
    grouped = {
        "providers": {
            "azdo": {
                "ok": True,
                "data": [{"name": "repo-a"}],
                "warnings": [],
                "partial": False,
                "error": None,
            }
        },
        "summary": {
            "requested_provider": "azdo",
            "queried": ["azdo"],
            "succeeded": ["azdo"],
            "failed": [],
        },
    }

    text = render_text("repos", grouped)
    assert text.strip() == "repo-a"
    assert "[azdo]" not in text
