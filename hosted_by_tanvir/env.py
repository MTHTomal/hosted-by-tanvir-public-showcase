import os
from pathlib import Path
from urllib.parse import urlsplit

from decouple import RepositoryEnv
from django.core.exceptions import ImproperlyConfigured


TRUE_VALUES = {"1", "true", "t", "yes", "y", "on"}
FALSE_VALUES = {"0", "false", "f", "no", "n", "off", ""}
VALID_DATABASE_URL_SCHEMES = {
    "cockroach",
    "mssql",
    "mssqlms",
    "mysql",
    "mysql-connector",
    "mysql2",
    "mysqlgis",
    "oracle",
    "oraclegis",
    "pgsql",
    "postgis",
    "postgres",
    "postgresql",
    "redshift",
    "spatialite",
    "sqlite",
    "timescale",
    "timescalegis",
}
PROJECT_ROOT = Path(__file__).resolve().parent.parent
_ENV_INITIALIZED = False


def _resolve_base_env_file():
    env_file_override = os.environ.get("ENV_FILE", "").strip()
    if env_file_override:
        env_file_path = Path(env_file_override)
        if not env_file_path.is_absolute():
            env_file_path = PROJECT_ROOT / env_file_path
        return env_file_path

    candidates = [
        PROJECT_ROOT / ".env",
        Path(__file__).resolve().parent / ".env",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def _resolve_local_env_file(base_env_file):
    if base_env_file.name == ".env":
        return base_env_file.with_name(".env.local")
    return Path(f"{base_env_file}.local")


def _read_env_file(env_file_path):
    if not env_file_path.exists() or not env_file_path.is_file():
        return {}
    return RepositoryEnv(str(env_file_path)).data


def _is_valid_bool(value):
    normalized = str(value).strip().lower()
    return normalized in TRUE_VALUES or normalized in FALSE_VALUES


def _is_valid_database_url(value):
    raw_value = str(value).strip()
    if not raw_value:
        return False
    scheme = urlsplit(raw_value).scheme.lower()
    return scheme in VALID_DATABASE_URL_SCHEMES


def _should_preserve_existing_env_value(key):
    existing_value = os.environ.get(key, "")
    if key == "DEBUG":
        return _is_valid_bool(existing_value)
    if key == "DATABASE_URL":
        return _is_valid_database_url(existing_value)
    return True


def initialize_environment(*, force=False):
    """
    Load env files with precedence:
    1) base env file (.env or ENV_FILE)
    2) local override file (.env.local or <ENV_FILE>.local)
    3) existing process environment (highest)
    """
    global _ENV_INITIALIZED
    if _ENV_INITIALIZED and not force:
        return

    existing_env_keys = set(os.environ.keys())
    base_env_file = _resolve_base_env_file()
    local_env_file = _resolve_local_env_file(base_env_file)

    for env_file in (base_env_file, local_env_file):
        for key, value in _read_env_file(env_file).items():
            if key in existing_env_keys and _should_preserve_existing_env_value(key):
                continue
            os.environ[key] = value

    _ENV_INITIALIZED = True


def env_bool(config, key, *, default=False):
    raw_value = config(key, default=default)
    if isinstance(raw_value, bool):
        return raw_value
    normalized = str(raw_value).strip().lower()
    if normalized in TRUE_VALUES:
        return True
    if normalized in FALSE_VALUES:
        return False
    return default


def env_list(config, key, *, default=""):
    raw_value = config(key, default=default)
    if raw_value is None:
        return []
    if isinstance(raw_value, (list, tuple)):
        return [str(item).strip() for item in raw_value if str(item).strip()]
    return [item.strip() for item in str(raw_value).split(",") if item.strip()]


def env_first(config, *keys, default=""):
    for key in keys:
        value = str(config(key, default="")).strip()
        if value:
            return value
    return default


def resolve_allowed_hosts(config, *, debug):
    default_hosts = "localhost,127.0.0.1" if debug else ""
    allowed_hosts = env_list(config, "ALLOWED_HOSTS", default=default_hosts)
    render_hostname = str(config("RENDER_EXTERNAL_HOSTNAME", default="")).strip()
    if render_hostname:
        allowed_hosts.append(render_hostname)

    allowed_hosts = list(dict.fromkeys(allowed_hosts))
    if not debug and not allowed_hosts:
        raise ImproperlyConfigured(
            "ALLOWED_HOSTS or RENDER_EXTERNAL_HOSTNAME must be set when DEBUG is False."
        )
    return allowed_hosts


def require_env(config, key):
    value = str(config(key, default="")).strip()
    if not value:
        raise ImproperlyConfigured(f"{key} must be set.")
    return value


def require_non_placeholder(config, key):
    value = require_env(config, key)
    if value.lower() == "placeholder":
        raise ImproperlyConfigured(f"{key} must be set to a real value.")
    return value
