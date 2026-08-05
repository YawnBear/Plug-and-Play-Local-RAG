import re
import unicodedata

_USERNAME = re.compile(r"^[a-z0-9][a-z0-9._-]{1,30}[a-z0-9]$")
_REJECTED_DISPLAY_CATEGORIES = frozenset({"Cc", "Cf", "Cs"})


def normalize_username(value: str) -> str:
    username = value.lower()
    if not _USERNAME.fullmatch(username):
        raise ValueError(
            "username must contain 3-32 lowercase ASCII letters, digits, '.', "
            "'_', or '-', and begin and end with a letter or digit"
        )
    return username


def normalize_display_name(value: str) -> str:
    display_name = unicodedata.normalize("NFKC", value.strip())
    if not 1 <= len(display_name) <= 80:
        raise ValueError("display name must contain 1-80 Unicode characters")
    if any(
        unicodedata.category(character) in _REJECTED_DISPLAY_CATEGORIES
        for character in display_name
    ):
        raise ValueError("display name must not contain Unicode control characters")
    return display_name


def validate_permanent_password(value: str) -> str:
    if not 14 <= len(value) <= 128:
        raise ValueError("permanent password must contain 14-128 Unicode characters")
    return value
