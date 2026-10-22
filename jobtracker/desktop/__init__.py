"""Qt-free desktop engine host for the native Mac frontend.

The native interface talks to one Python engine process over private pipes.
The engine owns the store, timer, recovery, scheduling and sync worker; it calls
the existing ``TrackerService``/``SyncedTrackerService`` facade and never adds a
second rule or database owner. Nothing here is imported by the Qt app or server.

Import order matters: :mod:`.bootstrap` and :mod:`.ownership` must run before
anything imports ``jobtracker.core.database`` (whose eager singleton opens the
configured store). The package initializer therefore imports nothing.
"""
