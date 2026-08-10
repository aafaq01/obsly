"""Session authentication for the web UI.

Django's session, driven by our own endpoints instead of the admin's login form. The admin
remains available but is no longer on the path to using the product.

Sessions rather than tokens on purpose: the API and the UI are same-origin behind one nginx,
so a HttpOnly session cookie cannot be read by injected script, while a token in localStorage
can. The cost is CSRF, which Django already handles.
"""

from django.contrib.auth import authenticate, login, logout
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView


class LoginRateThrottle(AnonRateThrottle):
    """Rate limit by IP, not by username.

    Throttling per username lets an attacker lock a real user out by failing their login on
    purpose, and does nothing about spraying one password across many accounts.
    """

    scope = "login"


@method_decorator(ensure_csrf_cookie, name="get")
class SessionView(APIView):
    """Who am I, and here is a CSRF cookie.

    AllowAny so an anonymous caller still gets the cookie — the login POST needs a token, and a
    403 before the view runs would never set one, leaving a first-time visitor unable to sign in
    at all.
    """

    permission_classes = [AllowAny]

    def get(self, request: Request) -> Response:
        if request.user is None or not request.user.is_authenticated:
            return Response({"authenticated": False, "username": None})
        return Response(
            {
                "authenticated": True,
                "username": request.user.get_username(),
                "is_staff": request.user.is_staff,
            }
        )


class LoginView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [LoginRateThrottle]

    def post(self, request: Request) -> Response:
        payload = request.data if isinstance(request.data, dict) else {}
        username = str(payload.get("username", "")).strip()
        password = str(payload.get("password", ""))

        if not username or not password:
            return Response(
                {"detail": "Username and password are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user = authenticate(request, username=username, password=password)
        if user is None:
            # One message for "no such user" and "wrong password". Distinguishing them turns
            # the login form into a way to enumerate who has an account.
            return Response(
                {"detail": "Incorrect username or password."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        # Cycles the session key, so a session fixated before login is worthless after it.
        login(request, user)

        return Response({"authenticated": True, "username": user.get_username()})


class LogoutView(APIView):
    permission_classes = [AllowAny]

    def post(self, request: Request) -> Response:
        # Flushes the session server-side; clearing only the cookie would leave a session that
        # still authenticates anyone who kept a copy of it.
        logout(request)
        return Response({"authenticated": False})
