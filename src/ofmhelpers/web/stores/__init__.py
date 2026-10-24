"""
Domain stores: the app's vocabulary (jobs, users, devices, tracked accounts,
short links, Drive folders, ...) as plain functions returning the Pydantic
models of web/schemas/.

Each module here wraps a repository in web/db/repositories -- routers and the
web/ services call these, never the repositories or a SQLAlchemy session
directly. That is the whole point of the layer: the JSON-file -> Postgres
migration changed everything under it without a single router edit.
"""
