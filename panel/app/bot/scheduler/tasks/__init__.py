"""Scheduled tasks for the Onnix SA bot.

Each task module exposes a module-level factory function that the
APScheduler job runner invokes.  Tasks are self-contained: they
create their own DB sessions and perform best-effort notifications.
"""
