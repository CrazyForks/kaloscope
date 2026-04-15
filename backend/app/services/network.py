from tortoise.expressions import Q
from tortoise.transactions import atomic

from app.core.exceptions import ErrorCode, KaloscopeException
from app.models.network import URLRule, URLRuleDNS, URLRuleUpsert
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
        rules = await URLRule.filter(id__in=ids)
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
            raise KaloscopeException(ErrorCode.NAME_ALREADY_EXISTS)

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
