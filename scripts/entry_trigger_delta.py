"""Per-strategy trade-count delta, entry_trigger level vs edge (#401).

Runs every registered signal strategy over one fixed book twice -- once with
``entry_trigger="level"``, once with ``"edge"`` -- and prints the trade count
under each. Nothing else changes between the two runs:

* each strategy's signals are computed ONCE and handed to both modes, so a
  symbol that fails at the signal stage is missing from both books, never from
  one (the comparison stays like-for-like by construction);
* a simulation that errors in either mode is listed by name, not dropped;
* a strategy with zero trades in both modes is reported as NOT EXERCISED rather
  than counted as "unchanged" -- 0 == 0 says nothing about the semantics.

Strategies from the private submodule are tagged so a public reader can see
which rows they can reproduce, and a public-only total is printed beside the
combined one.

Usage::

    python scripts/entry_trigger_delta.py [--corpus DIR] [--private-dir DIR]
                                          [--out FILE] [--hide-private]

``--corpus`` defaults to ``<repo>/parquet_data/data``; ``--private-dir``
defaults to ``<repo>/custom_strategies/private`` when it holds strategies.
Write the report with ``--out`` rather than pasting terminal output.
"""
import argparse
import os
import sys
import warnings

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("MPLBACKEND", "Agg")
warnings.filterwarnings("ignore")

import pandas as pd  # noqa: E402

import config as C  # noqa: E402

START, END = "2018-01-01", "2026-06-30"
SYMS = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "JPM", "XOM", "JNJ", "WMT",
        "PG", "UNH", "HD", "CAT", "BA", "GE", "F", "T", "KO", "PFE",
        "CSCO", "INTC", "ORCL", "CVX", "MRK", "ABBV", "CRM", "AMD", "QCOM", "TXN"]
STOP = {"type": "percentage", "value": 0.05}
CAPITAL, ALLOC = 100000.0, 0.10


def load(corpus, sym):
    p = os.path.join(corpus, f"{sym}.parquet")
    if not os.path.exists(p):
        return None
    df = pd.read_parquet(p)
    df.index = pd.to_datetime(df.index)
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    df = df.loc[(df.index >= pd.Timestamp(START, tz="UTC"))
                & (df.index <= pd.Timestamp(END, tz="UTC"))]
    need = {"Open", "High", "Low", "Close", "Volume"}
    return df if need.issubset(df.columns) and len(df) > 250 else None


def load_strats(private_dir):
    from helpers.registry import get_active_strategies, load_strategies
    strats = get_active_strategies(os.path.join(ROOT, "custom_strategies"))
    if private_dir and os.path.isdir(private_dir):
        load_strategies(private_dir)
        strats = get_active_strategies(os.path.join(ROOT, "custom_strategies"))
    return strats


def is_private(meta):
    mod = getattr(meta["logic"], "__module__", "") or ""
    return mod.startswith("private.") or ".private." in mod


def signals_for(meta, data, spy, vix):
    """Signals per symbol, plus the symbols that failed at this stage."""
    sigs, failed = {}, []
    deps = meta.get("dependencies") or []
    for sym, df in data.items():
        try:
            kw = dict(meta.get("params") or {})
            if "spy" in deps:
                kw["spy_df"] = spy
            if "vix" in deps:
                kw["vix_df"] = vix
            r = meta["logic"](df.copy(), **kw)
            s = r["Signal"] if isinstance(r, pd.DataFrame) and "Signal" in r else r
            sigs[sym] = pd.Series(s).reindex(df.index).fillna(0)
        except Exception as e:  # noqa: BLE001 -- reported in the census
            failed.append(f"{sym}:{type(e).__name__}")
    return sigs, failed


