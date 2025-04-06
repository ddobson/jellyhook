import json
import pathlib
import subprocess
import time
from collections.abc import Callable

import yaml

from workers.logger import logger


def timer(func: Callable) -> Callable:
    """Time the execution of a function.

    Args:
        func (function): The function to time.

    Returns:
        function: The wrapped function
    """

    def wrapper(*args: tuple, **kwargs: dict) -> any:
        start = time.time()
        result = func(*args, **kwargs)
        end = time.time()
        logger.info(f"{func.__name__} took {end - start} seconds")
        return result

    return wrapper


def run_command(
    cmd: str,
    log_output: bool = False,
    log_err: bool = False,
    **kwargs: dict,
) -> subprocess.CompletedProcess:
    """Execute a subprocess commands.

    This function runs a command in a subprocess and captures its output.
    It will sanitize command line arguments to prevent shell injection attacks.
    It can log the output and error streams to a logger if specified.

    Args:
        cmd (str): The command to execute.
        log_output (bool, optional): Stream stdout to logger. Defaults to False.
        log_err (bool, optional): Stream stderr to logger. Defaults to False.
        **kwargs: All additonal kwargs are passed to subproccess.Popen()

    Raises:
        subprocess.CalledProcessError: A exception with subprocess error information

    Returns:
        subprocess.CompletedProcess: An object representing the completed process

    """
    opts = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "shell": True,
        "bufsize": 1,
        "universal_newlines": True,
    }
    opts.update(kwargs)

    process = subprocess.Popen(cmd, **opts)  # Remove shlex.split, pass cmd directly

    # Simultaneously read stdout and stderr
    stdout_lines = []
    stderr_lines = []

    for line in process.stdout:
        strpline = line.rstrip()
        stdout_lines.append(strpline)
        if log_output:
            logger.info(strpline)
    for line in process.stderr:
        strpline = line.rstrip()
        stderr_lines.append(strpline)
        if log_err:
            logger.error(strpline)

    return_code = process.wait()
    output = "\n".join(stdout_lines)
    stderr = "\n".join(stderr_lines)

    if return_code != 0:
        raise subprocess.CalledProcessError(
            return_code,
            cmd,
            output=output,
            stderr=stderr,
        )

    return subprocess.CompletedProcess(
        process.args,
        process.returncode,
        stdout=output,
        stderr=stderr,
    )


def clean_dir(root: pathlib.Path) -> None:
    """Recursively delete all files and directories in a directory.

    Args:
        root (pathlib.Path): The directory to clean.

    Raises:
        FileNotFoundError: If the directory does not exist.
    """
    for p in root.iterdir():
        if p.is_dir():
            clean_dir(p)
        else:
            p.unlink()

    root.rmdir()


def ack_message(channel, delivery_tag: int, completed: bool) -> None:
    """Acknowledge a message.

    Note: that `channel` must be the same pika channel instance via which
    the message being ACKed was retrieved (AMQP protocol constraint).

    Args:
        channel (pika.channel.Channel): The channel to acknowledge the message on.
        delivery_tag (int): The delivery tag of the message to acknowledge.
        completed (bool): Whether the message was successfully processed.
    """
    if not channel.is_open:
        logger.info(
            f"Unable to acknowledge message with delivery tag: {delivery_tag}. Connection closed.",
        )
        return

    if completed:
        channel.basic_ack(delivery_tag)
    else:
        channel.basic_nack(delivery_tag, requeue=False)

    logger.info(f"Acknowledged message with delivery tag: {delivery_tag}")


def load_config_file(filename: str, default: dict | None = None) -> dict:
    """Load a JSON or YAML configuration file.

    Args:
        filename (str): The path to the configuration file.
        default (dict, None): Default value if file doesn't exist. Defaults to None.

    Returns:
        dict: The loaded configuration data.
    """
    if not default:
        default = {}

    try:
        config_path = pathlib.Path(filename)
        if not config_path.exists():
            return default

        with open(config_path, "r") as f:
            if config_path.suffix.lower() in [".yml", ".yaml"]:
                return yaml.safe_load(f)
            return json.load(f)

    except (json.JSONDecodeError, yaml.YAMLError, IOError) as e:
        logger.info(f"Error loading config file {filename}: {e}")
        return default
