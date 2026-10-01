"""Native Hermes plugin entry point."""


def register(ctx) -> None:
    from .tools import register_tools
    from .adapter import register as register_platform

    register_tools(ctx)
    register_platform(ctx)
