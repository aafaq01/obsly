"""A FastAPI app that fails in many different ways, on purpose.

Its job is to produce a realistic issue stream: several distinct bugs, one of them noisy, one
of them a chained exception, a couple of warnings, and traces with real inner spans — so the
grouping, correlation and percentile behaviour can be seen rather than described.

    uv pip install .                 # obsly and fastapi, from PyPI
    npm install                      # obsly-browser, from npm
    OBSLY_DSN="http://<key>@localhost:8081/<project>" uvicorn main:app --port 8200

    python drive.py            # generate traffic

Every endpoint here is a deliberate failure. Nothing in this file is an example of good code.
"""

import json
import logging
import os
import pathlib
import random
import time

import obsly
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from obsly.integrations.fastapi import ObslyMiddleware

obsly.init(
    release="obsly-demo@1.3.0",
    environment="production",
    traces_sample_rate=1.0,
    enable_logs=True,
)

# Forward the logging calls this app already makes. Nothing below is written specially for
# Obsly — it is ordinary logging, which is the point.
logging.basicConfig(level=logging.INFO)
logging.getLogger().addHandler(obsly.ObslyLogHandler())

log = logging.getLogger("demo")

app = FastAPI(title="Obsly demo")
app.add_middleware(ObslyMiddleware)

CART: dict[str, list[dict[str, float]]] = {"c-1": [{"price": 9.99}]}

HERE = pathlib.Path(__file__).parent

# The published package, not the one in the repository beside it. A demo that imports the SDK
# out of the monorepo proves the monorepo builds; installing from npm proves what somebody
# following the README actually receives.
SDK_DIST = HERE / "node_modules" / "obsly-browser" / "dist"

# Mounted only when it is installed, so a missing `npm install` is a page that says what to do
# rather than a stack trace on startup.
if SDK_DIST.is_dir():
    app.mount("/sdk", StaticFiles(directory=SDK_DIST), name="sdk")


@app.get("/shop", response_class=HTMLResponse)
def shop() -> str:
    """A page that reports itself to Obsly.

    Served from the same origin as the API it calls, which is what lets the browser SDK attach
    a trace header without turning every request into a preflighted one.
    """
    if not SDK_DIST.is_dir():
        return (
            "<h1>Install the browser SDK first</h1>"
            "<pre>cd examples/fastapi-demo &amp;&amp; npm install</pre>"
        )

    dsn = os.environ.get("OBSLY_DSN", "")
    page = (HERE / "shop.html").read_text(encoding="utf-8")
    # json.dumps rather than an f-string: a DSN containing a quote would otherwise close the
    # script tag early, and the public key is designed to ship in a page anyway.
    injected = f"<script>window.__OBSLY_DSN__ = {json.dumps(dsn)};</script>"
    return page.replace('<script type="module">', f'{injected}\n<script type="module">', 1)


@app.get("/")
def index() -> dict[str, str]:
    log.info("index served")
    return {"status": "ok"}


@app.get("/checkout/{cart_id}")
def checkout(cart_id: str) -> dict[str, float]:
    """Two different bugs behind one route, so grouping has to tell them apart."""
    log.info("checkout started for cart %s", cart_id)

    with obsly.start_span("db.query", "SELECT * FROM carts WHERE id = %s"):
        time.sleep(random.uniform(0.004, 0.02))
        items = CART.get(cart_id)

    if items is None:
        # KeyError, not a 404: a missing cart at this point means the caller had an id we
        # issued and then lost, which is a bug on our side.
        raise KeyError(f"cart {cart_id} vanished mid-checkout")

    with obsly.start_span("db.query", "SELECT * FROM items WHERE cart_id = %s"):
        time.sleep(random.uniform(0.002, 0.01))

    with obsly.start_span("http.client", "POST payments.example.com/charge"):
        log.info("charging card for cart %s", cart_id)
        time.sleep(random.uniform(0.05, 0.4))
        if random.random() < 0.5:
            raise ConnectionError("payments.example.com returned 502")

    total = sum(item["price"] for item in items)
    # The success path logs too. Most requests succeed, and the explanation for the ones that
    # do not usually lives in what the successful ones were doing.
    log.info("checkout complete for cart %s, total %.2f", cart_id, total)
    return {"total": total}


@app.get("/orders/{order_id}")
def get_order(order_id: int) -> dict[str, int]:
    """A chained exception — the interesting one is the outer, the cause explains it."""
    try:
        with obsly.start_span("db.query", "SELECT * FROM orders WHERE id = %s"):
            time.sleep(0.005)
            raise TimeoutError("statement timeout after 30000ms")
    except TimeoutError as cause:
        raise RuntimeError(f"could not load order {order_id}") from cause


@app.get("/report")
def report() -> dict[str, float]:
    """An N+1 in span form: many identical queries where one would do."""
    total = 0.0
    for index in range(25):
        with obsly.start_span("db.query", "SELECT total FROM orders WHERE id = %s"):
            time.sleep(0.002)
            total += index

    return {"total": total / 0}  # ZeroDivisionError


@app.get("/profile")
def profile() -> dict[str, str]:
    user: dict[str, str] = {}
    return {"email": user["email"]}  # KeyError: 'email'


@app.get("/search")
def search(q: str = "") -> dict[str, list[str]]:
    """Warnings, not errors. The request succeeds; something still deserves recording."""
    if len(q) > 50:
        obsly.capture_message(f"search query truncated from {len(q)} characters", level="warning")
        q = q[:50]

    with obsly.start_span("cache.get", "search:results"):
        log.debug("search cache miss for %r", q)
        time.sleep(0.001)

    if not q:
        obsly.capture_message("empty search query reached the index", level="warning")

    return {"results": [q] * 3}


@app.get("/legacy")
def legacy() -> dict[str, str]:
    """A handled failure worth reporting even though the caller gets a clean 503."""
    try:
        raise ValueError("legacy pricing table is missing column 'currency'")
    except ValueError as exc:
        obsly.capture_exception(exc)
        raise HTTPException(status_code=503, detail="pricing unavailable") from exc


@app.get("/slow")
def slow() -> dict[str, str]:
    """Succeeds, but slowly — so the percentiles have a visible tail to show."""
    with obsly.start_span("db.query", "SELECT * FROM analytics_rollup"):
        log.warning("analytics rollup is being computed on the request path")
        time.sleep(random.uniform(0.3, 1.2))
    return {"status": "eventually"}
