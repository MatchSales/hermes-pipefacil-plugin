"""Native Hermes plugin entry point."""

__version__ = "0.4.4"


def register(ctx) -> None:
    from .tools import register_tools
    from .adapter import register as register_platform
    from .policy import pre_tool_call

    register_tools(ctx)
    register_platform(ctx)
    ctx.register_hook("pre_tool_call", pre_tool_call)
