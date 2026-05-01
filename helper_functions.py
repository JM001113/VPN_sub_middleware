
def strip_email(vless_string: str) -> str:
    return vless_string[:vless_string.rfind("-")]