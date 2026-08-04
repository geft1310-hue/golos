#!/usr/bin/env python3
"""Локальная проверка витрины для людей, поисковиков и ИИ-помощников.

Сеть намеренно не используется: чужой сайт не должен делать наш CI красным. Здесь
проверяются только факты, которые контролирует этот репозиторий — HTML, метаданные,
JSON-LD, ссылки на свои файлы, Sitemap, llms.txt и социальная карточка.
"""

from __future__ import annotations

import json
import re
import struct
import sys
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
BASE_URL = "https://geft1310-hue.github.io/golos/"
PAGES = {
    DOCS / "index.html": {
        "lang": "ru",
        "canonical": BASE_URL,
        "self_href": BASE_URL,
    },
    DOCS / "en" / "index.html": {
        "lang": "en",
        "canonical": f"{BASE_URL}en/",
        "self_href": f"{BASE_URL}en/",
    },
}
EXPECTED_LANGUAGES = {
    "ru": BASE_URL,
    "en": f"{BASE_URL}en/",
    "x-default": f"{BASE_URL}en/",
}
INDEXNOW_RE = re.compile(r"^[A-Za-z0-9-]{8,128}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SKIP_SCHEMES = {"http", "https", "mailto", "tel", "data", "javascript"}


class SiteParser(HTMLParser):
    """Собирает только необходимые для проверки части HTML."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.html_lang = ""
        self.h1_count = 0
        self.title_parts: list[str] = []
        self.description = ""
        self.robots = ""
        self.canonical = ""
        self.hreflang: dict[str, str] = {}
        self.meta_properties: dict[str, str] = {}
        self.meta_names: dict[str, str] = {}
        self.relative_targets: list[str] = []
        self.json_ld_parts: list[list[str]] = []
        self._in_title = False
        self._in_json_ld = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        data = {key: value or "" for key, value in attrs}
        if tag == "html":
            self.html_lang = data.get("lang", "")
        elif tag == "h1":
            self.h1_count += 1
        elif tag == "title":
            self._in_title = True
        elif tag == "meta":
            name = data.get("name", "").lower()
            prop = data.get("property", "").lower()
            content = data.get("content", "")
            if name:
                self.meta_names[name] = content
            if prop:
                self.meta_properties[prop] = content
            if name == "description":
                self.description = content
            elif name == "robots":
                self.robots = content
        elif tag == "link":
            rel = {part.lower() for part in data.get("rel", "").split()}
            href = data.get("href", "")
            if "canonical" in rel:
                self.canonical = href
            if "alternate" in rel and data.get("hreflang"):
                self.hreflang[data["hreflang"]] = href
            self._remember_relative(href)
        elif tag in {"a", "img", "script"}:
            attribute = "src" if tag in {"img", "script"} else "href"
            self._remember_relative(data.get(attribute, ""))
            if tag == "script" and data.get("type") == "application/ld+json":
                self._in_json_ld = True
                self.json_ld_parts.append([])

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        elif tag == "script" and self._in_json_ld:
            self._in_json_ld = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title_parts.append(data)
        if self._in_json_ld:
            self.json_ld_parts[-1].append(data)

    @property
    def title(self) -> str:
        return "".join(self.title_parts).strip()

    @property
    def json_ld(self) -> list[str]:
        return ["".join(parts).strip() for parts in self.json_ld_parts]

    def _remember_relative(self, target: str) -> None:
        if not target or target.startswith(("#", "/")):
            return
        if urlparse(target).scheme.lower() not in SKIP_SCHEMES:
            self.relative_targets.append(target)


def check(condition: bool, message: str, errors: list[str]) -> None:
    if not condition:
        errors.append(message)


def resolve_target(page: Path, target: str) -> Path:
    clean_target = target.split("#", 1)[0].split("?", 1)[0]
    candidate = (page.parent / clean_target).resolve()
    if clean_target.endswith("/") or candidate.is_dir():
        candidate /= "index.html"
    return candidate


def schema_types(document: object) -> set[str]:
    if not isinstance(document, dict):
        return set()
    nodes = document.get("@graph", [document])
    if not isinstance(nodes, list):
        return set()
    result: set[str] = set()
    for node in nodes:
        if not isinstance(node, dict):
            continue
        item = node.get("@type")
        if isinstance(item, str):
            result.add(item)
        elif isinstance(item, list):
            result.update(value for value in item if isinstance(value, str))
    return result


def check_pages(errors: list[str]) -> None:
    required_schema = {"SoftwareApplication", "WebSite", "BreadcrumbList", "HowTo", "FAQPage"}
    for page, expected in PAGES.items():
        label = page.relative_to(ROOT)
        check(page.is_file(), f"{label}: файл отсутствует", errors)
        if not page.is_file():
            continue

        parser = SiteParser()
        parser.feed(page.read_text(encoding="utf-8"))
        check(parser.html_lang == expected["lang"], f"{label}: неверный lang", errors)
        check(parser.h1_count == 1, f"{label}: должен быть ровно один h1", errors)
        check(15 <= len(parser.title) <= 70, f"{label}: title пустой или неразумной длины", errors)
        check(
            70 <= len(parser.description) <= 180,
            f"{label}: meta description пустой или неразумной длины",
            errors,
        )
        check(parser.canonical == expected["canonical"], f"{label}: неверный canonical", errors)
        check(
            parser.hreflang == EXPECTED_LANGUAGES,
            f"{label}: неполный или неверный hreflang",
            errors,
        )
        check(
            "index" in parser.robots and "max-snippet:-1" in parser.robots,
            f"{label}: robots meta ограничивает индексирование",
            errors,
        )

        for key in ("og:title", "og:description", "og:url", "og:image", "og:image:alt"):
            check(bool(parser.meta_properties.get(key)), f"{label}: нет {key}", errors)
        for key in ("twitter:card", "twitter:title", "twitter:description", "twitter:image"):
            check(bool(parser.meta_names.get(key)), f"{label}: нет {key}", errors)
        check(
            parser.meta_properties.get("og:url") == expected["canonical"],
            f"{label}: og:url не равен canonical",
            errors,
        )
        check(
            parser.meta_properties.get("og:image") == f"{BASE_URL}og.png",
            f"{label}: неверная OG-картинка",
            errors,
        )

        found_schema: set[str] = set()
        check(bool(parser.json_ld), f"{label}: нет JSON-LD", errors)
        for number, text in enumerate(parser.json_ld, start=1):
            try:
                document = json.loads(text)
            except json.JSONDecodeError as exc:
                errors.append(f"{label}: JSON-LD #{number} не разбирается: {exc}")
                continue
            found_schema.update(schema_types(document))
        missing_schema = required_schema - found_schema
        check(not missing_schema, f"{label}: нет schema типов {sorted(missing_schema)}", errors)

        for target in parser.relative_targets:
            resolved = resolve_target(page, target)
            check(
                resolved.is_file(),
                f"{label}: внутренняя ссылка {target!r} ведёт в отсутствующий файл",
                errors,
            )


def check_sitemap(errors: list[str]) -> None:
    sitemap = DOCS / "sitemap.xml"
    check(sitemap.is_file(), "docs/sitemap.xml отсутствует", errors)
    if not sitemap.is_file():
        return
    try:
        root = ET.parse(sitemap).getroot()
    except ET.ParseError as exc:
        errors.append(f"docs/sitemap.xml: неверный XML: {exc}")
        return

    ns = {
        "s": "http://www.sitemaps.org/schemas/sitemap/0.9",
        "xhtml": "http://www.w3.org/1999/xhtml",
    }
    urls = root.findall("s:url", ns)
    locations = {url.findtext("s:loc", default="", namespaces=ns) for url in urls}
    check(
        locations == {BASE_URL, f"{BASE_URL}en/"},
        "sitemap должен содержать только две canonical-страницы",
        errors,
    )
    for url in urls:
        location = url.findtext("s:loc", default="", namespaces=ns)
        lastmod = url.findtext("s:lastmod", default="", namespaces=ns)
        check(bool(DATE_RE.fullmatch(lastmod)), f"sitemap: неверный lastmod для {location}", errors)
        alternates = {
            link.attrib.get("hreflang", ""): link.attrib.get("href", "")
            for link in url.findall("xhtml:link", ns)
        }
        check(
            alternates == EXPECTED_LANGUAGES, f"sitemap: неверные hreflang для {location}", errors
        )


def check_llms(errors: list[str]) -> None:
    llms = DOCS / "llms.txt"
    check(llms.is_file(), "docs/llms.txt отсутствует", errors)
    if not llms.is_file():
        return
    text = llms.read_text(encoding="utf-8")
    lines = text.splitlines()
    check(
        bool(lines) and lines[0] == "# Golos",
        "llms.txt должен начинаться с единственного H1 Golos",
        errors,
    )
    check(
        sum(line.startswith("# ") for line in lines) == 1, "llms.txt должен иметь один H1", errors
    )
    check(
        any(line.startswith("> ") for line in lines),
        "llms.txt не содержит краткого blockquote",
        errors,
    )
    check("## Primary documentation" in text, "llms.txt не содержит Primary documentation", errors)
    check("## Optional" in text, "llms.txt не содержит Optional", errors)
    check(
        "[English website](https://geft1310-hue.github.io/golos/en/)" in text,
        "llms.txt не ссылается на английскую canonical-страницу",
        errors,
    )
    for name in ("llms-full.txt", "overview.ru.md", "overview.en.md"):
        check((DOCS / name).is_file(), f"docs/{name} отсутствует", errors)


def png_dimensions(path: Path) -> tuple[int, int]:
    with path.open("rb") as stream:
        signature = stream.read(24)
    if len(signature) < 24 or signature[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("не PNG")
    return struct.unpack(">II", signature[16:24])


def check_assets(errors: list[str]) -> None:
    og_image = DOCS / "og.png"
    check(og_image.is_file(), "docs/og.png отсутствует", errors)
    if og_image.is_file():
        try:
            dimensions = png_dimensions(og_image)
        except (OSError, ValueError) as exc:
            errors.append(f"docs/og.png: {exc}")
        else:
            check(
                dimensions == (1200, 630),
                f"docs/og.png: размер {dimensions}, нужен 1200x630",
                errors,
            )
    check((DOCS / "favicon.svg").is_file(), "docs/favicon.svg отсутствует", errors)
    check((DOCS / ".nojekyll").is_file(), "docs/.nojekyll отсутствует", errors)
    check((DOCS / "404.html").is_file(), "docs/404.html отсутствует", errors)

    key_files = [
        path
        for path in DOCS.glob("*.txt")
        if INDEXNOW_RE.fullmatch(path.stem)
        and path.read_text(encoding="utf-8").strip() == path.stem
    ]
    check(len(key_files) == 1, "должен быть ровно один IndexNow key-файл", errors)


def check_metadata(errors: list[str]) -> None:
    citation = ROOT / "CITATION.cff"
    codemeta = ROOT / "codemeta.json"
    check(citation.is_file(), "CITATION.cff отсутствует", errors)
    check(codemeta.is_file(), "codemeta.json отсутствует", errors)
    if citation.is_file():
        text = citation.read_text(encoding="utf-8")
        for marker in ("cff-version: 1.2.0", "version: 0.1.0", "license: MIT"):
            check(marker in text, f"CITATION.cff: нет {marker}", errors)
    if codemeta.is_file():
        try:
            data = json.loads(codemeta.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            errors.append(f"codemeta.json не разбирается: {exc}")
        else:
            check(
                data.get("@type") == "SoftwareSourceCode", "codemeta.json: неверный @type", errors
            )
            check(data.get("version") == "0.1.0", "codemeta.json: неверная версия", errors)
            check(
                data.get("license") == "https://spdx.org/licenses/MIT",
                "codemeta.json: неверная лицензия",
                errors,
            )


def main() -> int:
    errors: list[str] = []
    check_pages(errors)
    check_sitemap(errors)
    check_llms(errors)
    check_assets(errors)
    check_metadata(errors)

    if errors:
        print("Витрина не прошла проверку:")
        for error in errors:
            print(f"- {error}")
        return 1

    print("витрина: HTML, JSON-LD, hreflang, Sitemap, llms.txt и файлы на месте")
    return 0


if __name__ == "__main__":
    sys.exit(main())
