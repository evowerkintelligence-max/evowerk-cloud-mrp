"""Pure analytics engines ported from the desktop app (evowerk-console).

These modules are deliberately dependency-light (stdlib + DuckDB SQL strings) and
contain no cloud/app imports, so the math stays identical to the desktop's
audited implementations. Routes/services wrap them with the dataset layer,
tenant scoping, and audit logging.
"""
