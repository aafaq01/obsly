"""Session authentication for the web UI.

Django's session, driven by our own endpoints instead of the admin's login form. The admin
remains available but is no longer on the path to using the product.

Sessions rather than tokens on purpose: the API and the UI are same-origin behind one nginx,
so a HttpOnly session cookie cannot be read by injected script, while a token in localStorage
can. The cost is CSRF, which Django already handles.
"""

from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView


def signup_is_open() -> bool:
    """Registration is open until the install has an owner, then it closes.

    An observability platform holds production stack traces, SQL statements and whatever PII
    survived scrubbing. An endpoint that hands out accounts for that, permanently and to
    anyone who can reach the URL, is not a sign-up form — it is a way to read somebody's
    production.

    So the window is the one moment it is genuinely needed: a fresh install has no users and
    no other way in short of `createsuperuser` on the container. The first account closes it.

    Leaving it open afterwards is possible and has to be typed out — a setting somebody chose,
    not a default they inherited.
    """
    return not User.objects.exists() or settings.OBSLY_ALLOW_SIGNUP


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
            # Whether signing up is possible belongs on the unauthenticated response: the
            # sign-in screen has to know whether to offer it, and a fresh install has to know
            # to show the register form instead of a login form nobody can satisfy.
            return Response(
                {"authenticated": False, "username": None, "signup_open": signup_is_open()}
            )
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


class RegisterView(APIView):
    """Create the first account, and close the door behind it."""

    permission_classes = [AllowAny]
    throttle_classes = [LoginRateThrottle]

    def post(self, request: Request) -> Response:
        payload = request.data if isinstance(request.data, dict) else {}
        username = str(payload.get("username", "")).strip()
        password = str(payload.get("password", ""))

        if not signup_is_open():
            return Response(
                {"detail": "Registration is closed. Ask an administrator for an account."},
                status=status.HTTP_403_FORBIDDEN,
            )

        if not username or not password:
            return Response(
                {"detail": "Username and password are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Django's own validators, so the rules match the ones the admin and any management
        # command already enforce. A second, weaker set here would be the one people meet.
        try:
            validate_password(password, User(username=username))
        except ValidationError as exc:
            return Response({"detail": " ".join(exc.messages)}, status=status.HTTP_400_BAD_REQUEST)

        # The first account owns the install: there is nobody to grant it anything, and an
        # instance whose only user cannot reach settings is an instance nobody can administer.
        first = not User.objects.exists()

        try:
            with transaction.atomic():
                user = User.objects.create_user(
                    username=username, password=password, is_staff=first, is_superuser=first
                )
        except IntegrityError:
            # Between the check and the insert somebody else took it. The unique constraint is
            # what actually decides, so this is the answer rather than a 500.
            return Response(
                {"detail": "That username is taken."}, status=status.HTTP_400_BAD_REQUEST
            )

        login(request, user)

        return Response(
            {"authenticated": True, "username": user.get_username(), "is_staff": user.is_staff},
            status=status.HTTP_201_CREATED,
        )


class LogoutView(APIView):
    permission_classes = [AllowAny]

    def post(self, request: Request) -> Response:
        # Flushes the session server-side; clearing only the cookie would leave a session that
        # still authenticates anyone who kept a copy of it.
        logout(request)
        return Response({"authenticated": False})
