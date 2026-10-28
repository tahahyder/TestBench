from pathlib import Path

import pytest
from selenium.common.exceptions import NoSuchElementException

from testbench.core.steps import AndroidConfig, TestSpec
from testbench.drivers.mobile import MobileDriver, _xpath_literal, parse_page_source


def test_plain_locator_uses_required_fallback_order(tmp_path: Path) -> None:
    config = AndroidConfig.model_validate({"package": "com.example.app", "timeouts": {"explicit": 0.1}})
    mobile = MobileDriver(config.model_dump(), tmp_path)
    calls = []
    expected = object()

    class MockClient:
        def find_element(self, by, selector):
            calls.append((by, selector))
            if "description(" in selector:
                return expected
            raise NoSuchElementException()

    mobile._driver = MockClient()

    assert mobile._find_element("Login") is expected
    assert len(calls) == 5
    assert calls[0][0] == "accessibility id"
    assert 'text("Login")' in calls[1][1]
    assert 'textContains("Login")' in calls[2][1]
    assert calls[3] == ("id", "Login")
    assert 'description("Login")' in calls[4][1]


def test_explicit_locator_uses_only_requested_strategy(tmp_path: Path) -> None:
    mobile = MobileDriver({"package": "com.example.app"}, tmp_path)
    calls = []
    expected = object()

    class MockClient:
        def find_element(self, by, selector):
            calls.append((by, selector))
            return expected

    mobile._driver = MockClient()

    assert mobile._find_element({"id": "com.example:id/login"}) is expected
    assert calls == [("id", "com.example:id/login")]


@pytest.mark.parametrize(
    "config, message",
    [
        ({}, "config.app pointing to an .apk or config.package"),
        ({"package": "com.example.app", "appium_url": "localhost:4723"}, "absolute http"),
        ({"app": "application.zip"}, "must be an .apk path"),
        ({"package": "com.example.app", "timeouts": {"explicit": 0}}, "greater than 0"),
        ({"package": "com.example.app", "device": ""}, "at least 1 character"),
    ],
)
def test_android_config_errors_are_actionable(config: dict, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        AndroidConfig.model_validate(config)


def test_android_test_spec_validates_config_and_mobile_actions() -> None:
    spec = TestSpec.model_validate(
        {
            "name": "settings",
            "target": "android",
            "config": {
                "package": "com.android.settings",
                "activity": ".Settings",
                "reset": False,
                "timeouts": {"implicit": 0, "explicit": 4},
            },
            "steps": [
                {"click": {"accessibility_id": "login"}},
                {"swipe": {"direction": "up"}},
                {"scroll_to": "Display"},
                {"back": True},
                {"home": None},
                {"hide_keyboard": True},
                {"press_key": "enter"},
                {"set_orientation": "landscape"},
            ],
        }
    )

    assert spec.config["package"] == "com.android.settings"
    assert [step.action for step in spec.steps] == [
        "click",
        "swipe",
        "scroll_to",
        "back",
        "home",
        "hide_keyboard",
        "press_key",
        "set_orientation",
    ]


@pytest.mark.parametrize(
    "step",
    [
        {"click": {"css": ".login"}},
        {"swipe": {"direction": "diagonal"}},
        {"press_key": "escape"},
        {"set_orientation": "upside_down"},
        {"back": "please"},
    ],
)
def test_invalid_android_steps_are_rejected(step: dict) -> None:
    with pytest.raises(ValueError):
        TestSpec.model_validate(
            {
                "name": "Invalid Android test",
                "target": "android",
                "config": {"package": "com.example.app"},
                "steps": [step],
            }
        )


def test_page_source_parser_returns_inspector_elements() -> None:
    source = (
        '<hierarchy><node text="Settings" resource-id="com.android.settings:id/title" '
        'content-desc="Settings" class="android.widget.TextView" '
        'bounds="[0,10][100,50]" clickable="true" /></hierarchy>'
    )

    assert parse_page_source(source) == [
        {
            "text": "Settings",
            "id": "com.android.settings:id/title",
            "content_desc": "Settings",
            "class": "android.widget.TextView",
            "bounds": "[0,10][100,50]",
            "clickable": True,
        }
    ]


def test_xpath_literal_escapes_values_with_both_quote_types() -> None:
    assert _xpath_literal("a'b\"c") == "concat('a', \"'\", 'b\"c')"
