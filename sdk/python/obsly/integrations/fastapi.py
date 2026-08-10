"""FastAPI integration.

    import obsly
    from obsly.integrations.fastapi import ObslyMiddleware

    obsly.init(dsn="http://<key>@localhost:8081/1", release="myapp@1.0.0")
    app.add_middleware(ObslyMiddleware)

FastAPI is Starlette, so the ASGI middleware is the whole implementation. It is re-exported
here because that is where somebody using FastAPI will look for it.

One caveat worth knowing: if the application installs its own `add_exception_handler` for
`Exception`, Starlette handles the error before it reaches any middleware, and nothing is
reported. Call `obsly.capture_exception()` inside that handler as well.
"""

from obsly.integrations.asgi import ObslyMiddleware

__all__ = ["ObslyMiddleware"]
