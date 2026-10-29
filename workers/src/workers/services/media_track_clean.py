import json
import os
import pathlib
import subprocess
from typing import Sequence

from workers import fileops
from workers.config import TEMP_DIR
from workers.logger import logger
from workers.models.movie import Movie
from workers.services.service_base import ServiceBase
from workers.utils import run_command


class MediaStream:
    """Represents a media stream with metadata from ffprobe."""

    def __init__(
        self,
        index: int,
        codec_type: str,
        language: str | None = None,
        is_default: bool = False,
        is_original: bool = False,
    ) -> None:
        """Initialize a MediaStream.

        Args:
            index: The index of the stream.
            codec_type: The type of the stream (audio, subtitle, etc.).
            language: The language of the stream.
            is_default: Whether this stream is marked as default.
            is_original: Whether this stream is marked as original.
        """
        self.index = index
        self.codec_type = codec_type
        self.language = language or "und"  # Default to undefined if not specified
        self.is_default = is_default
        self.is_original = is_original

    @classmethod
    def from_ffprobe(cls, stream_data: dict) -> "MediaStream":
        """Create a MediaStream from ffprobe stream data.

        Args:
            stream_data: The stream data from ffprobe.

        Returns:
            A MediaStream instance.
        """
        # Extract basic info
        index = stream_data["index"]
        codec_type = stream_data["codec_type"]

        # Extract language from tags if available
        language = None
        if "tags" in stream_data and "language" in stream_data["tags"]:
            language = stream_data["tags"]["language"]

        # Extract disposition flags
        is_default = False
        is_original = False
        if "disposition" in stream_data:
            is_default = bool(stream_data["disposition"].get("default", 0))
            is_original = bool(stream_data["disposition"].get("original", 0))

        return cls(
            index=index,
            codec_type=codec_type,
            language=language,
            is_default=is_default,
            is_original=is_original,
        )


class MediaTrackCleanConfig:
    """Configuration for the media track cleaning service."""

    def __init__(
        self,
        keep_original: bool = True,
        keep_default: bool = True,
        keep_audio_langs: list[str] | None = None,
        keep_sub_langs: list[str] | None = None,
    ) -> None:
        """Initialize the configuration.

        Args:
            keep_original: Whether to keep original tracks.
            keep_default: Whether to keep default tracks.
            keep_audio_langs: Languages to keep for audio tracks.
            keep_sub_langs: Languages to keep for subtitle tracks.
        """
        self.keep_original = keep_original
        self.keep_default = keep_default
        self.keep_audio_langs = keep_audio_langs or []
        self.keep_sub_langs = keep_sub_langs or []

    @classmethod
    def from_dict(cls, config: dict) -> "MediaTrackCleanConfig":
        """Create a configuration from a dictionary.

        Args:
            config: The configuration dictionary.

        Returns:
            A MediaTrackCleanConfig instance.
        """
        return cls(
            keep_original=config.get("keep_original", True),
            keep_default=config.get("keep_default", True),
            keep_audio_langs=config.get("keep_audio_langs", []),
            keep_sub_langs=config.get("keep_sub_langs", []),
        )


