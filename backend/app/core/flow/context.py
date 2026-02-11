import copy
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Self

from app.core.decorators import after
from app.models.flow import FlowVariable
from app.models.general import GlobalVariable
from app.utils.crypto import xor_decrypt
from app.utils.deep import deep_update
from app.utils.dict import TrackableDict

AUTH_KEY = "auth"
"""The key to the indexer authentication in the local variables."""

START_KEY = "$start"
"""The key to the start node type in the boot parameters."""

RETVAL_KEY = "$retval"
"""The key to the execution result in the flow context."""

OUTPUT_KEY = "$output"
"""The key to the output handle in the node data."""

LOOP_KEY = "$loop"
"""The key to the loop variable in the node data."""

IDX_KEY = "$index"
"""The key to the loop index in the loop variable."""


@dataclass(init=False)
class Context:
    _context: dict[str, Any]
    # the global variables
    globalvars: Mapping[str, str]
    # the local variables
    localvars: Mapping[str, Any]
    # the boot parameters
    bootparams: Mapping[str, Any]
    # the storage dictionary
    storage: TrackableDict[str, Any]
    # the loop variable
    loopvar: Mapping[str, Any] | None = None

    @classmethod
    async def create(
        cls,
        graph_id: int,
        bootparams: Mapping[str, Any],
        storage: dict[str, Any] | None = None,
    ) -> Self:
        """Create a new context object.

        Args:
            graph_id: The flow graph ID.
            bootparams: The boot parameters.
            storage: The storage dictionary.

        Returns:
            A new context object.
        """
        # load global variables
        globalvars = await GlobalVariable.all()
        globalvars = {
            g.key: (xor_decrypt(g.value) if g.encrypted else g.value)
            for g in globalvars
        }
        # load local variables
        now = time.time()
        await FlowVariable.filter(graph_id=graph_id, expires__lt=now).delete()
        localvars = await FlowVariable.filter(graph_id=graph_id)
        localvars = {f.key: f.value for f in localvars}
        # create a new instance
        context = cls.__new__(cls)
        context.globalvars = globalvars
        context.localvars = localvars
        context.bootparams = bootparams
        context.storage = TrackableDict(storage or {})
        context.union()
        return context

    def union(self):
        """Merge the context variables into a single dictionary."""
        context = getattr(self, "_context", None)
        if context is None:
            context = {}
            context.update(self.globalvars)
            context.update(self.localvars)
            context.update(self.bootparams)
        context.update(self.storage)
        if self.loopvar is not None:
            context.update(self.loopvar)
        self._context = context

    def __getitem__(self, key: str):
        return self._context[key]

    def get(self, key: str, default: Any = None):
        return self._context.get(key, default)

    def keys(self):
        return self._context.keys()

    def values(self):
        return self._context.values()

    def items(self):
        return self._context.items()

    @after("union")
    def __setitem__(self, key: str, value: Any):
        self.storage[key] = value

    @after("union")
    def setdefault(self, key: str, default: Any):
        return self.storage.setdefault(key, default)

    @after("union")
    def update(self, other: Self | dict[str, Any]) -> Self:
        """Merge the context with another context.

        Args:
            other: The other context to merge with.

        Returns:
            The merged context.
        """
        if isinstance(other, Context):
            deep_update(self.storage, other.storage)
        else:
            deep_update(self.storage, other)
        return self

    @after("union")
    def bind_loop(self, node_data: dict[str, Any]) -> Self:
        """Bind the loop variable to the context.

        Args:
            node_data: The loop node data.

        Returns:
            The context object.
        """
        self.loopvar = node_data.get(LOOP_KEY)
        return self

    def is_modified(self) -> bool:
        """Check if the context has been modified.

        Returns:
            True if the context has been modified, False otherwise.
        """
        return self.storage.is_modified()

    def copy(self) -> Self:
        """Create a copy of the context.

        Returns:
            A copy of the context.
        """
        context = self.__class__.__new__(self.__class__)
        context.globalvars = self.globalvars
        context.localvars = self.localvars
        context.bootparams = self.bootparams
        context.storage = TrackableDict(copy.deepcopy(self.storage))
        context.union()
        return context
