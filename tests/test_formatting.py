"""Resource -> LLM-facing dict flattening."""

from youtube_mcp.formatting import (
    category_name,
    humanize_duration,
    is_short,
    truncate,
    video_summary,
)


def test_truncate_short_text_unchanged():
    assert truncate("hello world") == "hello world"


def test_truncate_long_text_breaks_on_word_boundary():
    text = "word " * 100
    result = truncate(text, limit=50)
    assert len(result) <= 53  # limit + "..."
    assert not result.rstrip(".").endswith("wor")  # never mid-word


def test_truncate_empty():
    assert truncate(None) == ""
    assert truncate("") == ""


def test_humanize_duration_under_an_hour():
    assert humanize_duration(90) == "1:30"


def test_humanize_duration_over_an_hour():
    assert humanize_duration(3723) == "1:02:03"


def test_humanize_duration_unknown():
    assert humanize_duration(None) == "unknown"


def test_category_name_known_and_unknown():
    assert category_name("28") == "Science & Technology"
    assert category_name("9999") == "Other"
    assert category_name(None) == "Other"


def test_is_short_true_for_short_video():
    video = {"contentDetails": {"duration": "PT45S"}}
    assert is_short(video) is True


def test_is_short_false_for_long_video():
    video = {"contentDetails": {"duration": "PT10M"}}
    assert is_short(video) is False


def test_is_short_false_when_duration_unknown():
    # A live stream with no fixed duration must not be misclassified as a Short.
    assert is_short({"contentDetails": {}}) is False


def test_video_summary_flattens_expected_fields():
    video = {
        "id": "abc123",
        "snippet": {
            "title": "Test Video",
            "channelTitle": "Test Channel",
            "channelId": "UC123",
            "publishedAt": "2026-09-01T00:00:00Z",
            "categoryId": "28",
            "description": "A description",
        },
        "contentDetails": {"duration": "PT5M"},
        "statistics": {"viewCount": "1000", "likeCount": "50"},
    }
    summary = video_summary(video)
    assert summary["video_id"] == "abc123"
    assert summary["title"] == "Test Video"
    assert summary["duration"] == "5:00"
    assert summary["views"] == 1000
    assert summary["likes"] == 50
    assert summary["url"] == "https://www.youtube.com/watch?v=abc123"
    assert summary["category"] == "Science & Technology"


def test_video_summary_omits_missing_stats():
    video = {"id": "x", "snippet": {"title": "t"}, "contentDetails": {}}
    summary = video_summary(video)
    assert "views" not in summary
    assert "likes" not in summary


def test_video_summary_can_exclude_description():
    video = {"id": "x", "snippet": {"title": "t", "description": "long text"}}
    summary = video_summary(video, include_description=False)
    assert "description" not in summary
