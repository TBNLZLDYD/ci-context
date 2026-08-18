"""Regression tests: PyGithub requests must bypass the Windows registry system proxy.

dev-sidecar/Clash set the *registry* proxy (HKCU\\...\\Internet Settings) while
leaving HTTP_PROXY env vars empty; requests reads it via get_environ_proxies().
These tests pin down that a GitHubClient hides it for its lifetime and restores
it on close(), and that the fix is not the (ineffective) class-level trust_env.
"""

import os
import unittest
from unittest import mock

import requests.sessions

from ci_context.github.client import (
    _ORIGINAL_ENVIRONMENT_PROXIES,
    GitHubClient,
    _empty_environment_proxies,
)


def _resolved_proxies() -> dict[str, str]:
    """Proxies a freshly-created requests Session would use for api.github.com."""
    settings = requests.sessions.Session().merge_environment_settings(
        "https://api.github.com", {}, None, None, None
    )
    return settings["proxies"]


class TestRegistryProxySuppression(unittest.TestCase):
    """GitHubClient must not let requests learn the Windows system proxy."""

    def test_proxy_lookup_suspended_while_client_alive(self) -> None:
        """While a client is open, even freshly created Sessions see no proxies."""
        with mock.patch("ci_context.github.client.Github"):
            client = GitHubClient("test-token")
            try:
                self.assertEqual(_resolved_proxies(), {})
                self.assertIs(
                    requests.sessions.get_environ_proxies,
                    _empty_environment_proxies,
                )
            finally:
                client.close()

    def test_proxy_lookup_restored_after_close(self) -> None:
        """close() must restore the original lookup, and stay safe on double close."""
        with mock.patch("ci_context.github.client.Github"):
            client = GitHubClient("test-token")
            client.close()
            # a second close (context manager + explicit) must not corrupt restore
            client.close()
        self.assertIs(
            requests.sessions.get_environ_proxies,
            _ORIGINAL_ENVIRONMENT_PROXIES,
        )

    def test_class_level_trust_env_patch_is_ineffective(self) -> None:
        """Guard the fix design: Session.trust_env is per-instance, not class-level."""
        with mock.patch("ci_context.github.client.Github"):
            client = GitHubClient("test-token")
            try:
                # Session.__init__ hardcodes trust_env=True on each instance,
                # so setting it at the class level would not affect Sessions
                # created afterwards (e.g. PyGithub's session, built lazily).
                self.assertTrue(requests.sessions.Session().trust_env)
                self.assertEqual(_resolved_proxies(), {})
            finally:
                client.close()

    def test_init_failure_restores_proxy_state(self) -> None:
        """A failing constructor must not leak the env strip or the lookup patch.

        close() (and any `with` block) is unreachable when __init__ raises, so
        the constructor itself is the only place that can undo what it patched.
        Without the cleanup, every later requests.Session in the test process
        would run proxyless even after the exception propagated.
        """
        env = {
            "HTTPS_PROXY": "http://proxy.example:8080",
            "https_proxy": "http://proxy.example:8080",
        }
        with mock.patch(
            "ci_context.github.client.Github",
            side_effect=RuntimeError("boom"),
        ), mock.patch.dict(os.environ, env, clear=False), self.assertRaises(RuntimeError):
            GitHubClient("test-token")
            # Inside the patch.dict scope: a leaked strip would make the
            # keys absent here in spite of patch.dict having set them on
            # the way into the test.
            for key, value in env.items():
                self.assertEqual(os.environ.get(key), value)
        self.assertIs(
            requests.sessions.get_environ_proxies,
            _ORIGINAL_ENVIRONMENT_PROXIES,
        )


if __name__ == "__main__":
    unittest.main()