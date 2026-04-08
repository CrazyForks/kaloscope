from typing import Any

from app.core.flow.context import Context
from app.core.flow.fields.code import CodeField
from app.core.flow.handles import OutputHandle
from app.core.flow.nodes.base import Node, start_node
from app.core.renderer import render
from app.models.flow import GraphCategory
from app.utils.json import try_loads


@start_node(order=1, categories=(GraphCategory.SCHEDULE,))
class ScheduleStartNode(Node):
    bootparams = CodeField(
        "bootparams",
        language="jinja2",
        darkmode=True,
        default="{{ params|tojson }}",
    )

    class Handles:
        output = OutputHandle()

    @classmethod
    async def execute(cls, *, node_data: dict[str, Any], context: Context, **kwargs):
        bootparams = cls.bootparams.extract(node_data)
        if not bootparams:
            return

        # merge the rendered bootparams into context
        rendered = render(
            bootparams, {**context._context, "params": context.bootparams}
        )
        if rendered and isinstance((obj := try_loads(rendered)), dict):
            context.update(obj)
