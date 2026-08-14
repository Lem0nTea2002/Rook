from textual.widgets import MarkdownViewer

from rook_agent.app.viewer import ContentViewerScreen, summarize_diff


def test_diff_summary_reports_files_and_line_counts() -> None:
    content = "\n".join(
        [
            "diff --git a/a.py b/a.py",
            "--- a/a.py",
            "+++ b/a.py",
            "-old",
            "+new",
            "+extra",
        ]
    )

    summary = summarize_diff(content)

    assert summary.files == ("a.py",)
    assert summary.additions == 2
    assert summary.deletions == 1


def test_content_viewer_uses_read_only_virtualized_text_area() -> None:
    screen = ContentViewerScreen(
        title="Diff",
        content="diff --git a/a b/a",
        kind="diff",
    )

    widgets = list(screen.compose())
    text_area = next(widget for widget in widgets if widget.id == "viewer-content")

    assert text_area.read_only is True
    assert text_area.show_line_numbers is True


def test_help_viewer_renders_grouped_markdown_without_external_links() -> None:
    screen = ContentViewerScreen(
        title="ROOK // COMMAND DECK",
        content="# Help\n\n## 会话\n\n`/new`",
        kind="help",
    )

    widgets = list(screen.compose())
    viewer = next(widget for widget in widgets if widget.id == "help-content")

    assert isinstance(viewer, MarkdownViewer)
    assert viewer.show_table_of_contents is False
