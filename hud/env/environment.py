"""Base classes for environment implementations."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from hud.env.env_client import EnvClient
from hud.task import Task
from hud.utils import HudStyleConfig, expand_config
from hud.utils.config import ExpandedConfig

if TYPE_CHECKING:
    from hud.adapters.common.types import CLA

logger = logging.getLogger("hud.environment")


class Observation(BaseModel):
    """
    Observation from the environment.

    Attributes:
        screenshot: Base64 encoded PNG string of the screen
        text: Text observation, if available
    """

    screenshot: str | None = None  # base64 string png
    text: str | None = None


class Environment(BaseModel):
    """
    Environment base class that provides common functionality for all environment implementations.
    This class uses the primitives provided by EnvClient to implement core environment operations.
    """

    metadata: dict[str, Any]
    client: EnvClient
    _preloaded_setup: list[ExpandedConfig] = []
    _preloaded_evaluate: list[ExpandedConfig] = []
    url: str | None = None
    live_url: str | None = None
    # The task id to use for the environment reset
    task: Task | None = None

    def preload_setup(self, setup_config: HudStyleConfig) -> None:
        """Preload setup configuration from a Task.
        This will be run when the environment is reset.

        Args:
            setup_config: The setup configuration, which can be:
                - String (function name): "chrome.maximize"
                - String (function with args): "chrome.activate_tab 5"
                - Dict: {"function": [args]} where args are strings/ints/dicts
                - List of the above
        """
        self._preloaded_setup = expand_config(setup_config)

    def preload_evaluate(self, evaluate_config: HudStyleConfig) -> None:
        """Preload evaluation configuration from a Task.
        This will be run when the environment is evaluated.
        Args:
            evaluate_config: The evaluation configuration, which can be:
                - String (function name): "chrome.is_open"
                - String (function with args): "chrome.active_tab github.com"
                - Dict: {"function": [args]} where args are strings/ints/dicts
                - List of the above
        """
        self._preloaded_evaluate = expand_config(evaluate_config)

    async def reset(
        self,
        *,
        setup_config: HudStyleConfig | None = None,
    ) -> tuple[Observation, dict[str, Any]]:
        """Reset the environment.

        Args:
            task_id: The task id to include in the reset
            setup_config: The setup configuration to run

        Returns:
            Any: Result of the setup function
        """
        configs = self._preloaded_setup if setup_config is None else expand_config(setup_config)

        if not configs:
            logger.warning("Empty setup configuration")

        # Execute each config and collect results
        results = []
        for config in configs:
            result, stdout, stderr = await self.client.invoke(config)
            if stdout:
                logger.info("Setup produced stdout: %s", stdout.decode())
            if stderr:
                logger.warning("Setup produced stderr: %s", stderr.decode())
            results.append(result)

        # now reset
        obs, stdout, stderr = await self.client.invoke(
            ExpandedConfig(
                function="reset",
                args=[
                    {
                        "task_id": self.task.id,
                    }
                    if self.task
                    else {}
                ],
            )
        )

        if stdout:
            logger.info("Reset produced stdout: %s", stdout.decode())
        if stderr:
            logger.warning("Reset produced stderr: %s", stderr.decode())

        info = {
            "results": results,
        }

        # Return list of results
        return Observation.model_validate(obs), info

    async def step(self, actions: list[CLA]) -> tuple[Observation, float, bool, dict[str, Any]]:
        """Execute a step in the environment.

        Args:
            action: The action to execute

        Returns:
            Any: Result of the step execution
        """

        result, stdout, stderr = await self.client.invoke(
            ExpandedConfig(function="step", args=[[action.model_dump() for action in actions]])
        )
        if stdout:
            logger.info("Step produced stdout: %s", stdout.decode())
        if stderr:
            logger.warning("Step produced stderr: %s", stderr.decode())


        observation = Observation.model_validate(result["observation"], strict=True)


        return observation, 0, False, {}

    async def evaluate(self, evaluate_config: HudStyleConfig | None = None) -> Any:
        """Run an evaluation function in the environment.

        Args:
            evaluate_config: The evaluation configuration to run

        Returns:
            Any: Result of the evaluation function
        """
        configs = (
            self._preloaded_evaluate if evaluate_config is None else expand_config(evaluate_config)
        )

        if not configs:
            logger.warning("Empty evaluation configuration")

        # Execute each config and collect results
        results = []
        for config in configs:
            result, stdout, stderr = await self.client.invoke(config)
            if stdout:
                logger.info("Evaluation produced stdout: %s", stdout.decode())
            if stderr:
                logger.warning("Evaluation produced stderr: %s", stderr.decode())
            results.append(result)

        # Now invoke the evaluate command
        evaluate_result, stdout, stderr = await self.client.invoke(
            ExpandedConfig(function="evaluate", args=[])
        )
        if stdout:
            logger.info("Evaluate command produced stdout: %s", stdout.decode())
        if stderr:
            logger.warning("Evaluate command produced stderr: %s", stderr.decode())

        # Return list of results plus the evaluate command result
        return [*results, evaluate_result]

    async def get_vnc_url(self) -> str | None:
        """
        Get the VNC URL for the environment.

        Returns:
            str: The VNC URL for remote viewing/control
        """
        if self.live_url is None:
            await self.get_urls()
        return self.live_url

    async def get_urls(self) -> dict[str, Any]:
        """Get URLs for the environment.

        Returns:
            dict: Dictionary of URLs for accessing the environment
        """
        data, _, _ = await self.client.invoke(ExpandedConfig(function="get_urls", args=[]))

        self.url = data.get("url")
        self.live_url = data.get("live_url")

        return {
            "url": self.url,
            "live_url": self.live_url,
        }

    async def close(self) -> None:
        """Close the environment.

        This should release any resources and clean up the environment.
        """
        await self.client.close()
