from testbench.recorder.base import RawEvent
from testbench.recorder.optimizer import optimize_events
from testbench.recorder.step_builder import StepBuilder


def test_optimizer_merges_consecutive_keystrokes() -> None:
    events = [
        RawEvent("keystroke", 1.0, {"locator": "Email"}, "a"),
        RawEvent("keystroke", 1.1, {"locator": "Email"}, "b"),
        RawEvent("keystroke", 1.2, {"locator": "Email"}, "c"),
    ]

    optimized = optimize_events(events)

    assert len(optimized) == 1
    assert optimized[0].value == "abc"


def test_optimizer_uses_latest_value_for_input_snapshots() -> None:
    events = [
        RawEvent("input", 1.0, {"locator": "Email"}, "a"),
        RawEvent("input", 1.1, {"locator": "Email"}, "ab"),
        RawEvent("input", 1.2, {"locator": "Email"}, "abc"),
    ]

    assert optimize_events(events)[0].value == "abc"


def test_optimizer_drops_noise_and_duplicate_clicks() -> None:
    events = [
        RawEvent("mousemove", 1.0),
        RawEvent("click", 1.1, {"locator": "Login"}),
        RawEvent("hover", 1.2, {"locator": "Login"}),
        RawEvent("click", 1.3, {"locator": "Login"}),
        RawEvent("click", 2.0, {"locator": "Login"}),
    ]

    optimized = optimize_events(events)

    assert [event.type for event in optimized] == ["click", "click"]


def test_optimizer_collapses_scroll_bursts() -> None:
    events = [
        RawEvent("scroll", 1.0, value={"direction": "down", "amount": 100}),
        RawEvent("scroll", 1.2, value={"direction": "down", "amount": 200}),
        RawEvent("scroll", 1.5, value={"direction": "down", "amount": 300}),
    ]

    optimized = optimize_events(events)

    assert len(optimized) == 1
    assert optimized[0].timestamp == 1.5
    assert optimized[0].value == {"direction": "down", "amount": 600}


def test_sensitive_fields_are_converted_to_environment_placeholders() -> None:
    event = RawEvent(
        "input",
        1,
        {"locator": "Password"},
        "never-store-this",
        metadata={"sensitive": True, "placeholder": "${LOGIN_PASSWORD}"},
    )

    step = StepBuilder().build([event])[0]

    assert step.action == "type"
    assert step.value == {"field": "Password", "value": "${LOGIN_PASSWORD}"}
    assert "never-store-this" not in repr(step)


def test_building_steps_from_the_same_events_is_deterministic() -> None:
    events = [
        RawEvent("navigation", 0.1, value="https://example.test"),
        RawEvent("input", 0.2, {"locator": "Email"}, "person@example.test"),
        RawEvent("click", 0.3, {"locator": "Continue"}),
    ]
    builder = StepBuilder()

    first = [step.model_dump() for step in builder.build(events)]
    second = [step.model_dump() for step in builder.build(events)]

    assert first == second
    assert first == [
        {"action": "open", "value": "https://example.test"},
        {
            "action": "type",
            "value": {"field": "Email", "value": "person@example.test"},
        },
        {"action": "click", "value": "Continue"},
    ]


def test_builder_creates_scroll_and_assertion_steps() -> None:
    events = [
        RawEvent("scroll", 1, value={"direction": "down", "amount": 800}),
        RawEvent("assert_visible", 2, {"locator": {"role": {"role": "button", "name": "Save"}}}),
        RawEvent("assert_url", 3, value="/complete"),
        RawEvent("visible_change", 4),
    ]

    steps = StepBuilder().build(events)

    assert [step.action for step in steps] == [
        "scroll",
        "expect_visible",
        "expect_url_contains",
        "wait",
    ]
