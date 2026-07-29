"""
Domain stores: the app's vocabulary (jobs, todos, models, Instagram stats,
approval tokens) expressed as plain functions over dicts.

Each module here wraps a repository in web/db/ -- routers call these, never
the repositories or a SQLAlchemy session directly. That is the whole point of
the layer: the JSON-file -> Postgres migration changed everything under it
without a single router edit.
"""
