from tortoise.expressions import Q
from tortoise.transactions import atomic

from app.core.exceptions import ErrorCode, KaloscopeException
from app.models.network import (
    DNSResolver,
    DNSResolverUpsert,
    HTTPProxy,
    HTTPProxyUpsert,
    URLRule,
    URLRuleDNS,
    URLRuleUpsert,
)
from app.services.base import BaseService


class URLRuleService(BaseService[URLRule], model=URLRule):
    """The service class for all URL rule related operations."""

    @classmethod
    @atomic()
    async def update_priorities(cls, ids: list):
        """Update the URL rule priorities.

        Args:
            ids: The sorted URL rule IDs.
        """
        rules = await URLRule.all()
        if set(ids) != set(rule.id for rule in rules):
            raise KaloscopeException(ErrorCode.BAD_REQUEST)
        # avoid duplicate priorities
        priorities = [rule.priority for rule in rules]
        start_priority = 1 if min(priorities) > len(ids) else max(priorities) + 1
        for rule in rules:
            rule.priority = start_priority + ids.index(rule.id)
        await URLRule.bulk_update(rules, fields=["priority"])

    @classmethod
    @atomic()
    async def upsert(cls, obj: URLRuleUpsert) -> URLRule:
        """Create or update a URL rule.

        Args:
            obj: The URL rule data.

        Raises:
            KaloscopeException: If the pattern already exists.

        Returns:
            The URL rule instance.
        """
        # check if the pattern already exists
        filter = ~Q(id=obj.id) if obj.id else Q()
        if await URLRule.filter(filter & Q(pattern=obj.pattern)).count() > 0:
            raise KaloscopeException(ErrorCode.PATTERN_ALREADY_EXISTS)

        if obj.id:
            # update the URL rule
            await URLRule.filter(id=obj.id).update(
                pattern=obj.pattern,
                proxy_id=obj.proxy_id,
            )
            rule = await URLRule.get(id=obj.id)
        else:
            # create the URL rule
            priorities: list = await URLRule.all().values_list("priority", flat=True)
            rule = await URLRule.create(
                pattern=obj.pattern,
                proxy_id=obj.proxy_id,
                priority=(max(priorities) + 1 if priorities else 1),
            )

        # sync the resolver bindings
        await URLRuleDNS.filter(rule_id=rule.id).delete()
        if obj.resolver_ids:
            await URLRuleDNS.bulk_create(
                [
                    URLRuleDNS(rule_id=rule.id, resolver_id=rid)
                    for rid in obj.resolver_ids
                ]
            )

        return rule


class DNSResolverService(BaseService[DNSResolver], model=DNSResolver):
    """The service class for all DNS resolver related operations."""

    @classmethod
    async def upsert(cls, obj: DNSResolverUpsert) -> DNSResolver:
        """Create or update a DNS resolver.

        Args:
            obj: The DNS resolver data.

        Raises:
            KaloscopeException: If the name already exists.

        Returns:
            The DNS resolver instance.
        """
        # check if the name already exists
        filter = ~Q(id=obj.id) if obj.id else Q()
        if await DNSResolver.filter(filter & Q(name=obj.name)).count() > 0:
            raise KaloscopeException(ErrorCode.NAME_ALREADY_EXISTS)

        data = obj.model_dump(exclude={"id"})
        if obj.id:
            await DNSResolver.filter(id=obj.id).update(**data)
            return await DNSResolver.get(id=obj.id)
        else:
            return await DNSResolver.create(**data)


class HTTPProxyService(BaseService[HTTPProxy], model=HTTPProxy):
    """The service class for all HTTP proxy server related operations."""

    @classmethod
    async def upsert(cls, obj: HTTPProxyUpsert) -> HTTPProxy:
        """Create or update an HTTP proxy server.

        Args:
            obj: The HTTP proxy server data.

        Raises:
            KaloscopeException: If the name already exists.

        Returns:
            The HTTP proxy server instance.
        """
        # check if the name already exists
        filter = ~Q(id=obj.id) if obj.id else Q()
        if await HTTPProxy.filter(filter & Q(name=obj.name)).count() > 0:
            raise KaloscopeException(ErrorCode.NAME_ALREADY_EXISTS)

        data = obj.model_dump(exclude={"id"})
        if obj.id:
            await HTTPProxy.filter(id=obj.id).update(**data)
            return await HTTPProxy.get(id=obj.id)
        else:
            return await HTTPProxy.create(**data)
