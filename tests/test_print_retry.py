from email_invoice_bot.print_retry import classify_retry


def test_classify_retry_normalizes_known_renderer_error():
    assert classify_retry("engine6_error; Too few operands in path") == "normalize"


def test_classify_retry_resubmits_transient_error():
    assert classify_retry("Printer temporarily unavailable") == "resubmit"


def test_classify_retry_rejects_ambiguous_error():
    assert classify_retry("Unknown spooler failure") is None
