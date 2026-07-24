Flask applications can register many blueprints, but there is no supported way
to ask an application which blueprint contributed a given endpoint.

Implement this behavior on the application object:

- A new method `endpoint_origins()` that returns a dict mapping every registered
  endpoint name to the dotted name of the blueprint that registered it, or
  `None` for endpoints that were registered directly on the application.
- Endpoints contributed by a nested blueprint use the full dotted blueprint
  path (for example `parent.child`), matching the endpoint naming already used
  for `url_for`.
- The mapping reflects the current registration state: it is empty on a fresh
  application, and registering the same blueprint twice under different names
  produces one entry per resulting endpoint.
- A new configuration key `ENDPOINT_ORIGIN_STRICT`, defaulting to `False`. When
  it is `True`, registering a URL rule for an endpoint name that is already
  registered by a different blueprint raises a `ValueError` whose message names
  both blueprints, instead of the current behavior.

Keep all existing public behavior unchanged, and do not modify the existing
tests.
