from collections.abc import Iterable, Sized
from typing import Any

from app.core.flow.context import IDX_KEY, LOOP_KEY, Context
from app.core.flow.fields import TextField
from app.core.flow.handles import DefaultHandles, InputHandle, OutputHandle
from app.core.flow.nodes.base import Node, control_node


@control_node(order=2, icon="arrowSyncCircle")
class LoopNode(Node):
    expression = TextField("expression", required=True, maxlength=1024)
    varname = TextField("varname", required=True, maxlength=32)

    class Handles(DefaultHandles):
        loop_start = OutputHandle(
            "bottom",
            maxconn=1,
            style="left: 25%; border-style: dashed;",
            tag=LOOP_KEY,
        )
        loop_continue = InputHandle(
            "bottom",
            maxconn=1,
            style="left: 75%; border-style: dashed;",
            tag=LOOP_KEY,
        )

    @classmethod
    async def execute(
        cls,
        *,
        node_id: str,
        node_data: dict[str, Any],
        input_handle: InputHandle,
        context: Context,
        **kwargs,
    ) -> OutputHandle | None:
        # extract the loop variable
        var = cls.expression.extract(node_data, context=context, raw=True)
        varname = cls.varname.extract(node_data)
        if isinstance(var, int):
            var = list(range(var))
        elif isinstance(var, dict):
            var = list(var.items())

        # start the loop
        handles = cls.Handles
        if handles.input == input_handle:
            if not (isinstance(var, Iterable) and isinstance(var, Sized)):
                raise ValueError("loop variable must be an iterable with length")
            node_data[IDX_KEY] = 0
            if len(var) > 0:
                node_data[LOOP_KEY] = {varname: var[0]}
                return handles.loop_start.with_loop(node_id)
            else:
                # skip the loop
                return handles.output

        # continue the loop
        if handles.loop_continue == input_handle:
            node_data[IDX_KEY] += 1
            if node_data[IDX_KEY] <= len(var) - 1:
                node_data[LOOP_KEY] = {varname: var[node_data[IDX_KEY]]}
                return handles.loop_start.with_loop(node_id)
            else:
                # end the loop
                return handles.output
