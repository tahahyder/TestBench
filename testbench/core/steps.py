"""Pydantic models for TestBench YAML test definitions."""

from typing import Any, ClassVar, Literal, get_args
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

Action = Literal[
    "open",
    "click",
    "type",
    "wait",
    "screenshot",
    "expect_visible",
    "expect_text",
    "expect_url_contains",
    "expect_enabled",
    "select_option",
    "scroll",
    "swipe",
    "scroll_to",
    "back",
    "home",
    "hide_keyboard",
    "press_key",
    "set_orientation",
    "wait_for_image",
    "click_image",
    "expect_image",
    "expect_no_image",
    "click_text",
    "tap",
    "drag",
    "hold_key",
    "wait_for_change",
    "expect_no_change",
    "measure_fps",
]
BUILTIN_ACTIONS = get_args(Action)


class AndroidTimeouts(BaseModel):
    """Wait settings for an Appium Android session."""

    model_config = ConfigDict(extra="forbid")

    implicit: float = Field(default=0, ge=0)
    explicit: float = Field(default=10, gt=0)


class AndroidConfig(BaseModel):
    """Validated Android launch settings."""

    model_config = ConfigDict(extra="forbid")

    appium_url: str = "http://127.0.0.1:4723"
    device: str = Field(default="auto", min_length=1)
    app: str | None = None
    package: str | None = None
    activity: str | None = None
    reset: bool = True
    timeouts: AndroidTimeouts = Field(default_factory=AndroidTimeouts)

    @model_validator(mode="after")
    def validate_app_configuration(self) -> "AndroidConfig":
        parsed_url = urlparse(self.appium_url)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            raise ValueError("appium_url must be an absolute http(s) URL.")
        if self.app is not None and not self.app.strip():
            raise ValueError("config.app must not be empty.")
        if self.package is not None and not self.package.strip():
            raise ValueError("config.package must not be empty.")
        if self.activity is not None and not self.activity.strip():
            raise ValueError("config.activity must not be empty.")
        app_is_apk = bool(self.app and self.app.lower().endswith(".apk"))
        if self.app and not app_is_apk and not self.package:
            raise ValueError("config.app must be an .apk path, or provide config.package for an already-installed app.")
        if not app_is_apk and not self.package:
            raise ValueError("Android tests need config.app pointing to an .apk or config.package.")
        return self


class GameConfig(BaseModel):
    """Validated options shared by the vision-based game surfaces."""

    model_config = ConfigDict(extra="forbid")

    surface: Literal["desktop", "android", "browser"] = "desktop"
    window_title: str | None = None
    device: str = Field(default="auto", min_length=1)
    url: str | None = None
    templates_dir: str = "templates"
    match_threshold: float = Field(default=0.85, ge=0, le=1)
    image_match_threshold: float | None = Field(default=None, ge=0, le=1)
    step_timeout: float = Field(default=15, gt=0)
    scale_range: tuple[float, float] = (0.8, 1.2)
    color_check: bool = False
    ai_fallback: bool = False

    @model_validator(mode="after")
    def validate_surface_config(self) -> "GameConfig":
        if self.surface == "browser":
            parsed_url = urlparse(self.url or "")
            if parsed_url.scheme not in {"http", "https", "file"} or (
                not parsed_url.netloc and parsed_url.scheme != "file"
            ):
                raise ValueError("config.url must be an absolute http(s) URL or a file URL for browser surfaces.")
        if self.surface == "desktop" and self.window_title is not None:
            if not self.window_title.strip():
                raise ValueError("config.window_title must not be empty.")
        if self.scale_range[0] <= 0 or self.scale_range[1] < self.scale_range[0]:
            raise ValueError("config.scale_range must be [minimum, maximum] with 0 < minimum <= maximum.")
        if not self.templates_dir.strip():
            raise ValueError("config.templates_dir must not be empty.")
        return self


