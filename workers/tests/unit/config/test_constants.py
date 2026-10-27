import pytest

from workers.config.constants import _port_from_env


def test_port_defaults_when_unset(monkeypatch):
    monkeypatch.delenv("RABBITMQ_PORT", raising=False)

    assert _port_from_env("RABBITMQ_PORT", 5672) == 5672


@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_port_means_the_default(monkeypatch, blank):
    monkeypatch.setenv("RABBITMQ_PORT", blank)

    assert _port_from_env("RABBITMQ_PORT", 5672) == 5672


def test_port_is_read_from_the_environment(monkeypatch):
    monkeypatch.setenv("RABBITMQ_PORT", " 5999 ")

    assert _port_from_env("RABBITMQ_PORT", 5672) == 5999


def test_a_platform_injected_url_gets_an_error_that_names_the_variable(monkeypatch):
    """Legacy Docker links and Kubernetes service links set RABBITMQ_PORT=tcp://host:port."""
    monkeypatch.setenv("RABBITMQ_PORT", "tcp://10.0.0.5:5672")

    with pytest.raises(ValueError, match="RABBITMQ_PORT must be a port number.*tcp://10.0.0.5"):
        _port_from_env("RABBITMQ_PORT", 5672)
