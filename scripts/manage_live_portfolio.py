"""Daily after-close manager for all 5 live paper strategies (T+1 model).

Intended to run ONCE per weekday after the close (via a 5:30 PM ET scheduled task).
The strategies are next-day-open (T+1): we observe day N's close, decide, and the
orders fill at day N+1's open.

Flow:
  Phase 1  B1/B2/MR  -> daily_pipeline.py --cloud  (incremental state: checks each
           position's stop / target / MA20-cross / max-hold, computes new entries,
           submits market/day orders). Its stdout is logged + split per strategy.
  Phase 2  S8        -> build_s8_alpaca_targets.py  -> reconcile vs live S8 account.
  Phase 3  SSD       -> build_ssd_alpaca_targets.py -> reconcile vs live SSD account.

Every order is appended to paper_state/logs/<STRATEGY>.log (a dated block listing
each SELL/BUY with its reason), plus a combined paper_state/logs/daily_run.log.

  python scripts/manage_live_portfolio.py            # live: submits orders
  python scripts/manage_live_portfolio.py --dry-run  # prints/logs, submits nothing
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
LOGDIR = ROOT / "paper_state" / "logs"
ALPACA_BASE = "https://paper-api.alpaca.markets"
MIN_NOTIONAL = 25.0
SSD_PRUNE = 0.005   # drop sub-0.5% dust (scale-approximation noise) before SSD orders

# Per-strategy Alpaca env-key prefixes.
PREFIX = {"B1_VG12_PIT": "B1", "B2_VG12_PIT": "B2", "MR_VG12_PIT": "MR",
          "S8_DMVC35_PIT": "S8", "SSD_B2_DG20_PIT": "SSD"}


def load_env() -> dict:
    env = {}
    f = ROOT / ".env"
    if f.exists():
        for line in f.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def alpaca(method: str, path: str, kid: str, sec: str, body=None):
    data = json.dumps(body).encode() if body else None
    req = Request(ALPACA_BASE + path, data=data, method=method,
                  headers={"APCA-API-KEY-ID": kid, "APCA-API-SECRET-KEY": sec,
                           "Content-Type": "application/json"})
    try:
        with urlopen(req, timeout=30) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except HTTPError as e:
        return {"_err": f"{e.code} {e.read().decode()[:200]}"}
    except Exception as e:  # noqa: BLE001
        return {"_err": str(e)}


def log_block(strategy: str, text: str) -> None:
    LOGDIR.mkdir(parents=True, exist_ok=True)
    with (LOGDIR / f"{strategy}.log").open("a", encoding="utf-8") as fh:
        fh.write(text + "\n")
    with (LOGDIR / "daily_run.log").open("a", encoding="utf-8") as fh:
        fh.write(text + "\n")


def reconcile_and_submit(strategy: str, weights: dict[str, float], env: dict,
                         dry_run: bool, prune: float = 0.0) -> str:
    """Reconcile a target-weight book vs the live account; submit deltas; return log text."""
    pre = PREFIX[strategy]
    kid, sec = env.get(f"{pre}_APCA_API_KEY_ID"), env.get(f"{pre}_APCA_API_SECRET_KEY")
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    if not kid or not sec:
        return f"=== {ts} ET | {strategy} | NO KEYS — skipped ==="

    acct = alpaca("GET", "/v2/account", kid, sec)
    if "_err" in acct:
        return f"=== {ts} ET | {strategy} | ACCOUNT ERROR: {acct['_err']} ==="
    equity = float(acct["equity"])
    cur = {p["symbol"].upper(): float(p["market_value"])
           for p in alpaca("GET", "/v2/positions", kid, sec)}

    if prune:
        weights = {s: w for s, w in weights.items() if w >= prune}
    targets = {s.upper(): w * equity for s, w in weights.items() if w > 0}

    head = (f"=== {ts} ET | {strategy} | equity=${equity:,.2f} | "
            f"{'DRY-RUN' if dry_run else 'LIVE'} ===")
    lines = [head]
    n = 0
    for sym in sorted(set(targets) | set(cur)):
        delta = targets.get(sym, 0.0) - cur.get(sym, 0.0)
        if abs(delta) < MIN_NOTIONAL:
            continue
        side = "buy" if delta > 0 else "sell"
        if sym not in cur:
            reason = "NEW ENTRY"
        elif targets.get(sym, 0.0) == 0:
            reason = "EXIT (stop/target/exit-rule or dropped from book)"
        else:
            reason = "REBALANCE"
        flag = ""
        if not dry_run:
            r = alpaca("POST", "/v2/orders", kid, sec,
                       {"symbol": sym, "side": side, "type": "market",
                        "time_in_force": "day", "notional": str(round(abs(delta), 2))})
            if "_err" in r:
                flag = f"  [FAILED: {r['_err']}]"
            time.sleep(0.15)
        lines.append(f"  {side.upper():4s} {sym:6s} ${abs(delta):>11,.2f}  {reason}{flag}")
        n += 1
    if n == 0:
        lines.append("  no changes — positions already match target")
    return "\n".join(lines)


def book_from_csv(path: Path, weight_col: str = "target_weight",
                  sym_col: str = "symbol") -> dict[str, float]:
    import csv
    out: dict[str, float] = {}
    with path.open() as fh:
        for row in csv.DictReader(fh):
            sym = (row.get(sym_col) or row.get("ticker") or "").upper()
            if not sym:
                continue
            out[sym] = out.get(sym, 0.0) + float(row[weight_col])
    return out


def run(cmd: list[str], env: dict) -> str:
    import os
    full = {**os.environ, **env, "PYTHONIOENCODING": "utf-8"}
    p = subprocess.run([sys.executable, *cmd], cwd=str(ROOT), env=full,
                       capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    out = p.stdout or ""
    err = p.stderr or ""
    return out + ("\n" + err if err.strip() else "")


def split_pipeline_log(stdout: str) -> dict[str, str]:
    """Extract each '[<STRAT>]' order block from daily_pipeline stdout."""
    blocks: dict[str, list[str]] = {}
    cur = None
    for line in stdout.splitlines():
        marker = next((s for s in ("B1_VG12_PIT", "B2_VG12_PIT", "MR_VG12_PIT")
                       if f"[{s}]" in line), None)
        if marker:
            cur = marker
            blocks.setdefault(cur, [])
        if cur is not None:
            blocks[cur].append(line.rstrip())
            if line.strip().startswith(("[dry-run]", "Submitted", "ERROR")):
                cur = None
    return {k: "\n".join(v) for k, v in blocks.items()}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="Print/log only; submit nothing.")
    ap.add_argument("--date", help="Override trading date (YYYY-MM-DD).")
    args = ap.parse_args()
    env = load_env()
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    LOGDIR.mkdir(parents=True, exist_ok=True)

    banner = f"\n{'#'*64}\n# DAILY MANAGE RUN {ts} ET | {'DRY-RUN' if args.dry_run else 'LIVE'}\n{'#'*64}"
    print(banner)
    with (LOGDIR / "daily_run.log").open("a", encoding="utf-8") as fh:
        fh.write(banner + "\n")

    # ── Phase 1: B1/B2/MR via daily_pipeline (its own exit/entry engine + submit) ──
    print("\n[Phase 1] B1/B2/MR — daily_pipeline.py")
    cmd = ["scripts/daily_pipeline.py", "--cloud"]
    if args.dry_run:
        cmd.append("--dry-run")
    if args.date:
        cmd += ["--date", args.date]
    out = run(cmd, env)
    with (LOGDIR / "daily_run.log").open("a", encoding="utf-8") as fh:
        fh.write(out + "\n")
    for strat, blk in split_pipeline_log(out).items():
        log_block(strat, f"=== {ts} ET | {strat} | {'DRY-RUN' if args.dry_run else 'LIVE'} ===\n{blk}")
        print(f"  logged {strat}")

    # ── Phase 2: S8 ───────────────────────────────────────────────────────────────
    print("\n[Phase 2] S8 — build + reconcile")
    run(["scripts/build_s8_alpaca_targets.py", "--account-equity", "100000"], env)
    s8_book = book_from_csv(ROOT / "output" / "live_s8_alpaca" / "S8_latest_target_weights.csv")
    blk = reconcile_and_submit("S8_DMVC35_PIT", s8_book, env, args.dry_run)
    log_block("S8_DMVC35_PIT", blk)
    print(blk)

    # ── Phase 3: SSD ──────────────────────────────────────────────────────────────
    print("\n[Phase 3] SSD — build + reconcile")
    run(["scripts/build_ssd_alpaca_targets.py", "--account-equity", "100000"], env)
    ssd_book = book_from_csv(ROOT / "output" / "live_ssd_alpaca" / "SSD_latest_target_weights.csv")
    blk = reconcile_and_submit("SSD_B2_DG20_PIT", ssd_book, env, args.dry_run, prune=SSD_PRUNE)
    log_block("SSD_B2_DG20_PIT", blk)
    print(blk)

    print(f"\nDone. Per-strategy logs in {LOGDIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