def count_trades(mode, data, sigs, spy, vix):
    from helpers import portfolio_simulations as PS
    C.CONFIG["entry_trigger"] = mode
    res = PS.run_portfolio_simulation(
        {k: data[k] for k in sigs}, sigs, CAPITAL, ALLOC, spy, vix, None, STOP)
    tl = res[0] if isinstance(res, tuple) else res
    log = tl.get("trade_log") if isinstance(tl, dict) else tl
    return len(log) if log is not None else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--corpus", default=os.path.join(ROOT, "parquet_data", "data"))
    ap.add_argument("--private-dir", default=os.path.join(ROOT, "custom_strategies", "private"))
    ap.add_argument("--out", help="write the report here instead of stdout")
    ap.add_argument("--hide-private", action="store_true",
                    help="list private strategies only as aggregate counts, never "
                         "by name (use this for anything committed to the public repo)")
    args = ap.parse_args()

    data = {s: d for s in SYMS if (d := load(args.corpus, s)) is not None}
    spy = load(args.corpus, "SPY")
    vix = load(args.corpus, "$VIX")
    if vix is None:
        vix = load(args.corpus, "VIX")
    strats = load_strats(args.private_dir)

    rows, errors, idle, sig_fail = [], [], [], {}
    for name in sorted(strats):
        meta = strats[name]
        print(f"  {name}", file=sys.stderr, flush=True)
        sigs, failed = signals_for(meta, data, spy, vix)
        if failed:
            sig_fail[name] = failed
        tag = "private" if is_private(meta) else "public"
        if not sigs:
            errors.append((name, tag, "no symbol produced signals"))
            continue
        counts = {}
        for mode in ("level", "edge"):
            try:
                counts[mode] = count_trades(mode, data, sigs, spy, vix)
            except Exception as e:  # noqa: BLE001 -- listed below, not dropped
                counts[mode] = f"{type(e).__name__}: {e}"[:80]
        a, b = counts["level"], counts["edge"]
        if not (isinstance(a, int) and isinstance(b, int)):
            errors.append((name, tag, f"level={a!r} edge={b!r}"))
        elif a == 0 and b == 0:
            idle.append((name, tag))
        else:
            rows.append((name, tag, a, b))

    out = []
    w = out.append
    w(f"universe: {len(data)}/{len(SYMS)} symbols, {START}..{END}, "
      f"${CAPITAL:,.0f}, {ALLOC:.0%} allocation, {STOP['value']:.0%} percentage stop")
    n_priv = sum(1 for m in strats.values() if is_private(m))
    w(f"strategies registered: {len(strats)}  ({len(strats) - n_priv} public, {n_priv} private)")
    w("")
    w(f"{'strategy':52s} {'src':7s} {'level':>7s} {'edge':>7s} {'delta':>7s} {'pct':>8s}")
    for name, tag, a, b in rows:
        if args.hide_private and tag == "private":
            continue
        w(f"{name[:52]:52s} {tag:7s} {a:7d} {b:7d} {b - a:+7d} {(b - a) / a * 100:+7.1f}%")

    def total(label, sel):
        la = sum(r[2] for r in sel)
        lb = sum(r[3] for r in sel)
        pct = (lb - la) / la * 100 if la else 0.0
        w(f"{label:60s} {la:7d} {lb:7d} {lb - la:+7d} {pct:+7.1f}%")
        w(f"{'':60s} compared {len(sel)}   unchanged {sum(1 for r in sel if r[2] == r[3])}"
          f"   fewer {sum(1 for r in sel if r[3] < r[2])}   more {sum(1 for r in sel if r[3] > r[2])}")

    w("")
    total("TOTAL (all)", rows)
    total("TOTAL (public only)", [r for r in rows if r[1] == "public"])
    total("TOTAL (private only)", [r for r in rows if r[1] == "private"])
    w("")

    def names(items, fmt):
        shown = [fmt(x) for x in items if not (args.hide_private and x[1] == "private")]
        hidden = sum(1 for x in items if args.hide_private and x[1] == "private")
        if hidden:
            shown.append(f"{hidden} private (not named)")
        return ", ".join(shown) or "none"

    w("unchanged (level == edge, > 0 trades): "
      + names([r for r in rows if r[2] == r[3]], lambda r: f"{r[0]} [{r[1]}]"))
    w("not exercised (0 trades in both modes, excluded from counts): "
      + names(idle, lambda x: f"{x[0]} [{x[1]}]"))
    w("simulation errors (excluded, listed): "
      + names(errors, lambda x: f"{x[0]} [{x[1]}] {x[2]}"))
    n_fail = sum(len(v) for v in sig_fail.values())
    w(f"signal-stage symbol failures: {n_fail} across {len(sig_fail)} strategies"
      + ("" if args.hide_private else
         "".join(f"\n  {k}: {', '.join(v)}" for k, v in sorted(sig_fail.items()))))

    text = "\n".join(out) + "\n"
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
    sys.stdout.write(text)


if __name__ == "__main__":
    main()
