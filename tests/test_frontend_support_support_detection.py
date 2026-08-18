from src.agents.frontend_support.conversation import looks_like_support


def test_missing_profile_is_recognised_as_support_problem():
    assert looks_like_support("An employee profile is missing in Babylon. What should I do?")
