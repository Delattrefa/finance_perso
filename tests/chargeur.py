"""Chargeur de templates factice : permet de tester les vues avant l'écriture des templates."""
from django.template import Origin
from django.template.loaders.base import Loader


class ChargeurFactice(Loader):
    def get_template_sources(self, template_name):
        yield Origin(name=template_name, template_name=template_name, loader=self)

    def get_contents(self, origin):
        return f"[{origin.template_name}]"


TEMPLATES_FACTICES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "OPTIONS": {
        "context_processors": [
            "django.template.context_processors.request",
            "django.contrib.auth.context_processors.auth",
            "django.contrib.messages.context_processors.messages",
            "core.context_processors.preferences",
        ],
        "loaders": ["tests.chargeur.ChargeurFactice"],
    },
}]
