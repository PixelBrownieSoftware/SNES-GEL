import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
EDITOR = ROOT / "tools" / "visual-gel"


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = set()
        self.scripts = []
        self.stylesheets = []
        self.navigation = []
        self.in_navigation = False

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if "id" in attributes:
            self.ids.add(attributes["id"])
        if tag == "script" and "src" in attributes:
            self.scripts.append(urlsplit(attributes["src"]).path)
        if tag == "link" and attributes.get("rel") == "stylesheet":
            self.stylesheets.append(attributes.get("href"))
        if tag == "nav" and attributes.get("aria-label") == "GEL editors":
            self.in_navigation = True
        if tag == "a" and self.in_navigation:
            self.navigation.append((attributes.get("href"), attributes.get("aria-current")))

    def handle_endtag(self, tag):
        if tag == "nav":
            self.in_navigation = False


def test_project_editor_page_provides_its_controls_and_modules():
    page = PageParser()
    page.feed((EDITOR / "index.html").read_text(encoding="utf-8"))
    source = (EDITOR / "project-editor.js").read_text(encoding="utf-8")
    required_ids = set(re.findall(r'document\.getElementById\("([^"]+)"\)', source))
    assert not required_ids - page.ids

    project_index = page.scripts.index("project-editor.js")
    for dependency in ("model.js", "history.js", "editor.js", "memory-layout.js",
                       "table-editor.js", "define-editor.js", "global-editor.js"):
        assert dependency in page.scripts[:project_index]
        assert (EDITOR / dependency).is_file()


def test_all_editor_pages_have_complete_navigation():
    pages = {
        "editor": "/", "visual-gel": "/visual-gel/", "tile-editor": "/tile-editor/",
        "object-editor": "/object-editor/", "contact-editor": "/contact-editor/",
        "metasprite-editor": "/metasprite-editor/", "rom-overview": "/rom-overview/",
    }
    for folder, current in pages.items():
        page = PageParser()
        page.feed((ROOT / "tools" / folder / "index.html").read_text(encoding="utf-8"))
        assert {href for href, _ in page.navigation} == set(pages.values()), folder
        assert [(href, state) for href, state in page.navigation if state == "page"] == [(current, "page")]
        assert any(Path(href).name == "navigation.css" for href in page.stylesheets), folder
