from __future__ import annotations

from typing import Protocol

import keyring
from keyring.errors import KeyringError

from app.errors import ProviderError


class SecretStore(Protocol):
    def get(self, identifier: str) -> str | None: ...

    def set(self, identifier: str, secret: str) -> None: ...

    def delete(self, identifier: str) -> None: ...


class KeyringSecretStore:
    """Stores provider credentials in the operating system credential store."""

    service_name = "jarvis-local"

    def get(self, identifier: str) -> str | None:
        try:
            return keyring.get_password(self.service_name, identifier)
        except KeyringError as error:
            raise ProviderError("无法读取本机凭据存储。") from error

    def set(self, identifier: str, secret: str) -> None:
        try:
            keyring.set_password(self.service_name, identifier, secret)
        except KeyringError as error:
            raise ProviderError("无法写入本机凭据存储。") from error

    def delete(self, identifier: str) -> None:
        try:
            keyring.delete_password(self.service_name, identifier)
        except keyring.errors.PasswordDeleteError:
            return
        except KeyringError as error:
            raise ProviderError("无法删除本机凭据存储。") from error


class InMemorySecretStore:
    """A test-only secret store."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, identifier: str) -> str | None:
        return self.values.get(identifier)

    def set(self, identifier: str, secret: str) -> None:
        self.values[identifier] = secret

    def delete(self, identifier: str) -> None:
        self.values.pop(identifier, None)
