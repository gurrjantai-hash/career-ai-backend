import os
import json
from typing import Any, Dict, List, Optional

import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

from app.models import ExecutionPlanResponse, ExecutionTaskResponse


load_dotenv()


class ExecutionService:
    def __init__(self):
        self.database_url = os.getenv("DATABASE_URL")

    def create_execution_plan(
        self,
        career_analysis_id: str,
        user_id: str,
        target_role: Optional[str] = None,
    ) -> ExecutionPlanResponse:
        if not self.database_url:
            return ExecutionPlanResponse(
                success=False,
                message="DATABASE_URL is not configured.",
            )

        if not user_id:
            return ExecutionPlanResponse(
                success=False,
                message="Authenticated user_id is missing.",
            )

        try:
            connection = psycopg2.connect(self.database_url)
            cursor = connection.cursor(cursor_factory=RealDictCursor)

            analysis = self._get_analysis_for_user(
                cursor=cursor,
                career_analysis_id=career_analysis_id,
                user_id=user_id,
            )

            if not analysis:
                cursor.close()
                connection.close()
                return ExecutionPlanResponse(
                    success=False,
                    message="Career analysis not found for current user.",
                )

            target_roles = self._safe_json(analysis.get("target_roles"))
            selected_target_role = self._safe_text(
                target_role or self._first_text(target_roles) or analysis.get("currentrole"),
                "your target role",
            )

            existing_plan = self._get_existing_plan(
                cursor=cursor,
                career_analysis_id=career_analysis_id,
                user_id=user_id,
                target_role=selected_target_role,
            )

            if existing_plan:
                response = self._build_plan_response(
                    cursor=cursor,
                    execution_plan_id=existing_plan["id"],
                    user_id=user_id,
                    message="Execution plan already exists for this target role.",
                )
                cursor.close()
                connection.close()
                return response

            task_rows = self._analysis_to_action_tasks(
                analysis=analysis,
                target_role=selected_target_role,
            )

            if not task_rows:
                cursor.close()
                connection.close()
                return ExecutionPlanResponse(
                    success=False,
                    message="Unable to create execution tasks for this career analysis.",
                )

            cursor.execute(
                """
                insert into career_execution_plans (
                    user_id,
                    career_analysis_id,
                    target_role,
                    role_cluster,
                    status,
                    progress_percentage
                )
                values (%s, %s, %s, %s, 'active', 0)
                returning id
                """,
                (
                    user_id,
                    analysis.get("id"),
                    selected_target_role,
                    analysis.get("role_cluster"),
                ),
            )

            plan_row = cursor.fetchone()
            execution_plan_id = plan_row["id"]

            for task in task_rows:
                cursor.execute(
                    """
                    insert into career_execution_tasks (
                        execution_plan_id,
                        week_key,
                        task_order,
                        task_text,
                        is_completed
                    )
                    values (%s, %s, %s, %s, false)
                    """,
                    (
                        execution_plan_id,
                        task["week_key"],
                        task["task_order"],
                        task["task_text"],
                    ),
                )

            connection.commit()

            response = self._build_plan_response(
                cursor=cursor,
                execution_plan_id=execution_plan_id,
                user_id=user_id,
                message="Execution plan created successfully.",
            )

            cursor.close()
            connection.close()

            return response

        except Exception as e:
            print(f"Failed to create execution plan: {e}")
            return ExecutionPlanResponse(
                success=False,
                message=str(e),
            )

    def get_execution_plan_by_analysis(
        self,
        career_analysis_id: str,
        user_id: str,
        target_role: Optional[str] = None,
    ) -> ExecutionPlanResponse:
        if not self.database_url:
            return ExecutionPlanResponse(
                success=False,
                message="DATABASE_URL is not configured.",
            )

        if not user_id:
            return ExecutionPlanResponse(
                success=False,
                message="Authenticated user_id is missing.",
            )

        try:
            connection = psycopg2.connect(self.database_url)
            cursor = connection.cursor(cursor_factory=RealDictCursor)

            existing_plan = self._get_existing_plan(
                cursor=cursor,
                career_analysis_id=career_analysis_id,
                user_id=user_id,
                target_role=target_role,
            )

            if not existing_plan:
                cursor.close()
                connection.close()
                return ExecutionPlanResponse(
                    success=False,
                    message="Execution plan not found for current user and target role.",
                )

            response = self._build_plan_response(
                cursor=cursor,
                execution_plan_id=existing_plan["id"],
                user_id=user_id,
                message="Execution plan found.",
            )

            cursor.close()
            connection.close()

            return response

        except Exception as e:
            print(f"Failed to fetch execution plan: {e}")
            return ExecutionPlanResponse(
                success=False,
                message=str(e),
            )

    def get_execution_plans_by_analysis(
        self,
        career_analysis_id: str,
        user_id: str,
    ) -> List[ExecutionPlanResponse]:
        if not self.database_url or not user_id:
            return []

        try:
            connection = psycopg2.connect(self.database_url)
            cursor = connection.cursor(cursor_factory=RealDictCursor)

            cursor.execute(
                """
                select id
                from career_execution_plans
                where career_analysis_id = %s
                  and user_id = %s
                order by updated_at desc nulls last, created_at desc
                """,
                (career_analysis_id, user_id),
            )

            plan_rows = cursor.fetchall()
            responses = []

            for plan_row in plan_rows:
                response = self._build_plan_response(
                    cursor=cursor,
                    execution_plan_id=plan_row["id"],
                    user_id=user_id,
                    message="Execution plan found.",
                )

                if response.success:
                    responses.append(response)

            cursor.close()
            connection.close()

            return responses

        except Exception as e:
            print(f"Failed to fetch execution plans: {e}")
            return []

    def update_task_completion(
        self,
        task_id: str,
        is_completed: bool,
        user_id: str,
    ) -> ExecutionPlanResponse:
        if not self.database_url:
            return ExecutionPlanResponse(
                success=False,
                message="DATABASE_URL is not configured.",
            )

        if not user_id:
            return ExecutionPlanResponse(
                success=False,
                message="Authenticated user_id is missing.",
            )

        try:
            connection = psycopg2.connect(self.database_url)
            cursor = connection.cursor(cursor_factory=RealDictCursor)

            cursor.execute(
                """
                select
                    cet.id,
                    cet.execution_plan_id,
                    cet.week_key,
                    cet.is_completed
                from career_execution_tasks cet
                join career_execution_plans cep
                  on cep.id = cet.execution_plan_id
                where cet.id = %s
                  and cep.user_id = %s
                """,
                (task_id, user_id),
            )

            task = cursor.fetchone()

            if not task:
                cursor.close()
                connection.close()
                return ExecutionPlanResponse(
                    success=False,
                    message="Task not found for current user.",
                )

            execution_plan_id = task["execution_plan_id"]

            cursor.execute(
                """
                select
                    id,
                    week_key,
                    is_completed
                from career_execution_tasks
                where execution_plan_id = %s
                """,
                (execution_plan_id,),
            )

            all_tasks = cursor.fetchall()
            current_week_number = self._week_number(task["week_key"])

            if is_completed:
                incomplete_previous_tasks = [
                    row for row in all_tasks
                    if self._week_number(row["week_key"]) < current_week_number
                    and not row["is_completed"]
                ]

                if incomplete_previous_tasks:
                    cursor.close()
                    connection.close()
                    return ExecutionPlanResponse(
                        success=False,
                        message="Please complete previous week tasks before unlocking this week.",
                    )
            else:
                completed_later_tasks = [
                    row for row in all_tasks
                    if self._week_number(row["week_key"]) > current_week_number
                    and row["is_completed"]
                ]

                if completed_later_tasks:
                    cursor.close()
                    connection.close()
                    return ExecutionPlanResponse(
                        success=False,
                        message="You cannot uncomplete this task because later week tasks are already completed.",
                    )

            cursor.execute(
                """
                update career_execution_tasks
                set
                    is_completed = %s,
                    completed_at = case when %s = true then now() else null end,
                    updated_at = now()
                where id = %s
                """,
                (is_completed, is_completed, task_id),
            )

            self._recalculate_progress(cursor, execution_plan_id)

            connection.commit()

            response = self._build_plan_response(
                cursor=cursor,
                execution_plan_id=execution_plan_id,
                user_id=user_id,
                message="Task updated successfully.",
            )

            cursor.close()
            connection.close()

            return response

        except Exception as e:
            print(f"Failed to update execution task: {e}")
            return ExecutionPlanResponse(
                success=False,
                message=str(e),
            )

    def _get_analysis_for_user(
        self,
        cursor,
        career_analysis_id: str,
        user_id: str,
    ):
        cursor.execute(
            """
            select
                id,
                currentrole,
                role_cluster,
                target_roles,
                top_skill_gaps,
                resume_suggestions,
                skill_premium_insights,
                growth_paths,
                goal
            from career_analyses
            where id = %s
              and user_id = %s
            """,
            (career_analysis_id, user_id),
        )

        return cursor.fetchone()

    def _get_existing_plan(
        self,
        cursor,
        career_analysis_id: str,
        user_id: str,
        target_role: Optional[str] = None,
    ):
        if target_role and str(target_role).strip():
            cursor.execute(
                """
                select id
                from career_execution_plans
                where career_analysis_id = %s
                  and user_id = %s
                  and lower(trim(coalesce(target_role, ''))) = lower(trim(%s))
                order by updated_at desc nulls last, created_at desc
                limit 1
                """,
                (career_analysis_id, user_id, str(target_role).strip()),
            )
            return cursor.fetchone()

        cursor.execute(
            """
            select id
            from career_execution_plans
            where career_analysis_id = %s
              and user_id = %s
            order by updated_at desc nulls last, created_at desc
            limit 1
            """,
            (career_analysis_id, user_id),
        )
        return cursor.fetchone()

    def _build_plan_response(
        self,
        cursor,
        execution_plan_id: str,
        user_id: str,
        message: str,
    ) -> ExecutionPlanResponse:
        cursor.execute(
            """
            select
                id,
                career_analysis_id,
                target_role,
                role_cluster,
                progress_percentage
            from career_execution_plans
            where id = %s
              and user_id = %s
            """,
            (execution_plan_id, user_id),
        )

        plan = cursor.fetchone()

        if not plan:
            return ExecutionPlanResponse(
                success=False,
                message="Execution plan not found for current user.",
            )

        cursor.execute(
            """
            select
                id,
                week_key,
                task_order,
                task_text,
                is_completed,
                completed_at
            from career_execution_tasks
            where execution_plan_id = %s
            order by week_key, task_order
            """,
            (execution_plan_id,),
        )

        rows = cursor.fetchall()

        tasks = [
            ExecutionTaskResponse(
                task_id=str(row["id"]),
                week_key=row["week_key"],
                task_order=int(row["task_order"]),
                task_text=row["task_text"],
                is_completed=bool(row["is_completed"]),
                completed_at=(
                    row["completed_at"].isoformat()
                    if row.get("completed_at")
                    else None
                ),
            )
            for row in rows
        ]

        return ExecutionPlanResponse(
            success=True,
            execution_plan_id=str(plan["id"]),
            career_analysis_id=str(plan["career_analysis_id"]),
            target_role=plan.get("target_role"),
            role_cluster=plan.get("role_cluster"),
            progress_percentage=float(plan.get("progress_percentage") or 0),
            tasks=tasks,
            message=message,
        )

    def _recalculate_progress(self, cursor, execution_plan_id: str) -> None:
        cursor.execute(
            """
            select
                count(*) as total_tasks,
                count(*) filter (where is_completed = true) as completed_tasks
            from career_execution_tasks
            where execution_plan_id = %s
            """,
            (execution_plan_id,),
        )

        counts = cursor.fetchone()
        total_tasks = int(counts["total_tasks"] or 0)
        completed_tasks = int(counts["completed_tasks"] or 0)

        progress = 0
        if total_tasks > 0:
            progress = round((completed_tasks * 100.0) / total_tasks, 2)

        cursor.execute(
            """
            update career_execution_plans
            set
                progress_percentage = %s,
                updated_at = now()
            where id = %s
            """,
            (progress, execution_plan_id),
        )

    def _analysis_to_action_tasks(
        self,
        analysis: Dict[str, Any],
        target_role: str,
    ) -> List[Dict[str, Any]]:
        current_role = self._safe_text(
            analysis.get("currentrole"),
            "your current role",
        )
        role_cluster = self._safe_text(
            analysis.get("role_cluster"),
            "your role cluster",
        )
        goal = self._safe_text(
            analysis.get("goal"),
            "career growth",
        )

        target_roles = self._normalize_list(
            self._safe_json(analysis.get("target_roles"))
        )
        skill_gaps = self._normalize_list(
            self._safe_json(analysis.get("top_skill_gaps"))
        )
        resume_suggestions = self._normalize_list(
            self._safe_json(analysis.get("resume_suggestions"))
        )
        growth_paths = self._safe_json(analysis.get("growth_paths"))
        skill_premium_insights = self._safe_json(
            analysis.get("skill_premium_insights")
        )

        premium_skills = []
        if isinstance(skill_premium_insights, list):
            for item in skill_premium_insights:
                if isinstance(item, dict):
                    skill_name = item.get("skill_name")
                    if skill_name:
                        premium_skills.append(str(skill_name))

        all_skills = self._dedupe_keep_order(premium_skills + skill_gaps)

        primary_skill = (
            all_skills[0]
            if len(all_skills) > 0
            else "your highest-priority skill gap"
        )
        secondary_skill = (
            all_skills[1]
            if len(all_skills) > 1
            else "one supporting market skill"
        )
        third_skill = (
            all_skills[2]
            if len(all_skills) > 2
            else "one interview-critical topic"
        )

        target_role = self._safe_text(
            target_role or self._first_text(target_roles),
            "your target role",
        )

        target_role_options = self._format_skill_phrase(
            target_roles[:3],
            fallback=target_role,
        )
        skill_focus = self._format_skill_phrase(
            all_skills[:3],
            fallback=primary_skill,
        )

        resume_action = (
            self._truncate_text(resume_suggestions[0], max_length=150)
            if resume_suggestions
            else "Rewrite your profile summary and top 3 bullets for the target role."
        )

        growth_path_hint = ""
        if isinstance(growth_paths, list) and growth_paths:
            first_path = growth_paths[0]
            if isinstance(first_path, dict) and first_path.get("path_name"):
                growth_path_hint = str(first_path.get("path_name"))

        tasks_by_week = {
            "week_1": [
                (
                    f"Target clarity: choose {target_role} as the primary target "
                    f"from {target_role_options} and write a 5-line reason for this move."
                ),
                (
                    f"Skill execution: complete one focused learning sprint on "
                    f"{primary_skill} and prepare short interview notes."
                ),
                (
                    f"Resume action: {resume_action}"
                ),
                (
                    "Market action: shortlist 15 relevant jobs and capture the "
                    "repeated skills, tools and responsibilities you see."
                ),
            ],
            "week_2": [
                (
                    f"Proof action: build a small practical proof around "
                    f"{primary_skill} and {secondary_skill}."
                ),
                (
                    "Portfolio action: push the proof work to GitHub or prepare "
                    "a short case-study document explaining the problem and approach."
                ),
                (
                    "Resume action: add the proof work into your resume with "
                    "impact-focused bullets instead of only listing skills."
                ),
                (
                    "Networking action: send 5 targeted LinkedIn messages to "
                    f"people working in {target_role} or {role_cluster} roles."
                ),
            ],
            "week_3": [
                (
                    f"Interview action: prepare answers for {primary_skill}, "
                    f"{secondary_skill} and {third_skill} with real project examples."
                ),
                (
                    "Application action: apply to 8-10 relevant roles using the "
                    "updated resume and track company, role, date and response status."
                ),
                (
                    "Profile action: update LinkedIn/Naukri headline, skills and "
                    "summary to match the target role keywords."
                ),
                (
                    "Feedback action: ask one senior colleague, mentor or peer to "
                    "review your resume/profile positioning."
                ),
            ],
            "week_4": [
                (
                    "Mock interview action: complete at least 2 mock interviews "
                    "or recorded self-practice sessions for the target role."
                ),
                (
                    "Pipeline action: follow up on previous applications and add "
                    "10 more targeted applications if response rate is low."
                ),
                (
                    f"Positioning action: prepare a 60-second pitch connecting your "
                    f"{current_role} background to {target_role}."
                ),
                (
                    f"Review action: compare progress against the goal of {goal} "
                    "and decide the next 4-week focus based on gaps still remaining."
                ),
            ],
        }

        if growth_path_hint:
            tasks_by_week["week_1"].append(
                f"Direction check: validate whether the '{growth_path_hint}' path "
                "still feels aligned after reviewing market roles."
            )

        tasks = []
        for week_key in ["week_1", "week_2", "week_3", "week_4"]:
            for index, task_text in enumerate(tasks_by_week[week_key], start=1):
                tasks.append(
                    {
                        "week_key": week_key,
                        "task_order": index,
                        "task_text": task_text,
                    }
                )

        return tasks

    def _normalize_list(self, value: Any) -> List[str]:
        if not isinstance(value, list):
            return []

        normalized = []
        for item in value:
            if isinstance(item, str) and item.strip():
                normalized.append(item.strip())
            elif isinstance(item, dict):
                label = (
                    item.get("target_role")
                    or item.get("skill_name")
                    or item.get("path_name")
                    or item.get("name")
                )
                if label:
                    normalized.append(str(label).strip())

        return self._dedupe_keep_order(normalized)

    def _first_text(self, value: Any) -> str:
        normalized = self._normalize_list(value)
        return normalized[0] if normalized else ""

    def _dedupe_keep_order(self, values: List[str]) -> List[str]:
        seen = set()
        result = []

        for value in values:
            cleaned = str(value).strip()
            if not cleaned:
                continue

            key = cleaned.lower()
            if key in seen:
                continue

            seen.add(key)
            result.append(cleaned)

        return result

    def _format_skill_phrase(
        self,
        values: List[str],
        fallback: str,
    ) -> str:
        cleaned = self._dedupe_keep_order(values)

        if not cleaned:
            return fallback

        if len(cleaned) == 1:
            return cleaned[0]

        if len(cleaned) == 2:
            return f"{cleaned[0]} and {cleaned[1]}"

        return f"{cleaned[0]}, {cleaned[1]} and {cleaned[2]}"

    def _safe_text(self, value: Any, fallback: str) -> str:
        if value is None:
            return fallback

        text = str(value).strip()
        return text if text else fallback

    def _truncate_text(self, value: str, max_length: int = 150) -> str:
        text = str(value).strip()
        if len(text) <= max_length:
            return text

        return text[: max_length - 3].rstrip() + "..."

    def _sort_week_keys(self, week_keys: List[str]) -> List[str]:
        def sort_key(value: str):
            digits = "".join(char for char in value if char.isdigit())
            return int(digits) if digits else 999

        return sorted(week_keys, key=sort_key)

    def _week_number(self, week_key: str) -> int:
        digits = "".join(char for char in str(week_key) if char.isdigit())
        return int(digits) if digits else 999

    def _safe_json(self, value: Any):
        if isinstance(value, (dict, list)):
            return value

        if isinstance(value, str):
            try:
                return json.loads(value)
            except Exception:
                return value

        return value
