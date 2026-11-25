"""Load test: hundreds of users opening the dashboard at once (Sprint 21).

    python deploy/load_test.py --url http://127.0.0.1:8000 --admin USER --password PASS \\
        --users 300 --concurrency 50 --rounds 3

It creates (once) `--users` accounts through the API: national admins, department admins,
referents and readers spread over the departments in the data, then each simulated user
loads the dashboard as the browser does (account, departments, schools, statistics,
the sectors of their department, actions, one school page). It prints the latency of each
request type and of the whole dashboard load (requests in parallel, as the page does).

Run it against a test deployment, never production: it creates accounts. The server must
allow the logins (SRT_RATE_LIMIT_LOGIN, SRT_RATE_LIMIT_DEFAULT high enough).
"""
from __future__ import annotations

import argparse
import asyncio
import random
import statistics
import time
from collections import defaultdict

import httpx

API = "/api/v1"
PASSWORD = "load-test-password-not-secret"


def _percentile(values: list[float], p: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(p / 100 * (len(ordered) - 1))))]


async def _create_users(client: httpx.AsyncClient, n: int) -> list[dict]:
    territories = (await client.get(f"{API}/territoires")).json()
    codes = [t["code"] for t in territories]
    schools = (await client.get(f"{API}/colleges")).json()
    by_dep = defaultdict(list)
    for s in schools:
        by_dep[s["departement"]].append(s["uai"])
    existing = {u["username"] for u in (await client.get(f"{API}/auth/users")).json()}
    users = []
    for i in range(n):
        kind = ("admin-national", "admin-dep", "referent", "lecteur")[i % 4] if i >= 4 else "admin-national"
        dep = codes[i % len(codes)]
        body = {"username": f"load-{kind}-{i:04d}", "password": PASSWORD}
        if kind == "admin-national":
            body |= {"role": "admin"}
        elif kind == "admin-dep":
            body |= {"role": "admin", "departements": [dep]}
        elif kind == "referent":
            body |= {"role": "referent", "school_uais": by_dep[dep][:2]}
        else:
            body |= {"role": "lecteur", "departements": [dep]}
        if body["username"] not in existing:
            (await client.post(f"{API}/auth/users", json=body)).raise_for_status()
        users.append({"username": body["username"], "departement": dep, "kind": kind})
    return users


async def _login(base: str, user: dict, timings: dict[str, list[float]], errors: list[str]) -> None:
    """Users log in once per session, not at every page: logins are a phase of their own
    (password hashing is deliberately slow, and would distort the dashboard figures)."""
    async with httpx.AsyncClient(base_url=base, timeout=60) as client:
        start = time.perf_counter()
        credentials = {"username": user["username"], "password": PASSWORD}
        try:
            response = await client.post(f"{API}/auth/token", data=credentials)
        except httpx.TransportError as exc:
            errors.append(f"login: {type(exc).__name__}")
            return
        timings["login"].append(time.perf_counter() - start)
        if response.status_code != 200:
            errors.append(f"login {user['username']}: {response.status_code}")
        else:
            user["token"] = response.json()["access_token"]


async def _dashboard(base: str, user: dict, timings: dict[str, list[float]], errors: list[str]) -> None:
    if "token" not in user:
        return
    async with httpx.AsyncClient(base_url=base, timeout=60) as client:
        client.headers["Authorization"] = f"Bearer {user['token']}"
        client.headers["Accept-Encoding"] = "gzip"

        async def get(name: str, path: str) -> httpx.Response | None:
            start = time.perf_counter()
            try:
                r = await client.get(path)
            except httpx.TransportError as exc:  # counted, not fatal: the run goes on
                errors.append(f"{name} {user['kind']}: {type(exc).__name__}")
                return None
            timings[name].append(time.perf_counter() - start)
            if r.status_code != 200:
                errors.append(f"{name} {user['kind']}: {r.status_code}")
                return None
            return r

        national = user["kind"] == "admin-national"
        start = time.perf_counter()
        # The page's first wave (in parallel), then what depends on it.
        _, territories, schools, _, _ = await asyncio.gather(
            get("auth/me", f"{API}/auth/me"),
            get("territoires", f"{API}/territoires"),
            get("colleges", f"{API}/colleges"),
            get("stats", f"{API}/stats"),
            get("scores/parametres", f"{API}/scores/parametres"),
        )
        if territories is None or schools is None:
            return
        listed = schools.json()
        deps = [t["code"] for t in territories.json()]
        dep = user["departement"] if user["departement"] in deps else (deps[0] if deps else None)
        sectors = None if national or dep is None else get("secteurs", f"{API}/secteurs?departement={dep}")
        await asyncio.gather(*[c for c in (sectors, get("actions", f"{API}/actions?limit=50")) if c])
        timings["dashboard (total)"].append(time.perf_counter() - start)
        timings[f"dashboard {user['kind']} (total)"].append(time.perf_counter() - start)
        if listed:
            uai = random.choice(listed)["uai"]
            start = time.perf_counter()
            await asyncio.gather(
                get("college", f"{API}/colleges/{uai}"),
                get("historique", f"{API}/colleges/{uai}/historique"),
                get("ppms", f"{API}/colleges/{uai}/ppms"),
            )
            timings["school page (total)"].append(time.perf_counter() - start)


async def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--admin", required=True, help="A national administrator's username")
    parser.add_argument("--password", required=True)
    parser.add_argument("--users", type=int, default=300)
    parser.add_argument("--concurrency", type=int, default=50, help="Users loading at the same time")
    parser.add_argument("--rounds", type=int, default=2, help="Dashboard loads per user")
    args = parser.parse_args()

    async with httpx.AsyncClient(base_url=args.url, timeout=120) as admin:
        token = (await admin.post(f"{API}/auth/token",
                                  data={"username": args.admin, "password": args.password})).json()
        admin.headers["Authorization"] = f"Bearer {token['access_token']}"
        users = await _create_users(admin, args.users)
    print(f"{len(users)} users; {args.concurrency} at a time; {args.rounds} round(s)")

    timings: dict[str, list[float]] = defaultdict(list)
    errors: list[str] = []
    gate = asyncio.Semaphore(args.concurrency)

    async def login(user: dict) -> None:
        async with gate:
            await _login(args.url, user, timings, errors)

    async def one(user: dict) -> None:
        async with gate:
            await _dashboard(args.url, user, timings, errors)

    await asyncio.gather(*[login(u) for u in users])
    started = time.perf_counter()
    await asyncio.gather(*[one(u) for _ in range(args.rounds) for u in users])
    elapsed = time.perf_counter() - started

    print(f"\n{'request':<22}{'n':>6}{'p50 ms':>9}{'p95 ms':>9}{'max ms':>9}")
    for name in sorted(timings, key=lambda k: ("total" in k, k)):
        values = timings[name]
        print(f"{name:<22}{len(values):>6}{statistics.median(values) * 1000:>9.0f}"
              f"{_percentile(values, 95) * 1000:>9.0f}{max(values) * 1000:>9.0f}")
    dashboard_requests = sum(len(v) for k, v in timings.items() if "total" not in k and k != "login")
    print(f"\n{dashboard_requests} dashboard requests in {elapsed:.1f} s; "
          f"{len(errors)} errors{': ' + '; '.join(sorted(set(errors))[:10]) if errors else ''}")


if __name__ == "__main__":
    asyncio.run(main())
