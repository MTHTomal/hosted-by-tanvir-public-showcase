import re

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.views import LOGIN_ERROR_MESSAGE


User = get_user_model()


class AuthenticationSecurityTests(TestCase):
    def setUp(self):
        self.password = "testpass123"
        self.user = User.objects.create_user(
            username="authsecurity",
            email="authsecurity@example.com",
            password=self.password,
        )
        self.login_url = reverse("accounts:login")
        self.logout_url = reverse("accounts:logout")

    def _login(self, *, email=None, password=None, next_url=None, post_next=None, secure=False):
        url = self.login_url
        if next_url is not None:
            url = f"{url}?next={next_url}"
        data = {
            "email": email or self.user.email,
            "password": password or self.password,
        }
        if post_next is not None:
            data["next"] = post_next
        return self.client.post(
            url,
            data,
            secure=secure,
        )

    def _message_text(self, response):
        return [str(message) for message in response.context["messages"]]

    def test_safe_internal_relative_next_redirect_is_preserved(self):
        response = self._login(next_url="/accounts/dashboard/")

        self.assertRedirects(response, reverse("accounts:dashboard"))

    def test_external_https_next_redirect_falls_back_to_home(self):
        response = self._login(next_url="https://evil.example/collect")

        self.assertRedirects(response, reverse("tournament:home"))

    def test_protocol_relative_next_redirect_falls_back_to_home(self):
        response = self._login(next_url="//evil.example/collect")

        self.assertRedirects(response, reverse("tournament:home"))

    def test_unsafe_scheme_next_redirect_falls_back_to_home(self):
        response = self._login(next_url="javascript:alert(1)")

        self.assertRedirects(response, reverse("tournament:home"))

    def test_missing_next_redirect_falls_back_to_home(self):
        response = self._login()

        self.assertRedirects(response, reverse("tournament:home"))

    def test_invalid_next_redirect_falls_back_to_home(self):
        response = self._login(next_url="https://evil.example/collect")

        self.assertRedirects(response, reverse("tournament:home"))

    def test_valid_post_next_redirect_is_preserved(self):
        response = self._login(post_next=reverse("accounts:dashboard"))

        self.assertRedirects(response, reverse("accounts:dashboard"))

    def test_unsafe_post_next_redirect_falls_back_to_home(self):
        response = self._login(post_next="https://evil.example/collect")

        self.assertRedirects(response, reverse("tournament:home"))

    def test_secure_request_requires_https_for_absolute_same_host_next(self):
        https_response = self._login(
            next_url="https://testserver/accounts/dashboard/",
            secure=True,
        )
        self.assertEqual(https_response.status_code, 302)
        self.assertEqual(
            https_response["Location"],
            "https://testserver/accounts/dashboard/",
        )

        http_response = self._login(
            next_url="http://testserver/accounts/dashboard/",
            secure=True,
        )
        self.assertRedirects(http_response, reverse("tournament:home"))

    def test_unknown_account_and_wrong_password_use_same_generic_message(self):
        unknown_account_response = self._login(
            email="unknown@example.com",
            password="wrong-password",
        )
        wrong_password_response = self._login(password="wrong-password")

        self.assertEqual(
            self._message_text(unknown_account_response),
            [LOGIN_ERROR_MESSAGE],
        )
        self.assertEqual(
            self._message_text(wrong_password_response),
            [LOGIN_ERROR_MESSAGE],
        )
        self.assertNotContains(unknown_account_response, "No account found")
        self.assertNotContains(wrong_password_response, "Incorrect password")

    def test_inactive_account_uses_same_generic_message(self):
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        inactive_account_response = self._login()

        self.user.is_active = True
        self.user.save(update_fields=["is_active"])
        unknown_account_response = self._login(email="unknown@example.com")
        wrong_password_response = self._login(password="wrong-password")

        for response in (
            inactive_account_response,
            unknown_account_response,
            wrong_password_response,
        ):
            self.assertEqual(
                self._message_text(response),
                [LOGIN_ERROR_MESSAGE],
            )

    def test_authenticated_post_logout_succeeds_and_redirects_home(self):
        self.client.login(username=self.user.username, password=self.password)

        response = self.client.post(self.logout_url)

        self.assertRedirects(response, reverse("tournament:home"))
        dashboard_response = self.client.get(reverse("accounts:dashboard"))
        self.assertRedirects(
            dashboard_response,
            f"{self.login_url}?next={reverse('accounts:dashboard')}",
        )

    def test_authenticated_get_logout_is_rejected_without_logging_out(self):
        self.client.login(username=self.user.username, password=self.password)

        response = self.client.get(self.logout_url)

        self.assertEqual(response.status_code, 405)
        dashboard_response = self.client.get(reverse("accounts:dashboard"))
        self.assertEqual(dashboard_response.status_code, 200)

    def test_logout_control_is_a_csrf_protected_post_form(self):
        self.client.login(username=self.user.username, password=self.password)

        response = self.client.get(reverse("accounts:dashboard"))

        content = response.content.decode()
        logout_form = '<form method="post" action="/accounts/logout/"'
        logout_forms = re.findall(
            r'<form method="post" action="/accounts/logout/".*?</form>',
            content,
            re.DOTALL,
        )
        self.assertEqual(content.count(logout_form), 2)
        self.assertEqual(len(logout_forms), 2)
        self.assertTrue(
            all('name="csrfmiddlewaretoken"' in form for form in logout_forms)
        )
        self.assertRegex(
            content,
            re.compile(
                r'id="user-menu-dropdown".*?'
                r'<form method="post" action="/accounts/logout/".*?'
                r'name="csrfmiddlewaretoken"',
                re.DOTALL,
            ),
        )
        self.assertRegex(
            content,
            re.compile(
                r'id="mobile-menu".*?'
                r'<form method="post" action="/accounts/logout/".*?'
                r'name="csrfmiddlewaretoken"',
                re.DOTALL,
            ),
        )
