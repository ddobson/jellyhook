import json
import pathlib

from workers import utils
from workers.services.media_track_clean import MediaTrackCleanService


def _streams(path: str) -> list[tuple[str, str]]:
    """(codec_type, language) for every stream in the file, in order."""
    result = utils.run_command(
        ["ffprobe", "-v", "error", "-show_streams", "-print_format", "json", path]
    )
    return [
        (s["codec_type"], s.get("tags", {}).get("language", "und"))
        for s in json.loads(result.stdout)["streams"]
    ]


def test_keep_only_english_tracks(
    multitrack_service_all_eng, track_clean_command_mock, leftovers_beside
):
    """Cleaning a real file leaves video plus English audio and subtitles, in place."""
    path = multitrack_service_all_eng.movie.full_path
    assert len(_streams(path)) > 3

    multitrack_service_all_eng.exec()

    # ffprobe, then ffmpeg mapping only the kept streams
    ffmpeg_cmd = track_clean_command_mock.call_args_list[1].args[0]
    assert ffmpeg_cmd[0] == "ffmpeg"
    assert [ffmpeg_cmd[i + 1] for i, part in enumerate(ffmpeg_cmd) if part == "-map"] == [
        "0:0",
        "0:1",
        "0:5",
    ]
    assert [kind for kind, _ in _streams(path)] == ["video", "audio", "subtitle"]
    assert {lang for kind, lang in _streams(path) if kind != "video"} == {"eng"}
    # No .bak or staging file is left beside the movie
    assert leftovers_beside(pathlib.Path(path)) == []


def test_keep_multiple_languages(multitrack_service_multi_langs, track_clean_command_mock):
    """Several languages can be kept at once."""
    path = multitrack_service_multi_langs.movie.full_path

    multitrack_service_multi_langs.exec()

    ffmpeg_cmd = track_clean_command_mock.call_args_list[1].args[0]
    mapped = [ffmpeg_cmd[i + 1] for i, part in enumerate(ffmpeg_cmd) if part == "-map"]
    assert mapped == ["0:0", "0:1", "0:2", "0:3", "0:5", "0:7"]
    audio = {lang for kind, lang in _streams(path) if kind == "audio"}
    subs = {lang for kind, lang in _streams(path) if kind == "subtitle"}
    assert audio == {"eng", "spa", "deu"}
    assert subs == {"eng", "deu"}


def test_cleaning_twice_changes_nothing_the_second_time(
    multitrack_service_all_eng, multitrack_movie, temp_dir
):
    """A redelivered message finds nothing left to remove."""
    path = pathlib.Path(multitrack_movie.full_path)
    multitrack_service_all_eng.exec()
    cleaned = path.read_bytes()

    again = MediaTrackCleanService(
        multitrack_movie,
        keep_audio_langs=["eng"],
        keep_sub_langs=["eng"],
        temp_dir=str(temp_dir / "scratch"),
    )
    again.exec()

    assert path.read_bytes() == cleaned


def test_skip_processing_if_all_streams_kept(multitrack_movie, track_clean_command_mock, temp_dir):
    """If every stream is wanted, ffmpeg never runs and the file is not touched."""
    path = pathlib.Path(multitrack_movie.full_path)
    before = path.read_bytes()
    service = MediaTrackCleanService(
        multitrack_movie,
        keep_original=True,
        keep_default=True,
        keep_audio_langs=["eng", "spa", "deu", "jpn"],
        keep_sub_langs=["eng", "fra", "deu", "jpn"],
        temp_dir=str(temp_dir / "scratch"),
    )

    service.exec()

    assert track_clean_command_mock.call_count == 1  # ffprobe only
    assert path.read_bytes() == before
