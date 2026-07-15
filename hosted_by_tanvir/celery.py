import os

from celery import Celery


os.environ.setdefault("DJANGO_SETTINGS_MODULE", "hosted_by_tanvir.settings")

app = Celery("hosted_by_tanvir")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()


@app.task(name="hosted_by_tanvir.ping_celery")
def ping_celery():
    return "pong"
