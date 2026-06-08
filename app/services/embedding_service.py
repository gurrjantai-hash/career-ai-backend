import os
from typing import Any, Dict, List, Optional

import psycopg2
from psycopg2.extras import RealDictCursor
from sentence_transformers import SentenceTransformer
from dotenv import load_dotenv

load_dotenv()


class EmbeddingService:
    """
    Phase 2C Job Title Embedding Service.

    Purpose:
    - Generate embeddings for job titles
    - Store canonical role title embeddings
    - Search closest canonical role for messy user-entered job titles

    This should support RoleIntelligenceService, not replace it.
    """

    _model = None

    def __init__(self):
        self.database_url = os.getenv("DATABASE_URL")
        self.model_name = "all-MiniLM-L6-v2"

    def _get_model(self):
        if EmbeddingService._model is None:
            EmbeddingService._model = SentenceTransformer(self.model_name)

        return EmbeddingService._model

    def generate_embedding(self, text: str) -> List[float]:
        model = self._get_model()
        embedding = model.encode(text, normalize_embeddings=True)
        return embedding.tolist()

    def seed_role_title_embeddings(self) -> Dict[str, Any]:
        """
        Reads canonical_roles table and creates embeddings for:
        - role_name
        - common_titles

        This method is safe to rerun because it clears old embeddings first.
        """

        if not self.database_url:
            return {
                "success": False,
                "message": "DATABASE_URL not found"
            }

        connection = None
        cursor = None

        try:
            connection = psycopg2.connect(self.database_url)
            cursor = connection.cursor(cursor_factory=RealDictCursor)

            cursor.execute(
                """
                select
                  role_name,
                  role_family,
                  primary_cluster,
                  common_titles
                from canonical_roles
                order by role_name
                """
            )

            roles = cursor.fetchall()

            cursor.execute("delete from role_title_embeddings")

            inserted_count = 0

            for role in roles:
                role_name = role["role_name"]
                role_family = role.get("role_family")
                primary_cluster = role["primary_cluster"]
                common_titles = role.get("common_titles") or []

                title_items = []

                title_items.append(
                    {
                        "title_text": role_name,
                        "title_type": "canonical_role"
                    }
                )

                if isinstance(common_titles, list):
                    for title in common_titles:
                        if title and str(title).strip():
                            title_items.append(
                                {
                                    "title_text": str(title).strip(),
                                    "title_type": "common_title"
                                }
                            )

                for item in title_items:
                    title_text = item["title_text"]
                    title_type = item["title_type"]

                    embedding = self.generate_embedding(title_text)
                    embedding_text = "[" + ",".join(str(x) for x in embedding) + "]"

                    cursor.execute(
                        """
                        insert into role_title_embeddings (
                          canonical_role,
                          role_family,
                          primary_cluster,
                          title_text,
                          title_type,
                          embedding
                        )
                        values (%s, %s, %s, %s, %s, %s::vector)
                        """,
                        (
                            role_name,
                            role_family,
                            primary_cluster,
                            title_text,
                            title_type,
                            embedding_text
                        )
                    )

                    inserted_count += 1

            connection.commit()

            return {
                "success": True,
                "roles_count": len(roles),
                "embeddings_inserted": inserted_count
            }

        except Exception as e:
            if connection:
                connection.rollback()

            return {
                "success": False,
                "message": str(e)
            }

        finally:
            if cursor:
                cursor.close()

            if connection:
                connection.close()

    def find_closest_role(
        self,
        input_role: str,
        limit: int = 5
    ) -> List[Dict[str, Any]]:
        """
        Finds closest canonical roles for user-entered job title.
        """

        if not self.database_url:
            return []

        if not input_role or not input_role.strip():
            return []

        query_embedding = self.generate_embedding(input_role.strip())
        embedding_text = "[" + ",".join(str(x) for x in query_embedding) + "]"

        connection = None
        cursor = None

        try:
            connection = psycopg2.connect(self.database_url)
            cursor = connection.cursor(cursor_factory=RealDictCursor)

            cursor.execute(
                """
                select
                  canonical_role,
                  role_family,
                  primary_cluster,
                  title_text,
                  title_type,
                  1 - (embedding <=> %s::vector) as similarity
                from role_title_embeddings
                order by embedding <=> %s::vector
                limit %s
                """,
                (
                    embedding_text,
                    embedding_text,
                    limit
                )
            )

            rows = cursor.fetchall()

            results = []

            for row in rows:
                results.append(
                    {
                        "canonical_role": row["canonical_role"],
                        "role_family": row["role_family"],
                        "primary_cluster": row["primary_cluster"],
                        "matched_title": row["title_text"],
                        "title_type": row["title_type"],
                        "similarity": round(float(row["similarity"]), 4)
                    }
                )

            return results

        except Exception as e:
            print(f"Embedding search failed: {e}")
            return []

        finally:
            if cursor:
                cursor.close()

            if connection:
                connection.close()