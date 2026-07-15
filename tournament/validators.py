import os

from django.core.exceptions import ValidationError

try:
    from PIL import Image, UnidentifiedImageError
except ImportError:  # pragma: no cover - Pillow is pinned, but keep fallback safe.
    Image = None
    UnidentifiedImageError = OSError


KB = 1024
MB = 1024 * KB

PROFILE_IMAGE_MAX_SIZE = 500 * KB  # Existing avatar/logo limit.
RESULT_SCREENSHOT_MAX_SIZE = 5 * MB

ALLOWED_IMAGE_CONTENT_TYPES = {
    "image/gif": {"extensions": {".gif"}, "formats": {"GIF"}},
    "image/jpeg": {"extensions": {".jpg", ".jpeg"}, "formats": {"JPEG"}},
    "image/png": {"extensions": {".png"}, "formats": {"PNG"}},
    "image/webp": {"extensions": {".webp"}, "formats": {"WEBP"}},
}

ALLOWED_IMAGE_EXTENSIONS = {
    extension
    for spec in ALLOWED_IMAGE_CONTENT_TYPES.values()
    for extension in spec["extensions"]
}

ALLOWED_IMAGE_FORMATS = {
    image_format
    for spec in ALLOWED_IMAGE_CONTENT_TYPES.values()
    for image_format in spec["formats"]
}


def human_file_size(size):
    if size % MB == 0:
        return f"{size // MB} MB"
    if size % KB == 0:
        return f"{size // KB} KB"
    return f"{size} bytes"


def validate_image_upload(
    uploaded_file,
    *,
    field_name="image",
    display_name=None,
    max_size=PROFILE_IMAGE_MAX_SIZE,
    max_size_label=None,
    as_field_error=False,
):
    """
    Validate fresh Django image uploads without touching stored Cloudinary resources.

    Checks size, extension, browser-provided content type, and, when Pillow is
    available, verifies that the file payload can be opened as an image.
    """
    if not uploaded_file:
        return

    has_size = hasattr(uploaded_file, "size")
    has_reader = callable(getattr(uploaded_file, "read", None))
    if not has_size and not has_reader:
        return

    label = display_name or field_name.replace("_", " ")
    label = label[:1].upper() + label[1:]
    max_size_label = max_size_label or human_file_size(max_size)

    def fail(message):
        if as_field_error:
            raise ValidationError(message)
        raise ValidationError({field_name: message})

    size = getattr(uploaded_file, "size", None)
    if size is not None:
        if size <= 0:
            fail(f"{label} cannot be empty.")
        if size > max_size:
            fail(f"{label} must be {max_size_label} or smaller.")

    name = getattr(uploaded_file, "name", "") or ""
    extension = os.path.splitext(name)[1].lower()
    if extension not in ALLOWED_IMAGE_EXTENSIONS:
        allowed = ", ".join(sorted(ALLOWED_IMAGE_EXTENSIONS))
        fail(f"{label} must use one of these file extensions: {allowed}.")

    content_type = (getattr(uploaded_file, "content_type", "") or "").lower().split(";")[0].strip()
    if content_type not in ALLOWED_IMAGE_CONTENT_TYPES:
        fail(f"{label} must be a JPEG, PNG, GIF, or WebP image.")

    if extension not in ALLOWED_IMAGE_CONTENT_TYPES[content_type]["extensions"]:
        fail(f"{label} file extension does not match its content type.")

    if Image is None:
        return

    try:
        if callable(getattr(uploaded_file, "seek", None)):
            uploaded_file.seek(0)
        image = Image.open(uploaded_file)
        image.verify()
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError):
        fail(f"{label} could not be verified as a valid image.")
    finally:
        if callable(getattr(uploaded_file, "seek", None)):
            uploaded_file.seek(0)

    image_format = getattr(image, "format", None)
    if image_format not in ALLOWED_IMAGE_FORMATS:
        fail(f"{label} must be a JPEG, PNG, GIF, or WebP image.")
    if image_format not in ALLOWED_IMAGE_CONTENT_TYPES[content_type]["formats"]:
        fail(f"{label} image data does not match its content type.")
