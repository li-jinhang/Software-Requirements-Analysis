"""Deterministic inference for regression tests that assert on authored lesson data."""


def install(replace=setattr):
    from app.agents import encyclopedia_agent, feynman_agent, model_teaching, report_agent
    from app.services import llm_client, model_profiles
    from app.agents.teaching import model as teaching_model

    import json
    from app.agents.teaching import rubric
    from types import SimpleNamespace

    def assess(state, schema, correction=False):
        data = json.loads(teaching_model.local_assessment(state))
        if state.get("current_item_id"):
            # The fixture assesses exactly the supplied focus. Production uses the bound model.
            issues = [] if len(state["content"]) > 5 else [
                dict(issue_type="vague", detail="请补充定义", user_claim=state["content"])
            ]
            data["item_marks"] = [dict(item_id=state["current_item_id"],
                status="partial" if issues else "mastered", user_evidence=state["content"], issues=issues)]
        return json.dumps(data, ensure_ascii=False)

    def correct(schema, task, material):
        return schema(explanations=[dict(issue_number=i, accurate_explanation="请结合：" + p["item_statement"],
                                         source_indices=[0])
                                    for i, p in enumerate(json.loads(material)["issues"], 1)])
    replace(rubric, "ModelConnection", lambda profile: SimpleNamespace(structured=correct))
    replace(teaching_model, "assess", assess)
    replace(teaching_model, "offline", lambda: True)

    model_profiles.save_profile(model_profiles.ProfileInput(
        name="Fixture provider",
        mode="api",
        model="fixture",
        base_url="http://127.0.0.1:1/v1",
    ))

    def apply(db, turn, window):
        agent = feynman_agent if window == "feynman" else encyclopedia_agent
        return agent.stream(db, turn)

    replace(model_teaching, "prepare", lambda db, turn, window, profile, emit_preview=None: window)
    replace(model_teaching, "apply", apply)
    replace(model_teaching, "diagnose", lambda db, session, profile: report_agent.generate(db, session))
    replace(llm_client, "extract", lambda text, title, profile: llm_client.local_extract(text, title))
