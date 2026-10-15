"""Tests for store_sync.core.pic — the `;`-separated per-job PIC list."""
from store_sync.core.pic import (
    format_pics,
    is_valid_email,
    parse_pics,
    pic_email_warning,
    split_pic_emails,
)


def test_parse_trims_drops_empty_and_dedupes_case_insensitively():
    assert parse_pics("alice@x.com; Bob ;;ALICE@X.COM") == ["alice@x.com", "Bob"]
    assert parse_pics("") == [] and parse_pics(None) == []


def test_format_round_trips_to_canonical_semicolon_space():
    assert format_pics(["a@x.com", "b@y.com"]) == "a@x.com; b@y.com"
    assert format_pics([]) == ""


def test_is_valid_email_requires_a_dotted_domain():
    assert is_valid_email("citra@nielseniq.com")
    assert not is_valid_email("Citra")
    assert not is_valid_email("citra@nielseniq")


def test_split_groups_addresses_and_everything_else():
    assert split_pic_emails("a@x.com; Budi") == (["a@x.com"], ["Budi"])


def test_warning_names_non_addresses_and_is_empty_when_all_valid():
    assert pic_email_warning("a@x.com; Budi") == (
        "1 entry is not an email address and will not receive reports: Budi"
    )
    assert pic_email_warning("a@x.com") == ""
