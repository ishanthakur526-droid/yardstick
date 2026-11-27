"""Every screen, on every kind of project, opens without an error (Streamlit's headless test runner)."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from conftest import real

APP = str(Path(__file__).resolve().parent.parent / "app.py")
SECTIONS = ["Home", "See it", "Plan it", "Requisition", "Match the sets", "About the data"]


def run(section: str, dataset: str, custom=None) -> AppTest:
    at = AppTest.from_file(APP, default_timeout=300)
    at.session_state["dataset"] = dataset
    at.session_state["section"] = section
    if custom is not None:
        at.session_state["custom_ds"] = custom
    at.run()
    return at


def widget(at: AppTest, name: str):
    """A widget's value: keys carry the project's fingerprint (see ui/state.k)."""
    keys = [key for key in at.session_state.keys() if str(key).startswith(name + "@")]
    assert len(keys) == 1, keys
    return at.session_state[keys[0]]


def assert_clean(at: AppTest) -> None:
    assert not at.exception, [e.value for e in at.exception]
    assert not at.error, [e.value for e in at.error]


@pytest.mark.parametrize("section", SECTIONS)
def test_the_sample_project(section):
    at = run(section, "sample")
    assert_clean(at)
    assert "made-up sample data" in at.caption[0].value


@pytest.mark.parametrize("section", SECTIONS)
def test_a_small_project_with_no_sets_or_transfers(section, minimal_ds):
    at = run(section, "custom", custom=minimal_ds)
    assert_clean(at)
    shown = at.session_state["section"]
    assert shown == ("Home" if section == "Match the sets" else section)  # no sets: that screen is not offered


@real
@pytest.mark.parametrize("section", SECTIONS)
def test_the_real_project(section):
    at = run(section, "bundle")
    assert_clean(at)
    assert "Lumino" in at.caption[0].value


def test_switching_projects_forgets_the_last_projects_choices(minimal_ds):
    at = AppTest.from_file(APP, default_timeout=300)
    at.session_state["custom_ds"] = minimal_ds
    at.session_state["dataset"] = "sample"
    at.session_state["section"] = "See it"
    at.run()
    assert widget(at, "see_item") == "SPO001"
    at.radio(key="dataset").set_value("custom").run()  # the visitor picks another project in the sidebar
    assert_clean(at)
    assert widget(at, "see_item") in {"A100", "B200", "C300"}
    assert len([key for key in at.session_state.keys() if str(key).startswith("see_item@")]) == 1


def test_rupees_appear_only_after_a_rate_is_typed():
    at = run("Home", "sample")
    assert_clean(at)
    assert not [m for m in at.markdown if "priced item" in m.value]  # no rates: nothing in rupees
    key = next(str(x) for x in at.session_state.keys() if str(x).startswith("home_rates@"))
    at.session_state[key] = {
        "edited_rows": {0: {"Rate (₹ per unit)": 1500.0}},
        "added_rows": [],
        "deleted_rows": [],
    }
    at.run()
    assert_clean(at)
    assert [m for m in at.markdown if "1 priced item" in m.value]
    assert any("₹" in str(df.value.to_dict()) for df in at.dataframe)
