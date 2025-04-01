import functools
import json
import subprocess

import pika
import pika.adapters
import pika.adapters.blocking_connection
import pika.spec

from workers import utils
from workers.logger import logger
from workers.services import dovi_conversion


@utils.timer
def item_added(
    connection: pika.adapters.BlockingConnection,
    channel: pika.adapters.blocking_connection.BlockingChannel,
    delivery_tag: pika.spec.Basic.Deliver,
    body: bytes,
) -> None:
    """Process an item_added webhook message from Jellyfin.

    Args:
        connection (pika.BlockingConnection): The connection to the RabbitMQ server.
        channel (pika.adapters.BlockingChannel): The channel the message was received on.
        delivery_tag (int): The delivery tag of the message.
        body (bytes): The message body.
    """
    message = json.loads(body)
    completed = False

    try:
        service = dovi_conversion.DoviConversionService.from_message(message)
        service.exec()
        completed = True
    except Exception as e:
        if isinstance(e, subprocess.CalledProcessError):
            logger.error(e.stderr)
        logger.error(e)
    finally:
        try:
            logger.info(f"Cleaning temporary files for '{service.tmp_dir}'...")
            utils.clean_dir(service.tmp_dir)
        except (NameError, FileNotFoundError):
            pass

    cb = functools.partial(utils.ack_message, channel, delivery_tag, completed)
    connection.add_callback_threadsafe(cb)