class Step(BaseModel):
    """A normalized automation action."""

    model_config = ConfigDict(extra="forbid")

    action: str = Field(json_schema_extra={"enum": list(BUILTIN_ACTIONS)})
    value: Any

    @model_validator(mode="before")
    @classmethod
    def normalize_action(cls, value: Any) -> Any:
        if isinstance(value, cls):
            return value
        if not isinstance(value, dict) or len(value) != 1:
            raise ValueError("Each step must contain exactly one action.")
        action, argument = next(iter(value.items()))
        if action not in BUILTIN_ACTIONS:
            from testbench.plugins import step_plugin

            if not isinstance(action, str) or step_plugin(action) is None:
                raise ValueError(f"Unsupported step action: {action}")
        if not isinstance(action, str):
            raise ValueError(f"Unsupported step action: {action}")
        return {"action": action, "value": argument}

    @model_validator(mode="after")
    def validate_value(self) -> "Step":
        if self.action not in BUILTIN_ACTIONS:
            from testbench.plugins import step_plugin

            plugin = step_plugin(self.action)
            if plugin is None:
                raise ValueError(f"Unsupported step action: {self.action}")
            model = getattr(plugin, "model", None)
            if model is None or not callable(getattr(model, "model_validate", None)):
                raise ValueError(f"Step plugin {self.action!r} must expose a Pydantic model.")
            validated = model.model_validate(self.value)
            self.value = validated.model_dump()
            return self
        if self.action == "type":
            if (
                not isinstance(self.value, dict)
                or set(self.value) != {"field", "value"}
                or not _valid_locator_target(self.value["field"])
                or not isinstance(self.value["value"], str)
            ):
                raise ValueError("The type action requires {field: ..., value: ...}.")
        elif self.action == "select_option":
            if (
                not isinstance(self.value, dict)
                or set(self.value) != {"field", "value"}
                or not _valid_locator_target(self.value["field"])
                or not isinstance(self.value["value"], str)
            ):
                raise ValueError("select_option requires {field: locator, value: option}.")
        elif self.action == "scroll":
            if (
                not isinstance(self.value, dict)
                or set(self.value) != {"direction", "amount"}
                or self.value["direction"] not in {"up", "down", "left", "right"}
                or not _is_nonnegative_number(self.value["amount"])
                or self.value["amount"] <= 0
            ):
                raise ValueError("scroll requires {direction: up|down|left|right, amount: positive number}.")
        elif self.action == "expect_url_contains":
            if not isinstance(self.value, str) or not self.value.strip():
                raise ValueError("expect_url_contains requires a non-empty substring.")
        elif self.action == "expect_enabled":
            if (
                not isinstance(self.value, dict)
                or set(self.value) != {"target", "enabled"}
                or not _valid_locator_target(self.value["target"])
                or not isinstance(self.value["enabled"], bool)
            ):
                raise ValueError("expect_enabled requires {target: locator, enabled: true|false}.")
        elif self.action == "expect_visible":
            if not _valid_locator_target(self.value):
                raise ValueError("expect_visible requires non-empty text or a supported locator.")
        elif self.action == "wait":
            if not _is_nonnegative_number(self.value):
                raise ValueError("The wait action requires a non-negative number.")
        elif self.action == "click":
            if not _valid_locator_target(self.value):
                raise ValueError(
                    "click requires non-empty text or one explicit locator: "
                    "{id: ...}, {xpath: ...}, or {accessibility_id: ...}."
                )
        elif self.action == "swipe":
            if (
                not isinstance(self.value, dict)
                or set(self.value) != {"direction"}
                or self.value["direction"] not in ("up", "down", "left", "right")
            ):
                raise ValueError("swipe requires {direction: up|down|left|right}.")
        elif self.action in {"back", "home", "hide_keyboard"}:
            if self.value not in (None, True):
                raise ValueError(f"{self.action} takes no value; use true or omit it.")
        elif self.action == "press_key":
            if not isinstance(self.value, str) or self.value not in {
                "enter",
                "back",
                "home",
                "space",
                "left",
                "right",
                "up",
                "down",
                "tab",
            }:
                raise ValueError("press_key must be enter, back, home, space, an arrow, or tab.")
        elif self.action == "set_orientation":
            if not isinstance(self.value, str) or self.value not in (
                "portrait",
                "landscape",
            ):
                raise ValueError("set_orientation must be portrait or landscape.")
        elif self.action in {
            "wait_for_image",
            "click_image",
            "expect_image",
            "expect_no_image",
        }:
            if not _valid_image_step(self.value):
                raise ValueError(
                    f"{self.action} needs an image filename or a mapping with "
                    "image and optional threshold, region, and describe."
                )
        elif self.action in {"click_text", "expect_text"}:
            if not isinstance(self.value, str) or not self.value.strip():
                raise ValueError(f"{self.action} requires non-empty text.")
        elif self.action == "tap":
            if isinstance(self.value, dict) and set(self.value) == {"x", "y"}:
                _validate_point([self.value["x"], self.value["y"]], "tap")
            else:
                _validate_point(self.value, "tap")
        elif self.action == "drag":
            if (
                not isinstance(self.value, dict)
                or set(self.value) - {"from", "to", "duration"}
                or not {"from", "to"} <= set(self.value)
            ):
                raise ValueError("drag requires {from: [x,y], to: [x,y], duration?: seconds}.")
            _validate_point(self.value["from"], "drag from")
            _validate_point(self.value["to"], "drag to")
            duration = self.value.get("duration", 0.5)
            if not _is_nonnegative_number(duration):
                raise ValueError("drag duration must be a non-negative number.")
        elif self.action == "hold_key":
            if (
                not isinstance(self.value, dict)
                or set(self.value) != {"key", "seconds"}
                or not isinstance(self.value["key"], str)
                or not self.value["key"].strip()
                or not _is_nonnegative_number(self.value["seconds"])
            ):
                raise ValueError("hold_key requires {key: ..., seconds: non-negative number}.")
        elif self.action == "wait_for_change":
            _validate_frame_step(self.value, "wait_for_change", "timeout")
        elif self.action == "expect_no_change":
            _validate_frame_step(self.value, "expect_no_change", "seconds")
        elif self.action == "measure_fps":
            if (
                not isinstance(self.value, dict)
                or set(self.value) - {"seconds", "min_fps"}
                or "seconds" not in self.value
                or "min_fps" not in self.value
                or not _is_nonnegative_number(self.value["seconds"])
                or self.value["seconds"] <= 0
                or not _is_nonnegative_number(self.value["min_fps"])
            ):
                raise ValueError("measure_fps requires {seconds: positive, min_fps: non-negative}.")
        elif not isinstance(self.value, str) or not self.value.strip():
            raise ValueError(f"The {self.action} action requires a non-empty string.")
        return self


