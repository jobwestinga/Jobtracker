"""Native presentation preferences shared by owned widgets."""


def reduced_motion(widget) -> bool:
    """The window owns the device-local preference; widgets only read it."""
    return bool(widget.window().property("_jt_reduce_motion"))
