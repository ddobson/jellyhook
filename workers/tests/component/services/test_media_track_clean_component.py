import pytest

from workers.services.media_track_clean import MediaTrackCleanService


def test_keep_only_english_tracks(
    multitrack_service_all_eng, track_clean_command_mock, mock_os_operations
):
    """Test that only English audio and subtitle tracks are kept when configured."""
    # Run the service
    multitrack_service_all_eng.exec()

    # The mock ffprobe result in conftest.py has 4 audio and 4 subtitle tracks (various langs)
    # Only English audio (index 1) and English subtitle (index 5) should be kept, plus video (index 0)
    # The ffmpeg command should map only these indices
    ffmpeg_call = track_clean_command_mock.call_args_list[1]  # 0 is ffprobe, 1 is ffmpeg
    cmd = ffmpeg_call[0][0]
    assert "-map 0:0" in cmd  # video
    assert "-map 0:1" in cmd  # eng audio
    assert "-map 0:5" in cmd  # eng subtitle
    # Should not map other audio/subtitle tracks
    assert "-map 0:2" not in cmd  # spa audio
    assert "-map 0:3" not in cmd  # deu audio
    assert "-map 0:4" not in cmd  # jpn audio
    assert "-map 0:6" not in cmd  # fra subtitle
    assert "-map 0:7" not in cmd  # deu subtitle
    assert "-map 0:8" not in cmd  # jpn subtitle


def test_keep_multiple_languages(
    multitrack_service_multi_langs, track_clean_command_mock, mock_os_operations
):
    """Test that multiple audio and subtitle languages are kept when configured."""
    multitrack_service_multi_langs.exec()
    # Should keep eng, spa, deu audio (indices 1,2,3) and eng, deu subtitles (indices 5,7), plus video (0)
    ffmpeg_call = track_clean_command_mock.call_args_list[1]
    cmd = ffmpeg_call[0][0]
    assert "-map 0:0" in cmd  # video
    assert "-map 0:1" in cmd  # eng audio
    assert "-map 0:2" in cmd  # spa audio
    assert "-map 0:3" in cmd  # deu audio
    assert "-map 0:5" in cmd  # eng subtitle
    assert "-map 0:7" in cmd  # deu subtitle
    # Should not map jpn audio or fra/jpn subtitles
    assert "-map 0:4" not in cmd  # jpn audio
    assert "-map 0:6" not in cmd  # fra subtitle
    assert "-map 0:8" not in cmd  # jpn subtitle


def test_skip_processing_if_all_streams_kept(
    monkeypatch, multitrack_movie, track_clean_command_mock
):
    """Test that processing is skipped if all streams are to be kept."""
    # Configure service to keep all tracks
    service = MediaTrackCleanService(
        multitrack_movie,
        keep_original=True,
        keep_default=True,
        keep_audio_langs=["eng", "spa", "deu", "jpn"],
        keep_sub_langs=["eng", "fra", "deu", "jpn"],
    )
    # Patch _process_file and _replace_original_file to ensure they're not called
    monkeypatch.setattr(service, "_process_file", pytest.fail)
    monkeypatch.setattr(service, "_replace_original_file", pytest.fail)
    # Should not raise and should use the track_clean_command_mock for ffprobe
    service.exec()
