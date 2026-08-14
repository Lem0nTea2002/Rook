"""Harbor benchmark adapters for Rook.

Harbor is an optional, external benchmark runtime.  Keeping its adapters in
``benchmark.harbor`` prevents Harbor's dependency and container lifecycle from
leaking into Rook's core runtime.
"""
