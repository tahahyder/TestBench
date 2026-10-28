from testbench.recorder.locator import score_locator_candidates, stable_identifier


def test_locator_priority_prefers_accessible_role_then_label_and_fallbacks() -> None:
    candidates = score_locator_candidates(
        {
            "role": "button",
            "name": "Continue",
            "label": "Continue",
            "placeholder": "Continue",
            "text": "Continue",
            "data-testid": "continue-button",
            "id": "continue",
            "css": "button.primary",
            "xpath": "//button",
        }
    )

    assert [candidate.kind for candidate in candidates] == [
        "role",
        "label",
        "placeholder",
        "text",
        "test_id",
        "id",
        "css",
        "xpath",
    ]
    assert candidates[0].as_step_target() == {"role": {"role": "button", "name": "Continue"}}


def test_locator_policy_rejects_generated_ids_and_complex_css() -> None:
    candidates = score_locator_candidates(
        {
            "id": "button-7c9f103b12345678",
            "css": "div:nth-child(1) > div:nth-child(2) > button:nth-child(4)",
            "xpath": "//button",
        }
    )

    assert [candidate.kind for candidate in candidates] == ["xpath"]
    assert not stable_identifier("react-2026102712345")
    assert not stable_identifier("react-12345")
    assert stable_identifier("login-button")
