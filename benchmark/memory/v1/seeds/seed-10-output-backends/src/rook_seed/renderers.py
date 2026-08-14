def render_html(title: str) -> str:
    return f'<h1 title="Permalink to this headline">{title}</h1>'


def render_text(title: str) -> str:
    return f"{title}\nPermalink to this headline"
