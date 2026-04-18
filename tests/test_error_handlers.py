from telegram.error import BadRequest

from tbssa.error_handlers import is_ignorable_telegram_error


def test_is_ignorable_telegram_error_for_message_not_modified():
    assert is_ignorable_telegram_error(BadRequest("Message is not modified"))


def test_is_ignorable_telegram_error_is_false_for_other_errors():
    assert not is_ignorable_telegram_error(BadRequest("Message to edit not found"))