class MediaTrackCleanService(ServiceBase):
    """Service to clean up media tracks based on configured rules."""

    movies_only = True

    def __init__(
        self,
        movie: Movie,
        keep_original: bool = True,
        keep_default: bool = True,
        keep_audio_langs: list[str] | None = None,
        keep_sub_langs: list[str] | None = None,
        temp_dir: str = TEMP_DIR,
    ) -> None:
        """Initialize the media track clean service.

        Args:
            movie: The movie to clean up media tracks for.
            keep_original: Whether to keep original tracks.
            keep_default: Whether to keep default tracks.
            keep_audio_langs: List of languages to keep for audio tracks.
            keep_sub_langs: List of languages to keep for subtitle tracks.
            temp_dir: Where to create this run's scratch directory.
        """
        self.movie = movie
        self.config = MediaTrackCleanConfig(
            keep_original=keep_original,
            keep_default=keep_default,
            keep_audio_langs=keep_audio_langs or [],
            keep_sub_langs=keep_sub_langs or [],
        )
        # Only a path for now; the directory is created in exec(), if there is work to do
        self.tmp_dir = fileops.job_dir_path(temp_dir, "media_track_clean")
        logger.info(
            f"Initialized MediaTrackCleanService for {movie.full_title} with config: "
            f"keep_original={keep_original}, keep_default={keep_default}, "
            f"keep_audio_langs={keep_audio_langs}, keep_sub_langs={keep_sub_langs}"
        )

    @property
    def locked_path(self) -> pathlib.Path:
        """The movie file this service rewrites."""
        return pathlib.Path(self.movie.full_path)

    def exec(self) -> None:
        """Execute the media track clean service."""
        if not os.path.exists(self.movie.full_path):
            raise FileNotFoundError(f"Movie file not found: {self.movie.full_path}")

        # Get media streams info
        streams = self._get_media_streams()
        logger.info(f"Found {len(streams)} streams in {self.movie.full_title}")

        # Evaluate which streams to keep
        streams_to_keep = self._evaluate_streams(streams)
        logger.info(f"Keeping {len(streams_to_keep)} streams out of {len(streams)}")

        # Skip if all streams should be kept
        if len(streams_to_keep) == len(streams):
            logger.info(
                f"All streams will be kept, skipping processing for {self.movie.full_title}"
            )
            return

        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        output_file = self._create_output_file_path()

        # Generate and execute ffmpeg command to remove unwanted tracks
        self._process_file(streams_to_keep, output_file)

        # Replace original file with processed file
        self._replace_original_file(output_file)

    def _get_media_streams(self) -> list[MediaStream]:
        """Get media streams from the movie file.

        Returns:
            A list of MediaStream objects.
        """
        # Call ffprobe to get stream information
        cmd = [
            "ffprobe",
            "-v",
            "error",
            "-show_streams",
            "-print_format",
            "json",
            self.movie.full_path,
        ]

        # Execute the command using the utility function
        try:
            result = run_command(cmd)
            # Parse the JSON output
            ffprobe_data = json.loads(result.stdout)
        except (subprocess.CalledProcessError, json.JSONDecodeError) as e:
            logger.error(f"Failed to get media streams from {self.movie.full_path}: {e}")
            raise

        # Convert to MediaStream objects
        streams = []
        for stream_data in ffprobe_data.get("streams", []):
            # Only consider audio and subtitle streams
            if stream_data.get("codec_type") in ["audio", "subtitle"]:
                stream = MediaStream.from_ffprobe(stream_data)
                streams.append(stream)
            elif stream_data.get("codec_type") == "video":
                # Add video streams automatically (they'll always be kept)
                stream = MediaStream.from_ffprobe(stream_data)
                streams.append(stream)

        return streams

    def _evaluate_streams(self, streams: list[MediaStream]) -> list[MediaStream]:
        """Evaluate which streams to keep based on configuration.

        Args:
            streams: List of media streams.

        Returns:
            List of streams to keep.
        """
        streams_to_keep = []

        for stream in streams:
            # Always keep video streams
            if stream.codec_type == "video":
                streams_to_keep.append(stream)
                continue

            # Apply rules for audio and subtitle streams
            keep = False

            # Keep original streams if configured
            if self.config.keep_original and stream.is_original:
                keep = True
                logger.debug(f"Keeping stream {stream.index} (original flag is set)")

            # Keep default streams if configured
            elif self.config.keep_default and stream.is_default:
                keep = True
                logger.debug(f"Keeping stream {stream.index} (default flag is set)")

            # Keep audio streams with matching languages
            elif stream.codec_type == "audio" and stream.language in self.config.keep_audio_langs:
                keep = True
                logger.debug(f"Keeping audio stream {stream.index} (language {stream.language})")

            # Keep subtitle streams with matching languages
            elif stream.codec_type == "subtitle" and stream.language in self.config.keep_sub_langs:
                keep = True
                logger.debug(f"Keeping subtitle stream {stream.index} (language {stream.language})")

            if keep:
                streams_to_keep.append(stream)
            else:
                logger.debug(
                    f"Removing stream {stream.index} (type: {stream.codec_type}, "
                    f"language: {stream.language})"
                )

        return streams_to_keep

    def _create_output_file_path(self) -> pathlib.Path:
        """Create the output file path.

        Returns:
            Path to the output file.
        """
        # Get the original file extension
        _, extension = os.path.splitext(self.movie.full_path)

        # Create a temporary output file in the temp directory
        output_file = self.tmp_dir / f"cleaned{extension}"
        return output_file

    def _process_file(
        self, streams_to_keep: Sequence[MediaStream], output_file: pathlib.Path
    ) -> None:
        """Process the file to keep only selected streams.

        Args:
            streams_to_keep: List of streams to keep.
            output_file: Path to the output file.
        """
        # Build ffmpeg command parts
        # -nostdin: ffmpeg otherwise reads the keyboard; -loglevel/-stats keep stderr short
        cmd = ["ffmpeg", "-nostdin", "-loglevel", "error", "-i", self.movie.full_path]

        # Add map options for each stream to keep
        for stream in streams_to_keep:
            cmd.extend(["-map", f"0:{stream.index}"])

        # Keep attachments (MKV fonts that styled subtitles need). Optional, so files without
        # any still work. Data streams are left out: the Matroska muxer rejects them.
        cmd.extend(["-map", "0:t?"])

        # Copy streams without re-encoding
        cmd.extend(["-c", "copy"])

        # Copy metadata
        cmd.extend(["-map_metadata", "0"])

        # Add output file
        cmd.append(str(output_file))

        logger.info(f"Running ffmpeg command to clean tracks: {cmd}")

        # Execute the command using the utility function
        try:
            run_command(cmd, log_err=True)
            logger.info(f"Successfully created cleaned file at {output_file}")
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to process file {self.movie.full_path}: {e}")
            logger.error(f"FFmpeg error: {e.stderr}")
            raise

    def _replace_original_file(self, output_file: pathlib.Path) -> None:
        """Replace the original file with the processed file.

        Args:
            output_file: Path to the processed file.
        """
        # One atomic swap. The old backup-rename-delete dance had a window where the movie
        # existed only as "<name>.bak", which no library scan would pick up.
        fileops.replace_file(output_file, pathlib.Path(self.movie.full_path))
        logger.info(f"Replaced {self.movie.full_path} with the cleaned file")

    @classmethod
    def from_message(cls, message: dict, service_config: dict) -> "ServiceBase":
        """Called when a message is received from the queue.

        Args:
            message: The message to be processed.
            service_config: The configuration for the service.

        Returns:
            ServiceBase: An instance of the service.
        """
        movie_file = cls.file_from_message(message)
        movie = Movie.from_file(movie_file)
        kwargs = {
            "keep_original": service_config.get("keep_original", True),
            "keep_default": service_config.get("keep_default", True),
            "keep_audio_langs": service_config.get("keep_audio_langs", []),
            "keep_sub_langs": service_config.get("keep_sub_langs", []),
            "temp_dir": service_config.get("temp_dir", TEMP_DIR),
        }

        return cls(movie, **kwargs)
