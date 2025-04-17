import json
import os
import pathlib
import shlex
import subprocess
import tempfile
from typing import Sequence

from workers.logger import logger
from workers.models.items import MediaStream, MediaTrackCleanConfig
from workers.movie import Movie
from workers.services.service_base import ServiceBase
from workers.utils import run_command


class MediaTrackCleanService(ServiceBase):
    """Service to clean up media tracks based on configured rules."""

    def __init__(
        self,
        movie: Movie,
        keep_original: bool = True,
        keep_default: bool = True,
        keep_audio_langs: list[str] = None,
        keep_sub_langs: list[str] = None,
    ) -> None:
        """Initialize the media track clean service.

        Args:
            movie: The movie to clean up media tracks for.
            keep_original: Whether to keep original tracks.
            keep_default: Whether to keep default tracks.
            keep_audio_langs: List of languages to keep for audio tracks.
            keep_sub_langs: List of languages to keep for subtitle tracks.
        """
        self.movie = movie
        self.config = MediaTrackCleanConfig(
            keep_original=keep_original,
            keep_default=keep_default,
            keep_audio_langs=keep_audio_langs or [],
            keep_sub_langs=keep_sub_langs or [],
        )
        self.tmp_dir = pathlib.Path(tempfile.mkdtemp(prefix="jellyhook_media_track_clean_"))
        logger.info(
            f"Initialized MediaTrackCleanService for {movie.full_title} with config: "
            f"keep_original={keep_original}, keep_default={keep_default}, "
            f"keep_audio_langs={keep_audio_langs}, keep_sub_langs={keep_sub_langs}"
        )

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

        # Create output file name
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
        cmd_parts = [
            "ffprobe",
            "-v",
            "error",
            "-show_streams",
            "-print_format",
            "json",
        ]

        # Convert to shell command
        cmd = " ".join(shlex.quote(str(part)) for part in cmd_parts) + f" {self.movie.escaped_path}"

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
        cmd_parts = ["ffmpeg", "-i", self.movie.escaped_path]

        # Add map options for each stream to keep
        for stream in streams_to_keep:
            cmd_parts.extend(["-map", f"0:{stream.index}"])

        # Copy streams without re-encoding
        cmd_parts.extend(["-c", "copy"])

        # Copy metadata
        cmd_parts.extend(["-map_metadata", "0"])

        # Add output file
        escaped_output = shlex.quote(str(output_file))
        cmd_parts.append(escaped_output)

        # Convert to shell command
        cmd = " ".join(str(part) for part in cmd_parts)

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
        # Get original file path
        original_file = self.movie.full_path

        # Create a backup file name
        backup_file = f"{original_file}.bak"

        # Move original file to backup
        os.rename(original_file, backup_file)
        logger.info(f"Created backup of original file at {backup_file}")

        # Move new file to original location
        os.rename(output_file, original_file)
        logger.info(f"Moved cleaned file to original location: {original_file}")

        # Remove backup file
        os.unlink(backup_file)
        logger.info(f"Removed backup file: {backup_file}")

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
        }

        return cls(movie, **kwargs)
