import os
from typing import Any, Dict, Optional

import httpx
import jwt
from dotenv import load_dotenv
from jwt import PyJWKClient
from jwt.exceptions import (
    DecodeError,
    ExpiredSignatureError,
    InvalidAudienceError,
    InvalidIssuerError,
    InvalidTokenError,
)

from app.models import AuthenticatedUser


load_dotenv()


class AuthenticationError(Exception):
    pass


class AuthenticationService:
    SUPPORTED_ASYMMETRIC_ALGORITHMS = {
        "RS256",
        "ES256",
        "EdDSA",
    }

    def __init__(self):
        self.supabase_url = (
            os.getenv("SUPABASE_URL", "")
            .strip()
            .rstrip("/")
        )

        self.supabase_api_key = (
            os.getenv("SUPABASE_PUBLISHABLE_KEY")
            or os.getenv("SUPABASE_ANON_KEY")
            or ""
        ).strip()

        if self.supabase_url:
            self.issuer = f"{self.supabase_url}/auth/v1"
            self.jwks_url = (
                f"{self.supabase_url}"
                "/auth/v1/.well-known/jwks.json"
            )
            self.jwks_client: Optional[PyJWKClient] = PyJWKClient(
                self.jwks_url
            )
        else:
            self.issuer = ""
            self.jwks_url = ""
            self.jwks_client = None

    def authenticate_access_token(
        self,
        access_token: str,
    ) -> AuthenticatedUser:
        self._validate_configuration()

        if not access_token or not access_token.strip():
            raise AuthenticationError(
                "Access token is missing."
            )

        token = access_token.strip()

        try:
            header = jwt.get_unverified_header(token)
        except DecodeError as exc:
            raise AuthenticationError(
                "Invalid access token."
            ) from exc

        algorithm = header.get("alg")

        if algorithm in self.SUPPORTED_ASYMMETRIC_ALGORITHMS:
            return self._authenticate_with_jwks(
                token=token,
                algorithm=algorithm,
            )

        if algorithm == "HS256":
            return self._authenticate_with_auth_server(token)

        raise AuthenticationError(
            f"Unsupported JWT signing algorithm: {algorithm}"
        )

    def _authenticate_with_jwks(
        self,
        token: str,
        algorithm: str,
    ) -> AuthenticatedUser:
        if not self.jwks_client:
            raise AuthenticationError(
                "JWKS client is not configured."
            )

        try:
            signing_key = self.jwks_client.get_signing_key_from_jwt(
                token
            )

            claims: Dict[str, Any] = jwt.decode(
                token,
                signing_key.key,
                algorithms=[algorithm],
                audience="authenticated",
                issuer=self.issuer,
                options={
                    "require": [
                        "exp",
                        "iss",
                        "sub",
                    ]
                },
            )

        except ExpiredSignatureError as exc:
            raise AuthenticationError(
                "Access token has expired."
            ) from exc

        except InvalidAudienceError as exc:
            raise AuthenticationError(
                "Invalid access token audience."
            ) from exc

        except InvalidIssuerError as exc:
            raise AuthenticationError(
                "Invalid access token issuer."
            ) from exc

        except InvalidTokenError as exc:
            raise AuthenticationError(
                "Invalid access token."
            ) from exc

        user_id = claims.get("sub")
        role = claims.get("role")

        if not user_id:
            raise AuthenticationError(
                "Access token does not contain a user ID."
            )

        if role != "authenticated":
            raise AuthenticationError(
                "Authenticated user token is required."
            )

        return AuthenticatedUser(
            user_id=str(user_id),
            email=claims.get("email"),
            role=str(role),
        )

    def _authenticate_with_auth_server(
        self,
        token: str,
    ) -> AuthenticatedUser:
        try:
            response = httpx.get(
                f"{self.supabase_url}/auth/v1/user",
                headers={
                    "apikey": self.supabase_api_key,
                    "Authorization": f"Bearer {token}",
                },
                timeout=5.0,
            )

        except httpx.RequestError as exc:
            raise AuthenticationError(
                "Authentication service is currently unavailable."
            ) from exc

        if response.status_code != 200:
            raise AuthenticationError(
                "Invalid or expired access token."
            )

        try:
            user_data = response.json()
        except ValueError as exc:
            raise AuthenticationError(
                "Invalid authentication response."
            ) from exc

        user_id = user_data.get("id")

        if not user_id:
            raise AuthenticationError(
                "Authenticated user ID was not returned."
            )

        return AuthenticatedUser(
            user_id=str(user_id),
            email=user_data.get("email"),
            role="authenticated",
        )

    def _validate_configuration(self) -> None:
        if not self.supabase_url:
            raise AuthenticationError(
                "SUPABASE_URL is not configured."
            )

        if not self.supabase_api_key:
            raise AuthenticationError(
                "SUPABASE_PUBLISHABLE_KEY or "
                "SUPABASE_ANON_KEY is not configured."
            )