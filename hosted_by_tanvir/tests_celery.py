from django.test import SimpleTestCase

from hosted_by_tanvir.celery import app as celery_app, ping_celery


class CeleryFoundationTests(SimpleTestCase):
    def test_celery_app_imports_with_project_name(self):
        self.assertEqual(celery_app.main, "hosted_by_tanvir")

    def test_ping_celery_runs_in_eager_mode(self):
        result = ping_celery.delay()

        self.assertEqual(result.get(timeout=1), "pong")