class TestSpec(BaseModel):
    """Validated description of a web, Android, or game test."""

    __test__: ClassVar[bool] = False
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    target: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9_]*$")
    config: dict[str, Any] = Field(default_factory=dict)
    variables: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    steps: list[Step] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_target_config(self) -> "TestSpec":
        if any(not _valid_variable_name(variable) for variable in self.variables):
            raise ValueError(
                "variables must contain environment variable names using letters, digits, and underscores."
            )
        if len(set(self.variables)) != len(self.variables):
            raise ValueError("variables must not contain duplicate names.")
        if self.target == "android":
            try:
                self.config = AndroidConfig.model_validate(self.config).model_dump()
            except ValidationError as exc:
                raise ValueError(f"Invalid Android config: {exc}") from exc
            for step in self.steps:
                locator_targets: list[Any] = []
                if step.action in {"click", "expect_visible"}:
                    locator_targets.append(step.value)
                elif step.action in {"type", "select_option"}:
                    locator_targets.append(step.value["field"])
                elif step.action == "expect_enabled":
                    locator_targets.append(step.value["target"])
                if any(not _valid_android_locator(target) for target in locator_targets):
                    raise ValueError(
                        "Android locators support accessibility_id, id, and xpath "
                        "strategies; use a plain text value for visible text."
                    )
                if step.action == "press_key" and step.value not in {
                    "enter",
                    "back",
                    "home",
                }:
                    raise ValueError("Android press_key must be one of: enter, back, home.")
        elif self.target == "game":
            try:
                self.config = GameConfig.model_validate(self.config).model_dump()
            except ValidationError as exc:
                raise ValueError(f"Invalid game config: {exc}") from exc
        return self


def _valid_locator_target(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if not isinstance(value, dict):
        return False
    if set(value) == {"frame_url", "locator"}:
        return isinstance(value["frame_url"], str) and _valid_locator_target(value["locator"])
    if len(value) != 1:
        return False
    key, argument = next(iter(value.items()))
    if key in {
        "id",
        "xpath",
        "accessibility_id",
        "label",
        "placeholder",
        "text",
        "test_id",
        "css",
    }:
        return isinstance(argument, str) and bool(argument.strip())
    if key == "role":
        return (
            isinstance(argument, dict)
            and set(argument) == {"role", "name"}
            and isinstance(argument["role"], str)
            and bool(argument["role"].strip())
            and isinstance(argument["name"], str)
            and bool(argument["name"].strip())
        )
    return False


def _valid_android_locator(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    return (
        isinstance(value, dict)
        and len(value) == 1
        and next(iter(value)) in {"id", "xpath", "accessibility_id"}
        and isinstance(next(iter(value.values())), str)
        and bool(next(iter(value.values())).strip())
    )


def _valid_variable_name(value: str) -> bool:
    return bool(value) and value.replace("_", "A").isalnum() and not value[0].isdigit()


def _is_nonnegative_number(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and value >= 0


def _validate_point(value: Any, label: str) -> None:
    if (
        not isinstance(value, (list, tuple))
        or len(value) != 2
        or any(not _is_nonnegative_number(coordinate) for coordinate in value)
    ):
        raise ValueError(f"{label} must be [x, y] with non-negative numbers.")


def _validate_region(value: Any) -> None:
    if value is None:
        return
    if (
        not isinstance(value, (list, tuple))
        or len(value) != 4
        or any(not _is_nonnegative_number(coordinate) for coordinate in value)
        or value[2] <= 0
        or value[3] <= 0
    ):
        raise ValueError("region must be [x, y, width, height] with positive size.")


def _valid_image_step(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if (
        not isinstance(value, dict)
        or set(value) - {"image", "threshold", "region", "describe"}
        or not isinstance(value.get("image"), str)
        or not value["image"].strip()
    ):
        return False
    threshold = value.get("threshold")
    if threshold is not None and (
        isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not 0 <= threshold <= 1
    ):
        return False
    try:
        _validate_region(value.get("region"))
    except ValueError:
        return False
    describe = value.get("describe")
    return describe is None or isinstance(describe, str)


def _validate_frame_step(value: Any, action: str, duration_key: str) -> None:
    if not isinstance(value, dict) or set(value) - {"region", duration_key}:
        raise ValueError(f"{action} accepts region and {duration_key}.")
    if duration_key not in value or not _is_nonnegative_number(value[duration_key]):
        raise ValueError(f"{action}.{duration_key} must be a non-negative number.")
    if duration_key == "timeout" and value[duration_key] <= 0:
        raise ValueError("wait_for_change.timeout must be greater than zero.")
    try:
        _validate_region(value.get("region"))
    except ValueError as exc:
        raise ValueError(f"{action}: {exc}") from exc
