from steward.filter.redact import redact


def test_known_key_shapes_are_redacted() -> None:
    samples = [
        "export OPENROUTER_API_KEY=sk-or-v1-" + "a" * 40,
        "token hf_" + "b" * 34,
        "ghp_" + "c" * 36,
        "AKIA" + "D" * 16,
        "Authorization: Bearer " + "e" * 30,
        "-----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY-----",
    ]
    for sample in samples:
        assert "[REDACTED]" in redact(sample), sample


def test_assigned_secret_keeps_the_name() -> None:
    assert redact('password: "hunter2hunter2hunter2"') == 'password: "[REDACTED]"'


def test_ordinary_text_unchanged() -> None:
    text = "[step 4] ACTION Bash: git push origin main"
    assert redact(text) == text
