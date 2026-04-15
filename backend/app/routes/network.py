from sanic import Blueprint, HTTPResponse, empty, json
from sanic_ext import validate
from tortoise.expressions import Q

from app.core.decorators import authorize
from app.models.base import IDs
from app.models.network import URLRule, URLRuleQuery, URLRuleToggle, URLRuleUpsert
from app.models.user import UserRole
from app.services.network import URLRuleService

# subroutes for all network related operations
network = Blueprint("network", url_prefix="/network")


@network.get("/rule/list")
@validate(query=URLRuleQuery)
async def list_rules(_, query: URLRuleQuery) -> HTTPResponse:
    """List the URL rules."""
    queries = []
    if query.pattern:
        queries.append(Q(pattern__icontains=query.pattern))
    rules = await URLRuleService.dump_list(URLRule.filter(*queries))
    return json(rules)


@network.post("/rule/sort")
@validate(json=IDs)
async def sort_rules(_, body: IDs) -> HTTPResponse:
    """Sort the URL rules."""
    await URLRuleService.update_priorities(body.ids)
    return empty()


@network.post("/rule/upsert")
@authorize(role=UserRole.ADMIN)
@validate(json=URLRuleUpsert)
async def upsert_rule(_, body: URLRuleUpsert) -> HTTPResponse:
    """Create or update a URL rule."""
    rule = await URLRuleService.upsert(body)
    return json(await URLRuleService.dump(rule))


@network.post("/rule/delete")
@authorize(role=UserRole.ADMIN)
@validate(json=IDs)
async def delete_rules(_, body: IDs) -> HTTPResponse:
    """Delete the URL rules."""
    await URLRule.filter(id__in=body.ids).delete()
    return empty()


@network.post("/rule/toggle")
@authorize(role=UserRole.ADMIN)
@validate(json=URLRuleToggle)
async def toggle_rule(_, body: URLRuleToggle) -> HTTPResponse:
    """Toggle boolean fields of a URL rule."""
    fields = {}
    if body.secure_dns is not None:
        fields["secure_dns"] = body.secure_dns
    if body.http_proxy is not None:
        fields["http_proxy"] = body.http_proxy
    if fields:
        await URLRule.filter(id=body.id).update(**fields)
    return empty()
