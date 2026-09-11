class PreviewOnlyPublisher:
    """Bounded no-op publisher used when streaming configuration is invalid."""

    def __init__(self, config):
        self.config = config

    def start(self):
        pass

    def publish(self, _frame):
        return True

    def stop(self):
        pass


def validate_stream_config(config):
    """Validate frontend constraints in addition to StreamConfig's checks."""
    if config.pixel_format == "yuv420p" and (config.width % 2 or config.height % 2):
        raise ValueError("width y height deben ser pares para yuv420p")
    return config


__all__ = ["PreviewOnlyPublisher", "validate_stream_config"]
