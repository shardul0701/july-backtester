"""Delta-runner call contract and private-strategy source classification.

The report it writes is committed to the PUBLIC repo with --hide-private, so a
mistag publishes a private strategy's name. The first version tagged by module
name, which load_strategies derives from the folder name -- a private folder
not literally called "private" was tagged public (adversarial QA on #405).
"""
import importlib.util
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _script():
    spec = importlib.util.spec_from_file_location(
        "entry_trigger_delta", os.path.join(ROOT, "scripts", "entry_trigger_delta.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _logic_from(path):
    spec = importlib.util.spec_from_file_location("secret_strats.dummy", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return {"logic": mod.dummy}


def test_a_private_folder_with_any_name_is_private(tmp_path):
    d = tmp_path / "secret_strats"
    d.mkdir()
    (d / "dummy.py").write_text("def dummy(df, **kw):\n    return df\n")
    meta = _logic_from(str(d / "dummy.py"))
    D = _script()
    assert D.is_private(meta, [str(d)])
    assert not D.is_private(meta, [str(tmp_path / "elsewhere")])


def test_a_public_plugin_is_public():
    D = _script()
    from custom_strategies import sma_crossovers  # noqa: F401  (registers)
    from helpers.registry import get_active_strategies
    strats = get_active_strategies(os.path.join(ROOT, "custom_strategies"))
    meta = next(m for m in strats.values()
                if m["logic"].__module__.endswith("sma_crossovers"))
    assert not D.is_private(meta, [os.path.join(ROOT, "custom_strategies", "private")])


def test_no_findable_source_is_private_not_public():
    """A callable with no source file (a functools.partial, a C builtin)
    resolved to the CWD and was tagged PUBLIC. Unknown origin must never be
    printed under --hide-private."""
    import functools
    D = _script()
    meta = {"logic": functools.partial(len)}
    assert D.is_private(meta, [os.path.join(ROOT, "custom_strategies", "private")])


def test_load_strats_restores_the_strategies_filter():
    import config as C
    D = _script()
    before = C.CONFIG.get("strategies", "all")
    C.CONFIG["strategies"] = ["Some Subset"]
    try:
        D.load_strats(None)
        assert C.CONFIG["strategies"] == ["Some Subset"]
    finally:
        C.CONFIG["strategies"] = before


def test_ticker_dependent_signals_cover_each_symbol_and_report_real_failures():
    import pandas as pd
    D = _script()
    dates = pd.date_range("2024-01-02", periods=3)
    data = {s: pd.DataFrame({"Close": [10., 11., 12.]}, index=dates)
            for s in ("AAA", "BBB", "BROKEN")}

    def logic(df, **kwargs):
        symbol = kwargs["symbol"]
        if symbol == "BROKEN":
            raise ValueError("unavailable events")
        df["Signal"] = [1, 0, -1] if symbol == "AAA" else [0, 1, -1]
        return df

    signals, failed = D.signals_for({"logic": logic}, data, None, None)
    assert failed == ["BROKEN:ValueError"]
    assert set(signals) == {"AAA", "BBB"}
    assert signals["AAA"].tolist() == [1, 0, -1]
    assert signals["BBB"].tolist() == [0, 1, -1]
    assert all("Signal" not in df for df in data.values())


def test_strategy_params_override_injected_kwargs_like_the_pipeline():
    import pandas as pd
    D = _script()
    frame = pd.DataFrame({"Close": [10.]})

    def logic(df, **kwargs):
        assert kwargs["symbol"] == "OVERRIDE"
        assert kwargs["spy_df"] == "custom benchmark"
        return pd.Series([1], index=df.index)

    meta = {"logic": logic, "dependencies": ["spy"],
            "params": {"symbol": "OVERRIDE", "spy_df": "custom benchmark"}}
    signals, failed = D.signals_for(meta, {"AAA": frame}, frame, None)
    assert failed == []
    assert signals["AAA"].tolist() == [1]
