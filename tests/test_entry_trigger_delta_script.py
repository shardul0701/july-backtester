"""scripts/entry_trigger_delta.py must never tag a private strategy public.

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
