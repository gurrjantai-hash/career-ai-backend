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

    def create_execution_plan(self, career_analysis_id: str) -> ExecutionPlanResponse:
        if not self.database_url:
            return ExecutionPlanResponse(
                success=False,
                message="DATABASE_URL is not configured."
            )

        try:
            connection = psycopg2.connect(self.database_url)
            cursor = connection.cursor(cursor_factory=RealDictCursor)

            existing_plan = self._get_existing_plan(cursor, career_analysis_id)
            if existing_plan:
                response = self._build_plan_response(
                    cursor,
                    existing_plan["id"],
                    message="Execution plan already exists."
                )
                connection.close()
                return response

            cursor.execute(
                """
                select
                    id,
                    user_id,
                    currentrole,
                    role_cluster,
                    target_roles,
                    roadmap_4_weeks
                from career_analyses
                where id = %s
                """,
                (career_analysis_id,)
            )

            analysis = cursor.fetchone()

            if not analysis:
                cursor.close()
                connection.close()
                return ExecutionPlanResponse(
                    success=False,
                    message="Career analysis not found."
                )

            roadmap = self._safe_json(analysis.get("roadmap_4_weeks"))
            target_roles = self._safe_json(analysis.get("target_roles"))

            if not isinstance(roadmap, dict) or not roadmap:
                cursor.close()
                connection.close()
                return ExecutionPlanResponse(
                    success=False,
                    message="No 4-week roadmap found for this career analysis."
                )

            target_role = None
            if isinstance(target_roles, list) and target_roles:
                target_role = str(target_roles[0])

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
                    analysis.get("user_id"),
                    analysis.get("id"),
                    target_role,
                    analysis.get("role_cluster"),
                )
            )

            plan_row = cursor.fetchone()
            execution_plan_id = plan_row["id"]

            task_rows = self._roadmap_to_tasks(roadmap)

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
                    )
                )

            connection.commit()

            response = self._build_plan_response(
                cursor,
                execution_plan_id,
                message="Execution plan created successfully."
            )

            cursor.close()
            connection.close()

            return response

        except Exception as e:
            print(f"Failed to create execution plan: {e}")
            return ExecutionPlanResponse(
                success=False,
                message=str(e)
            )

    def get_execution_plan_by_analysis(
        self,
        career_analysis_id: str
    ) -> ExecutionPlanResponse:
        if not self.database_url:
            return ExecutionPlanResponse(
                success=False,
                message="DATABASE_URL is not configured."
            )

        try:
            connection = psycopg2.connect(self.database_url)
            cursor = connection.cursor(cursor_factory=RealDictCursor)

            existing_plan = self._get_existing_plan(cursor, career_analysis_id)

            if not existing_plan:
                cursor.close()
                connection.close()
                return ExecutionPlanResponse(
                    success=False,
                    message="Execution plan not found."
                )

            response = self._build_plan_response(
                cursor,
                existing_plan["id"],
                message="Execution plan found."
            )

            cursor.close()
            connection.close()

            return response

        except Exception as e:
            print(f"Failed to fetch execution plan: {e}")
            return ExecutionPlanResponse(
                success=False,
                message=str(e)
            )

    def update_task_completion(
        self,
        task_id: str,
        is_completed: bool
    ) -> ExecutionPlanResponse:
        if not self.database_url:
            return ExecutionPlanResponse(
                success=False,
                message="DATABASE_URL is not configured."
            )

        try:
            connection = psycopg2.connect(self.database_url)
            cursor = connection.cursor(cursor_factory=RealDictCursor)

            cursor.execute(
                """
                select execution_plan_id
                from career_execution_tasks
                where id = %s
                """,
                (task_id,)
            )

            task = cursor.fetchone()

            if not task:
                cursor.close()
                connection.close()
                return ExecutionPlanResponse(
                    success=False,
                    message="Task not found."
                )

            execution_plan_id = task["execution_plan_id"]

            cursor.execute(
                """
                update career_execution_tasks
                set
                    is_completed = %s,
                    completed_at = case when %s = true then now() else null end,
                    updated_at = now()
                where id = %s
                """,
                (is_completed, is_completed, task_id)
            )

            self._recalculate_progress(cursor, execution_plan_id)

            connection.commit()

            response = self._build_plan_response(
                cursor,
                execution_plan_id,
                message="Task updated successfully."
            )

            cursor.close()
            connection.close()

            return response

        except Exception as e:
            print(f"Failed to update execution task: {e}")
            return ExecutionPlanResponse(
                success=False,
                message=str(e)
            )

    def _get_existing_plan(self, cursor, career_analysis_id: str):
        cursor.execute(
            """
            select id
            from career_execution_plans
            where career_analysis_id = %s
            """,
            (career_analysis_id,)
        )
        return cursor.fetchone()

    def _build_plan_response(
        self,
        cursor,
        execution_plan_id: str,
        message: str
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
            """,
            (execution_plan_id,)
        )

        plan = cursor.fetchone()

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
            (execution_plan_id,)
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
                )
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
            message=message
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
            (execution_plan_id,)
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
            (progress, execution_plan_id)
        )

    def _roadmap_to_tasks(self, roadmap: Dict[str, Any]) -> List[Dict[str, Any]]:
        tasks = []

        for week_key in self._sort_week_keys(list(roadmap.keys())):
            week_tasks = roadmap.get(week_key)

            if not isinstance(week_tasks, list):
                continue

            for index, task_text in enumerate(week_tasks, start=1):
                if not task_text:
                    continue

                tasks.append(
                    {
                        "week_key": week_key,
                        "task_order": index,
                        "task_text": str(task_text),
                    }
                )

        return tasks

    def _sort_week_keys(self, week_keys: List[str]) -> List[str]:
        def sort_key(value: str):
            digits = "".join(char for char in value if char.isdigit())
            return int(digits) if digits else 999

        return sorted(week_keys, key=sort_key)

    def _safe_json(self, value: Any):
        if isinstance(value, (dict, list)):
            return value

        if isinstance(value, str):
            try:
                return json.loads(value)
            except Exception:
                return value

        return value