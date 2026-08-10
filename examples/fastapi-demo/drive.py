"""Generate traffic against the demo app.

Weighted so the stream looks like a real one: a couple of loud issues, several quiet ones, and
far more successful requests than failures. A stream where every issue has the same count
tells you nothing about triage.
"""

import random
import sys
import urllib.error
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8200"
ROUNDS = int(sys.argv[2]) if len(sys.argv) > 2 else 60

# (path, weight)
ROUTES: list[tuple[str, int]] = [
    ("/", 10),
    ("/checkout/c-1", 8),
    ("/checkout/c-missing", 5),
    ("/profile", 4),
    ("/orders/42", 3),
    ("/search?q=shoes", 4),
    ("/search?q=", 2),
    ("/search?q=" + "x" * 80, 2),
    ("/report", 2),
    ("/legacy", 2),
    ("/slow", 2),
]


def main() -> None:
    paths = [path for path, weight in ROUTES for _ in range(weight)]
    counts: dict[int, int] = {}

    for _ in range(ROUNDS):
        path = random.choice(paths)
        try:
            with urllib.request.urlopen(f"{BASE}{path}", timeout=10) as response:  # noqa: S310
                status = response.status
        except urllib.error.HTTPError as exc:
            status = exc.code
        except Exception:  # noqa: BLE001 - a 500 arrives as a connection reset under uvicorn
            status = 500
        counts[status] = counts.get(status, 0) + 1

    print(f"sent {ROUNDS} requests: " + ", ".join(f"{k} x{v}" for k, v in sorted(counts.items())))


if __name__ == "__main__":
    main()
