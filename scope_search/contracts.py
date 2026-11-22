"""API and workspace import contracts."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Slug = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9-]{0,59}$")]
Label = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=140)]
QueryText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Principal(Contract):
    tenant_id: Slug
    tenant_name: Label
    handle: Slug
    name: Label
    role: Literal["member", "admin"]


class AccountSpec(Contract):
    handle: Slug
    name: Label
    role: Literal["member", "admin"] = "member"
    groups: list[Slug] = Field(default_factory=list, max_length=32)


class GroupSpec(Contract):
    slug: Slug
    name: Label


class DocumentSpec(Contract):
    slug: Slug
    title: Label
    collection: Label
    group_slug: Slug | None = None
    body: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=30_000)]


class TenantSpec(Contract):
    tenant_id: Slug
    name: Label
    users: list[AccountSpec] = Field(min_length=1, max_length=100)
    groups: list[GroupSpec] = Field(default_factory=list, max_length=32)
    documents: list[DocumentSpec] = Field(default_factory=list, max_length=200)

    @model_validator(mode="after")
    def check_references(self):
        groups = {group.slug for group in self.groups}
        if len(groups) != len(self.groups) or len({user.handle for user in self.users}) != len(self.users):
            raise ValueError("Group slugs and user handles must be unique within a workspace.")
        if len({item.slug for item in self.documents}) != len(self.documents):
            raise ValueError("Document slugs must be unique within a workspace.")
        if any(set(user.groups) - groups or len(set(user.groups)) != len(user.groups) for user in self.users):
            raise ValueError("Each membership must reference a known group and appear once.")
        if any(item.group_slug and item.group_slug not in groups for item in self.documents):
            raise ValueError("Document restrictions must reference existing groups.")
        if not any(user.role == "admin" for user in self.users):
            raise ValueError("At least one administrator is required.")
        return self


class PublicDocument(Contract):
    slug: Slug
    title: Label
    collection: Label
    group_slug: Slug | None
    body: str
    updated_at: datetime


class Hit(Contract):
    slug: Slug
    title: Label
    collection: Label
    group_slug: Slug | None
    excerpt: str
    updated_at: datetime


class SearchResult(Contract):
    query: QueryText
    cache: Literal["hit", "miss"]
    results: list[Hit]


class AnswerInput(Contract):
    query: QueryText


class Answer(Contract):
    mode: Literal["extractive", "local-model", "no-evidence"]
    citations: list[Hit]


class ChangeAccess(Contract):
    enabled: bool = Field(strict=True)


class DemoSignIn(Contract):
    actor: Slug
