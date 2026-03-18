from __future__ import annotations

from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Iterable
from urllib.parse import urljoin, urlparse


def collapse_ws(value: str) -> str:
    return " ".join((value or "").split())


VOID_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}


@dataclass(slots=True)
class HtmlNode:
    tag: str
    attrs: dict[str, str]
    parent: "HtmlNode | None" = None
    children: list["HtmlNode"] = field(default_factory=list)
    text_chunks: list[str] = field(default_factory=list)

    def add_child(self, node: "HtmlNode") -> None:
        self.children.append(node)

    @property
    def class_list(self) -> set[str]:
        return set(self.attrs.get("class", "").split())

    def iter_descendants(self) -> Iterable["HtmlNode"]:
        for child in self.children:
            yield child
            yield from child.iter_descendants()

    def inner_text(self) -> str:
        pieces = list(self.text_chunks)
        for child in self.children:
            child_text = child.inner_text()
            if child_text:
                pieces.append(child_text)
        return collapse_ws(" ".join(pieces))


class _TreeBuilder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = HtmlNode(tag="document", attrs={})
        self._stack: list[HtmlNode] = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = HtmlNode(
            tag=tag.lower(),
            attrs={key: value or "" for key, value in attrs},
            parent=self._stack[-1],
        )
        self._stack[-1].add_child(node)
        if node.tag not in VOID_TAGS:
            self._stack.append(node)

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.lower()
        for index in range(len(self._stack) - 1, 0, -1):
            if self._stack[index].tag == normalized:
                del self._stack[index:]
                return

    def handle_data(self, data: str) -> None:
        if data.strip():
            self._stack[-1].text_chunks.append(data)


@dataclass(slots=True)
class _SelectorToken:
    tag: str | None
    identifier: str | None
    classes: set[str]


def parse_html(text: str) -> HtmlNode:
    parser = _TreeBuilder()
    parser.feed(text)
    parser.close()
    return parser.root


def find_all(root: HtmlNode, selector: str) -> list[HtmlNode]:
    tokens = _parse_selector(selector)
    if not tokens:
        return []
    current = [root]
    for token in tokens:
        next_nodes: list[HtmlNode] = []
        for base in current:
            for node in base.iter_descendants():
                if _matches(node, token):
                    next_nodes.append(node)
        current = next_nodes
    return current


def find_first(root: HtmlNode, selector: str) -> HtmlNode | None:
    matches = find_all(root, selector)
    return matches[0] if matches else None


def extract_links(root: HtmlNode, base_url: str, selector: str | None = None, limit: int | None = None) -> list[str]:
    if selector:
        seeds = find_all(root, selector)
        anchors: list[HtmlNode] = []
        for node in seeds:
            if node.tag == "a":
                anchors.append(node)
            anchors.extend(desc for desc in node.iter_descendants() if desc.tag == "a")
    else:
        anchors = [node for node in root.iter_descendants() if node.tag == "a"]
    links: list[str] = []
    for anchor in anchors:
        href = anchor.attrs.get("href", "").strip()
        if not href or href.startswith(("#", "javascript:", "mailto:")):
            continue
        links.append(urljoin(base_url, href))
    unique = list(dict.fromkeys(links))
    return unique[:limit] if limit is not None else unique


def extract_title(root: HtmlNode) -> str:
    for selector in ("title", "h1"):
        node = find_first(root, selector)
        if node:
            text = node.inner_text()
            if text:
                return text
    return ""


def extract_meta(root: HtmlNode, attr_name: str, attr_value: str, content_name: str = "content") -> str | None:
    for node in root.iter_descendants():
        if node.tag != "meta":
            continue
        if node.attrs.get(attr_name) == attr_value and node.attrs.get(content_name):
            return node.attrs[content_name]
    return None


def extract_author(root: HtmlNode) -> str | None:
    return collapse_ws(
        extract_meta(root, "name", "author") or extract_meta(root, "property", "article:author") or ""
    ) or None


def extract_published_at(root: HtmlNode) -> str | None:
    for node in root.iter_descendants():
        if node.tag == "time" and node.attrs.get("datetime"):
            return node.attrs["datetime"]
    return (
        extract_meta(root, "property", "article:published_time")
        or extract_meta(root, "name", "pubdate")
        or extract_meta(root, "name", "date")
    )


def extract_body_text(root: HtmlNode, selector: str | None = None) -> str:
    candidates: list[HtmlNode] = []
    if selector:
        candidates = find_all(root, selector)
    else:
        for fallback in ("article", "main", "body"):
            node = find_first(root, fallback)
            if node:
                candidates = [node]
                break
    if not candidates:
        candidates = [root]
    return collapse_ws(" ".join(node.inner_text() for node in candidates if node.inner_text()))


def filter_same_host(urls: Iterable[str], base_url: str) -> list[str]:
    host = urlparse(base_url).netloc
    if not host:
        return list(urls)
    output: list[str] = []
    for url in urls:
        parsed = urlparse(url)
        if parsed.scheme == "file" or parsed.netloc == host:
            output.append(url)
    return output


def _parse_selector(selector: str) -> list[_SelectorToken]:
    tokens: list[_SelectorToken] = []
    for raw in selector.split():
        tag = None
        identifier = None
        classes: set[str] = set()
        current = ""
        mode = "tag"
        for char in raw.strip():
            if char == "#":
                if current:
                    if mode == "tag":
                        tag = current
                    elif mode == "class":
                        classes.add(current)
                current = ""
                mode = "id"
            elif char == ".":
                if current:
                    if mode == "tag":
                        tag = current
                    elif mode == "id":
                        identifier = current
                    elif mode == "class":
                        classes.add(current)
                current = ""
                mode = "class"
            else:
                current += char
        if current:
            if mode == "tag":
                tag = current
            elif mode == "id":
                identifier = current
            else:
                classes.add(current)
        tokens.append(_SelectorToken(tag=tag, identifier=identifier, classes=classes))
    return tokens


def _matches(node: HtmlNode, token: _SelectorToken) -> bool:
    if token.tag and node.tag != token.tag.lower():
        return False
    if token.identifier and node.attrs.get("id") != token.identifier:
        return False
    if token.classes and not token.classes.issubset(node.class_list):
        return False
    return True
