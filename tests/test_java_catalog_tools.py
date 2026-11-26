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


def test_admission_recommendation_uses_all_declared_java_resources(monkeypatch):
    requested = []

    def list_resource(path, params=None):
        requested.append(path)
        return [{"name": path}]

    monkeypatch.setattr(admission_tools, "list_resource", list_resource)

    result = admission_tools.recommend_class(age=8, goal="thể lực", schedule_preference="tối")

    assert result["resources"]["studentAge"] == 8
    assert requested == ["/courses", "/course-schedules", "/training-plans", "/training-plan-requirements"]


def test_coach_tool_combines_people_positions_and_assignments(monkeypatch):
    monkeypatch.setattr(center_info_tools, "list_resource", lambda path: [{"resource": path}])

    result = center_info_tools.search_coach_information("huấn luyện viên")

    assert set(result["resources"]) == {"persons", "positions", "courseStaffAssignments"}
