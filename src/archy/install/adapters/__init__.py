"""Per-client agent adapters for `archy install`.

One module per client. The registry in :mod:`archy.install.registry` is the
only place that imports these; everything else goes through the registry so a
new client is a single new module plus one registry line.
"""
