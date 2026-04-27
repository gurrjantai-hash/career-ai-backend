import os
import json
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()


class AIService:

    def __init__(self):
        self.client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    def get_json_response(self, prompt: str) -> dict:
        response = self.client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": "You return only valid JSON. No markdown. No explanation."
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            temperature=0.3
        )

        content = response.choices[0].message.content

        try:
            return json.loads(content)
        except json.JSONDecodeError:
            raise ValueError(f"AI returned invalid JSON: {content}")

    def classify_role_cluster(self, current_role: str, skills: list[str]) -> str:
        prompt = f"""
Classify this professional into exactly one role cluster.

Allowed clusters:
- Backend Engineering
- Frontend Engineering
- Data/AI
- Marketing
- Sales
- Finance
- Operations

Role: {current_role}
Skills: {skills}

Return only the cluster name.
"""

        response = self.client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "Return only one cluster name."},
                {"role": "user", "content": prompt}
            ],
            temperature=0
        )

        return response.choices[0].message.content.strip()