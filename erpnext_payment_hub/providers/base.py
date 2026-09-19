from __future__ import annotations

from typing import Any

import frappe
import requests


class ProviderError(frappe.ValidationError):
    pass


class BaseProvider:
    provider_name = "Base"

    def __init__(self, account):
        self.account = account

    def get_token(self):
        # Providers may use either Secret Key or API Key / Token.
        # Frappe raises for an empty Password field unless raise_exception=False.
        token = self.account.get_password("secret_key", raise_exception=False)
        if not token:
            token = self.account.get_password("api_key", raise_exception=False)

        if not token:
            raise ProviderError(f"API credential is missing for {self.account.name}")
        return token

    def headers(self):
        return {
            "Authorization": f"Bearer {self.get_token()}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def request(self, method: str, url: str, *, json_data=None, timeout=30) -> dict[str, Any]:
        try:
            response = requests.request(
                method,
                url,
                json=json_data,
                headers=self.headers(),
                timeout=timeout,
            )
        except requests.RequestException as exc:
            raise ProviderError(f"{self.provider_name} connection failed: {exc}") from exc

        try:
            payload = response.json()
        except ValueError:
            payload = {"raw_text": response.text}

        if not response.ok:
            detail = payload if payload else response.text
            raise ProviderError(
                f"{self.provider_name} API error {response.status_code}: {detail}"
            )

        return payload

    def create_payment(self, **kwargs):
        raise NotImplementedError

    def get_payment_status(self, transaction):
        raise NotImplementedError

    def refund(self, transaction, amount, reason=None, retry_key=None):
        raise NotImplementedError
