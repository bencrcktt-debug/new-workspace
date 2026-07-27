from __future__ import annotations

from streamlit.testing.v1 import AppTest


def test_all_navigation_sections_render() -> None:
    app = AppTest.from_file("app.py", default_timeout=120).run()
    assert not app.exception
    expected = [
        "Command center",
        "Watchlist",
        "Legislature",
        "Campaign finance",
        "Influence",
        "Government",
        "Media",
        "Legislators on X",
        "GOP calendar",
        "Source health",
    ]
    assert app.get("button_group")[0].options == expected
    for page in expected:
        app.get("button_group")[0].set_value(page)
        app.run()
        assert not app.exception
