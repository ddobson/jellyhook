import hashlib
import json
import pathlib
from typing import Any

from workers import utils
from workers.config import TEMP_DIR
from workers.errors import WebhookWorkerError
from workers.logger import logger
from workers.movie import Movie
from workers.services.service_base import ServiceBase

DOVI_PROFLE_7 = 7


class DoviConversionError(WebhookWorkerError):
    """Exception raised for errors in the DoviConversionService."""


class DoviConversionService(ServiceBase):
    """Service to convert Dolby Vision Profile 7.x to 8.x."""

    def __init__(self, movie: Movie, tmp_dir: pathlib.Path) -> None:
        """Initialize the DoviConversionService.

        Args:
            movie (Movie): The movie to convert.
            tmp_dir (pathlib.Path): The location to store temporary files.

        Returns:
            None

        """
        self.movie = movie
        self.tmp_dir = tmp_dir

    def exec(self) -> None:
        """Execute the Dolby Vision conversion process.

        Returns:
            None

        """
        logger.info(f"Beginning Dolby Vision profile conversion for {self.movie.full_title}...")

        logger.info("Checking Dolby Vision Profile Version")
        dovi_profile = self.get_dovi_profile()

        if not dovi_profile or dovi_profile != DOVI_PROFLE_7:
            logger.info("Dolby Vision Profile is not 7.x. Aborting.")
            return

        logger.info("Extracting video steam...")
        video_path = self.extract_video()

        logger.info("Checking if base layer and enhancement layer are in sync...")
        el_video_path = self.demux_video(video_path)
        base_layer = self.extract_layer(video_path, "BL_RPU.bin")
        enhancement_layer = self.extract_layer(el_video_path, "EL_RPU.bin")
        bl_checksum = self.calculate_sha512(base_layer)
        el_checksum = self.calculate_sha512(enhancement_layer)

        if bl_checksum != el_checksum:
            logger.info("Base layer is out of sync with enhancement layer. Aborting.")
            return

        logger.info("Converting video to Dolby Vision profile 8...")
        p8_video_path = self.convert_dovi_profile(video_path)

        logger.info("Merging P8 video with original Matroska files...")
        final_output = self.merge_mkv(self.movie.full_path, p8_video_path)

        logger.info("Moving final ouput to media destination")
        self.move_to_target(final_output, self.movie.full_path)

        logger.info(f"Conversion complete for '{self.movie.full_title}'")

    def get_dovi_profile(self) -> int:
        """Return the Dolby Vision profile version of the movie.

        Returns:
            int: The Dolby Vision profile version.

        """
        cmd = (
            "ffprobe "
            "-v quiet "
            "-print_format json "
            "-show_streams "
            "-select_streams v:0 "
            f"{self.movie.escaped_path}"
        )
        completed = utils.run_command(cmd)
        media_info = json.loads(completed.stdout)

        try:
            dovi_profile = media_info["streams"][0]["side_data_list"][0]["dv_profile"]
        except (IndexError, KeyError):
            dovi_profile = None

        return dovi_profile

    def extract_video(self) -> str:
        """Extract the video stream from the movie.

        Returns:
            str: The path to the extracted video stream.
        """
        video_path = f"{self.tmp_dir}/video.hevc"
        # TODO: Change this command to use ffmpeg instead of mkvextract for improved performance
        cmd = f'mkvextract "{self.movie.full_path}" tracks "0:{video_path}"'
        utils.run_command(cmd, log_output=True, log_err=True)

        return video_path

    def demux_video(self, video_path: str) -> str:
        """Demux the video stream to extract the enhancement layer.

        Args:
            video_path (str): The path to the video stream.

        Returns:
            str: The path to the extracted enhancement layer.

        """
        el_video_path = f"{self.tmp_dir}/EL.hevc"
        cmd = f'dovi_tool demux "{video_path}" --el-only -e  "{el_video_path}"'
        utils.run_command(cmd, log_output=True, log_err=True)

        return el_video_path

    def extract_layer(self, hevc_path: str, output_layer: str) -> str:
        """Extract the enhancement layer from the video stream.

        Args:
            hevc_path (str): The path to the video stream.
            output_layer (str): The name of the extracted layer.

        Returns:
            str: The path to the extracted layer.

        """
        layer = f"{self.tmp_dir}/{output_layer}"
        cmd = f'dovi_tool -m 0 extract-rpu "{hevc_path}" -o "{layer}"'
        utils.run_command(cmd)

        return layer

    def calculate_sha512(self, file_path: str) -> str:
        """Return a SHA512 checksum as a hexdigest for given file path.

        Args:
            file_path (str): The path to the file to checksum.

        Returns:
            str: The SHA512 checksum as a hexdigest.

        """
        sha512 = hashlib.sha512()
        with open(file_path, "rb") as f:  # noqa: PTH123
            for chunk in iter(lambda: f.read(4096), b""):
                sha512.update(chunk)
        return sha512.hexdigest()

    def convert_dovi_profile(self, video_path: str) -> str:
        """Convert the Dolby Vision profile from 7.x to 8.x.

        Args:
            video_path (str): The path to the video stream.

        Returns:
            str: The path to the converted video stream.

        """
        logger.info("Converting Dolby Vision to Profile 8.1...")

        p8_video_path = f"{self.tmp_dir}/P8.hevc"
        cmd = f'dovi_tool -m 2 convert --discard "{video_path}" -o "{p8_video_path}"'
        utils.run_command(cmd, log_output=True, log_err=True)

        return p8_video_path

    def merge_mkv(self, original_mkv: str, p8_video_path: str) -> pathlib.Path:
        """Merge the P8 video stream with the original Matroska files.

        Args:
            original_mkv (str): The path to the original Matroska file.
            p8_video_path (str): The path to the P8 video stream.

        Returns:
            pathlib.Path: The path to the final output file.

        """
        output_path = f"{self.tmp_dir}/final_output.mkv"
        cmd = f'mkvmerge --output "{output_path}" "{p8_video_path}" --no-video "{original_mkv}"'
        utils.run_command(cmd)
        return pathlib.Path(output_path)

    def move_to_target(self, output_file: pathlib.Path, target: str) -> None:
        """Move the final output file to the target destination.

        Args:
            output_file (pathlib.Path): The path to the final output file.
            target (str): The target destination for the final output file.

        Returns:
            None

        """
        self.movie.delete()
        output_file.rename(target)

    @classmethod
    def from_message(cls, message: dict, service_config: dict[str, Any]) -> "DoviConversionService":
        """Create a DoviConversionService from a Jellyfin webhook message.

        Args:
            message (dict): The Jellyfin webhook message.
            service_config (dict): The service configuration options.

        Returns:
            DoviConversionService: The initialized DoviConversionService.
        """
        # Get the configured temp directory or fall back to default
        temp_directory = service_config.get("temp_dir", TEMP_DIR)

        movie_file = cls.file_from_message(message)
        movie = Movie.from_file(movie_file)

        # Create the movie-specific temp directory
        tmp_dir = pathlib.Path(f"{temp_directory}/{movie.folder_title}")
        tmp_dir.mkdir(exist_ok=True, parents=True)
        logger.info(f"Using temporary directory: {tmp_dir}")
        return cls(movie, tmp_dir)
