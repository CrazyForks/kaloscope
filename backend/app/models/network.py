from enum import StrEnum, auto

from pydantic import BaseModel, Field, PositiveInt
from tortoise.fields import (
    BooleanField,
    CharEnumField,
    CharField,
    ForeignKeyField,
    ForeignKeyNullableRelation,
    ForeignKeyRelation,
    IntField,
    ReverseRelation,
)

from app.models.base import Pageable, TortoiseModel


# -------------------- Enumerations --------------------
class ProxyProtocol(StrEnum):
    HTTP = auto()
    SOCKS5 = auto()


class DNSProtocol(StrEnum):
    TLS = auto()  # DNS over TLS
    HTTPS = auto()  # DNS over HTTPS


# -------------------- ORM Models --------------------
class HTTPProxy(TortoiseModel):
    name = CharField(max_length=64, unique=True)
    protocol = CharEnumField(max_length=16, enum_type=ProxyProtocol)
    host = CharField(max_length=255)
    port = IntField()
    username = CharField(max_length=64, null=True)
    password = CharField(max_length=64, null=True)
    # relational fields
    rules: ReverseRelation["URLRule"]

    class Meta:
        table = "http_proxy"
        ordering = ["name"]

    class PydanticMeta:
        exclude = ("password", "rules")


class DNSResolver(TortoiseModel):
    name = CharField(max_length=64, unique=True)
    protocol = CharEnumField(max_length=16, enum_type=DNSProtocol)
    nameserver = CharField(max_length=255)
    dnssec = BooleanField(default=False)
    # relational fields
    rules: ReverseRelation["URLRuleDNS"]

    class Meta:
        table = "dns_resolver"
        ordering = ["name"]

    class PydanticMeta:
        exclude = ("rules",)


class URLRule(TortoiseModel):
    pattern = CharField(max_length=255, unique=True)
    secure_dns = BooleanField(default=True)
    http_proxy = BooleanField(default=True)
    priority = IntField(unique=True)
    proxy_id: int | None
    proxy: ForeignKeyNullableRelation[HTTPProxy] = ForeignKeyField(
        "models.HTTPProxy", related_name="rules", db_index=True, null=True
    )
    # relational fields
    resolvers: ReverseRelation["URLRuleDNS"]

    class Meta:
        table = "url_rule"
        ordering = ["priority"]


class URLRuleDNS(TortoiseModel):
    rule_id: int
    rule: ForeignKeyRelation[URLRule] = ForeignKeyField(
        "models.URLRule", related_name="resolvers", db_index=True
    )
    resolver_id: int
    resolver: ForeignKeyRelation[DNSResolver] = ForeignKeyField(
        "models.DNSResolver", related_name="rules", db_index=True
    )

    class Meta:
        table = "url_rule_dns"
        unique_together = (("rule", "resolver"),)

    class PydanticMeta:
        exclude = ("rule", "resolver")


# -------------------- Pydantic Models --------------------
class HTTPProxyQuery(Pageable):
    name: str | None = None


class HTTPProxyUpsert(BaseModel):
    id: PositiveInt | None = None
    name: str = Field(min_length=1, max_length=64)
    protocol: ProxyProtocol
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(ge=1, le=65535)
    username: str | None = Field(min_length=1, max_length=64, default=None)
    password: str | None = Field(min_length=1, max_length=64, default=None)


class DNSResolverQuery(Pageable):
    name: str | None = None


class DNSResolverUpsert(BaseModel):
    id: PositiveInt | None = None
    name: str = Field(min_length=1, max_length=64)
    protocol: DNSProtocol
    nameserver: str = Field(min_length=1, max_length=255)
    dnssec: bool = False


class URLRuleQuery(Pageable):
    pattern: str | None = None


class URLRuleUpsert(BaseModel):
    id: PositiveInt | None = None
    pattern: str = Field(min_length=1, max_length=255)
    proxy_id: PositiveInt | None = None
    resolver_ids: list[PositiveInt] = Field(max_length=99, default_factory=list)


class URLRuleToggle(BaseModel):
    id: PositiveInt
    secure_dns: bool | None = None
    http_proxy: bool | None = None
