from dataclasses import dataclass, field


@dataclass(frozen=True)
class Source:
    """The edge source data wrapper."""

    node_id: str
    handle_id: str = field(kw_only=True)


@dataclass(frozen=True)
class Target:
    """The edge target data wrapper."""

    node_id: str
    handle_id: str = field(kw_only=True)
