import time

import pika

from workers import config
from workers.clients import rabbitmq
from workers.logger import logger


def main() -> None:
    """Main function for the worker.

    Starts a long running process that listens for messages on the queue.
    """
    logger.info(f"Loading Jellyhook configuration from {config.JELLYHOOK_CONFIG_PATH}")
    worker_config = config.WorkerConfig.load(config.JELLYHOOK_CONFIG_PATH)  # Singleton first init

    # Get enabled webhooks
    enabled_webhooks = worker_config.get_enabled_webhooks()
    if not enabled_webhooks:
        logger.warning("No enabled webhooks found in configuration file.")
        logger.warning("Exiting...")
        return

    # Create consumer and start processing
    while True:
        try:
            consumer = rabbitmq.MessageConsumer(enabled_webhooks)
            consumer.connect()
            consumer.start()
            break  # If we get here cleanly, break the loop
        except pika.exceptions.AMQPConnectionError as e:
            logger.error(f"Connection error: {e}. Retrying in 5 seconds...")
            time.sleep(5)
        except KeyboardInterrupt:
            logger.info("Keyboard Interrupt received. Exiting...")
            break
        except Exception as e:
            logger.error(f"Unexpected error: {e}. Retrying in 5 seconds...")
            time.sleep(5)


if __name__ == "__main__":
    if config.DEBUG:
        import debugpy

        debugpy.listen(("0.0.0.0", 5678))
    main()
