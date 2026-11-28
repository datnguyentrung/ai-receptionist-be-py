"""Contract tests for Java-backed ADK FunctionTools."""

from app.agent.tools import admission_tools, center_info_tools, training_tools, tuition_tools


def test_tuition_packages_loads_list_then_details(monkeypatch):
    monkeypatch.setattr(tuition_tools, "list_resource", lambda path: [{"trainingPlanId": "plan-1", "name": "Thiếu nhi"}])
    monkeypatch.setattr(tuition_tools, "get_resource", lambda path: {"id": "plan-1", "name": "Thiếu nhi", "price": 500000})

    result = tuition_tools.search_tuition_packages("thiếu nhi")

    assert result["source"] == "java-api"
    assert result["resources"]["trainingPlans"] == [{"id": "plan-1", "name": "Thiếu nhi", "price": 500000}]


def test_training_schedule_combines_all_schedule_sources(monkeypatch):
    def list_resource(path, params=None):
        assert path in {"/class-schedules", "/course-schedules", "/class-sessions/calendar"}
        return [{"name": path}]

    monkeypatch.setattr(training_tools, "list_resource", list_resource)

    result = training_tools.search_class_schedule("tối")

    assert set(result["resources"]) == {"classSchedules", "courseSchedules", "calendarSessions"}


def test_admission_recommendation_uses_registration_catalog(monkeypatch):
    requested = []

    def get_resource(path):
        requested.append(path)
        return [{"name": "Thiếu nhi", "finalPrice": 500000}]

    monkeypatch.setattr(admission_tools, "get_resource", get_resource)

    result = admission_tools.recommend_class(age=8, goal="thể lực", schedule_preference="tối")

    assert result["resources"]["studentAge"] == 8
    assert requested == ["/training-plans/registration-catalog"]


def test_registration_process_and_curriculum_use_registration_catalog(monkeypatch):
    catalog = [{"name": "Thiếu nhi", "durationMonths": 3, "requirements": []}]
    monkeypatch.setattr(admission_tools, "get_resource", lambda path: catalog)
    monkeypatch.setattr(training_tools, "get_resource", lambda path: catalog)

    registration = admission_tools.search_registration_process("thiếu nhi")
    curriculum = training_tools.search_training_curriculum("thiếu nhi")

    assert registration["resources"]["registrationCatalog"] == catalog
    assert curriculum["resources"]["registrationCatalog"] == catalog


def test_contact_information_uses_branch_directory(monkeypatch):
    monkeypatch.setattr(center_info_tools, "list_resource", lambda path: [{"branchId": 1, "name": "Cơ sở A"}])
    monkeypatch.setattr(center_info_tools, "get_resource", lambda path: {"branchId": 1, "hotline": "0900"})

    result = center_info_tools.search_contact_information("cơ sở a")

    assert result["resources"]["branches"] == [{"branchId": 1, "hotline": "0900"}]


def test_coach_tool_combines_people_positions_and_assignments(monkeypatch):
    monkeypatch.setattr(center_info_tools, "list_resource", lambda path: [{"resource": path}])

    result = center_info_tools.search_coach_information("huấn luyện viên")

    assert set(result["resources"]) == {"persons", "positions", "courseStaffAssignments"}


def test_search_admission_knowledge_includes_graphrag_and_gracefully_handles_java_401(monkeypatch):
    fake_graph = {
        "source": "graphrag",
        "scopeKey": "admission_onboarding",
        "status": "ANSWERED",
        "passages": [{"text": "Đối với học viên dưới 6 tuổi, cần đạt yêu cầu sát hạch của CLB."}],
        "facts": [{"statement": "Điều kiện tiếp nhận học viên dưới 6 tuổi: cần đạt yêu cầu sát hạch."}],
    }
    monkeypatch.setattr(
        admission_tools,
        "retrieve_scope_knowledge_sync",
        lambda q, scope: fake_graph,
    )
    monkeypatch.setattr(
        admission_tools,
        "list_resource",
        lambda path: [{"sessionType": "MAIN", "sessionsPerWeek": 3}],
    )

    ok_res = admission_tools.search_admission_knowledge("trẻ em dưới 6 tuổi")
    assert ok_res["resources"]["graphKnowledge"] == fake_graph
    assert ok_res["resources"]["trainingPlanRequirements"] == [{"sessionType": "MAIN", "sessionsPerWeek": 3}]

    def _raise_401(path):
        raise admission_tools.JavaCatalogError(path, 401)

    monkeypatch.setattr(admission_tools, "list_resource", _raise_401)
    fallback_res = admission_tools.search_admission_knowledge("trẻ em dưới 6 tuổi")
    assert "error" not in fallback_res
    assert fallback_res["resources"]["graphKnowledge"] == fake_graph
    assert fallback_res["resources"]["trainingPlanRequirements"] == []

