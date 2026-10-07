"""Native Hermes plugin entry point."""

__version__ = "0.5.0"


def register(ctx) -> None:
    from .tools import register_tools
    from .adapter import register as register_platform
    from .policy import pre_tool_call
    from .public import register as register_public

    register_tools(ctx)
    register_platform(ctx)
    register_public(ctx)
    ctx.register_hook("pre_tool_call", pre_tool_call)
