"""Deterministic inference for regression tests that assert on authored lesson data."""


def install(replace=setattr):
    from app.agents import encyclopedia_agent, feynman_agent, model_teaching, report_agent
    from app.services import llm_client, model_profiles

    model_profiles.save_profile(model_profiles.ProfileInput(
        name="Fixture provider",
        mode="api",
        model="fixture",
        base_url="http://127.0.0.1:1/v1",
    ))

    def apply(db, turn, window):
        agent = feynman_agent if window == "feynman" else encyclopedia_agent
        return agent.stream(db, turn)

    replace(model_teaching, "prepare", lambda db, turn, window, profile: window)
    replace(model_teaching, "apply", apply)
    replace(model_teaching, "diagnose", lambda db, session, profile: report_agent.generate(db, session))
    replace(llm_client, "extract", lambda text, title, profile: llm_client.local_extract(text, title))
