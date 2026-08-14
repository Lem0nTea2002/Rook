from rook_seed.renderers import render_html, render_text


def test_html_uses_heading_term() -> None:
    assert 'title="Permalink to this heading"' in render_html("Rook")


def test_text_uses_heading_term() -> None:
    assert render_text("Rook").endswith("Permalink to this heading")
