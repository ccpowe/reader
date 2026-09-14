"""Safe, bounded subset of Crawl4AI's native JSON/CSS extraction schema.

The schema is passed unchanged to JsonCssExtractionStrategy. Reader supplies
only an action interpreter and resource limits; agents never supply code.
"""

from __future__ import annotations

from typing import Literal

import soupsieve
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NativeField(StrictModel):
    name: str = Field(
        min_length=1,
        max_length=60,
        description=(
            "Output key. Reader consumes top-level url, title, published_at, author, "
            "excerpt or summary, and image_url from listing recipes. Legacy article recipes "
            "remain parseable but are not executed. Use published_at for dates; "
            "date is not consumed. "
            "Nested helper fields may use other names."
        ),
    )
    type: Literal["text", "html", "attribute", "nested", "nested_list"]
    selector: str | None = Field(default=None, min_length=1, max_length=400)
    attribute: str | None = Field(default=None, min_length=1, max_length=80)
    fields: list[NativeField] | None = Field(default=None, max_length=12)

    @field_validator("selector")
    @classmethod
    def css_selector(cls, value: str | None) -> str | None:
        if value is not None:
            try:
                soupsieve.compile(value)
            except soupsieve.SelectorSyntaxError as exc:
                raise ValueError(str(exc)) from exc
        return value

    @model_validator(mode="after")
    def field_shape(self):
        if self.type == "attribute" and not self.attribute:
            raise ValueError("attribute fields require attribute")
        if self.type in {"nested", "nested_list"}:
            if not self.fields or not self.selector:
                raise ValueError("nested fields require selector and fields")
        elif self.fields:
            raise ValueError("only nested fields may contain fields")
        return self


class NativeSchema(StrictModel):
    name: str = Field(default="Reader listing", min_length=1, max_length=80)
    baseSelector: str = Field(min_length=1, max_length=400)
    baseFields: list[NativeField] = Field(default_factory=list, max_length=12)
    fields: list[NativeField] = Field(default_factory=list, max_length=12)

    @field_validator("baseSelector")
    @classmethod
    def css_selector(cls, value: str) -> str:
        try:
            soupsieve.compile(value)
        except soupsieve.SelectorSyntaxError as exc:
            raise ValueError(str(exc)) from exc
        return value

    @model_validator(mode="after")
    def bound_shape(self):
        def visit(fields, depth=0):
            if depth > 3:
                raise ValueError("native schema nesting exceeds 3 levels")
            names = [field.name for field in fields]
            if len(set(names)) != len(names):
                raise ValueError("native field names must be unique")
            return len(fields) + sum(visit(f.fields or [], depth + 1) for f in fields)

        if visit(self.baseFields + self.fields) > 40:
            raise ValueError("native schema exceeds 40 fields")
        return self


class BrowserAction(StrictModel):
    kind: Literal["click", "wait", "scroll"]
    selector: str | None = Field(default=None, min_length=1, max_length=400)
    timeout_ms: int = Field(default=4000, ge=100, le=8000)
    min_count: int = Field(default=1, ge=1, le=500)
    steps: int = Field(default=1, ge=1, le=8)

    @model_validator(mode="after")
    def require_selector(self):
        if self.kind != "scroll" and not self.selector:
            raise ValueError("click and wait actions require a CSS selector")
        if self.selector:
            try:
                soupsieve.compile(self.selector)
            except soupsieve.SelectorSyntaxError as exc:
                raise ValueError(str(exc)) from exc
        return self


class PageRecipe(StrictModel):
    extraction: NativeSchema
    render_js: bool = False
    actions: list[BrowserAction] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def actions_need_browser(self):
        if self.actions:
            self.render_js = True
        return self


INSPECTION_RECIPE = {
    "extraction": {
        "name": "Page inspection links",
        "baseSelector": "a[href]",
        "fields": [
            {"name": "url", "type": "attribute", "attribute": "href"},
            {"name": "title", "type": "text"},
        ],
    },
}


class WebRule(StrictModel):
    """A source-bound native Crawl4AI recipe; no site-specific compatibility fields."""

    id: str = Field(min_length=1, max_length=80)
    hosts: list[str] = Field(min_length=1, max_length=8)
    index_paths: list[str] = Field(min_length=1, max_length=20)
    listing: PageRecipe
    article: PageRecipe | None = Field(
        default=None,
        description="Legacy rule compatibility only; Web scans do not execute article recipes.",
    )
    next_page_selector: str | None = Field(
        default=None,
        min_length=1,
        max_length=400,
        description=(
            "CSS selector for an observed next-page link with href. Omit if not observed. "
            "Reused on every listing page; the first match in DOM order wins, including "
            "across comma alternatives. Scope a source-only entry alternative with observed "
            "attributes or parent structure so its href cannot also select later pager links. "
            "JavaScript load-more controls require bounded listing.actions instead. "
            "Reading one listing in multiple batches is not website pagination."
        ),
    )
    date_formats: list[str] = Field(
        default_factory=list,
        max_length=6,
        description=(
            "strptime formats for published_at when not ISO/RFC dates. Prefer an observed "
            "time[datetime] attribute when available. Formats cannot rename a date field."
        ),
    )

    @field_validator("hosts")
    @classmethod
    def normalise_hosts(cls, value: list[str]) -> list[str]:
        value = [host.rstrip(".").lower() for host in value]
        if any(not host or len(host) > 253 or ":" in host or "/" in host for host in value):
            raise ValueError("hosts must be bare hostnames")
        return value

    @field_validator("index_paths")
    @classmethod
    def validate_paths(cls, value: list[str]) -> list[str]:
        if any(not path.startswith("/") or "://" in path or len(path) > 2000 for path in value):
            raise ValueError("index_paths must be absolute URL paths")
        return value

    @field_validator("date_formats")
    @classmethod
    def bound_formats(cls, value: list[str]) -> list[str]:
        if any(not item or len(item) > 100 for item in value):
            raise ValueError("date formats must be non-empty and at most 100 characters")
        return value

    @model_validator(mode="after")
    def listing_contract(self):
        fields = self.listing.extraction.fields + self.listing.extraction.baseFields
        names = {field.name: field for field in fields}
        if not {"url", "title"} <= names.keys():
            raise ValueError("listing native schema requires url and title fields")
        if names["url"].type != "attribute" or names["title"].type != "text":
            raise ValueError("listing url must be attribute and title must be text")
        if self.next_page_selector:
            try:
                soupsieve.compile(self.next_page_selector)
            except soupsieve.SelectorSyntaxError as exc:
                raise ValueError(str(exc)) from exc
        return self
