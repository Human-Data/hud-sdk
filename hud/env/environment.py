"""Base classes for environment implementations."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from hud.task import Task
from hud.utils import HudStyleConfig, create_evaluate_config, create_setup_config, expand_config
from hud.utils.config import ExpandedConfig

from .env_client import EnvClient

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
    url: str | None = None
    live_url: str | None = None
    # The task id to use for the environment reset
    task: Task | None = None

    async def _invoke_all(self, configs: HudStyleConfig | list[HudStyleConfig]) -> list[Any]:
        # Execute each config and collect results
        if not isinstance(configs, list):
            configs = [configs]
        results = []
        for config in configs:
            for expanded_config in expand_config(config):
                print(expanded_config)
                result, stdout, stderr = await self.client.invoke(expanded_config)
                results.append(result)
                if stdout:
                    logger.info(
                        "%s produced stdout:\n%s",
                        expanded_config.function,
                        stdout.decode(),
                    )
                if stderr:
                    logger.warning(
                        "%s produced stderr:\n%s",
                        expanded_config.function,
                        stderr.decode(),
                    )
        return results
    
    async def _setup(self, configs: HudStyleConfig | list[HudStyleConfig] | None = None) -> None:
        if configs:
            await self._invoke_all(create_setup_config(configs))
        elif self.task:
            await self._invoke_all(create_setup_config(self.task.setup))

    async def reset(self, configs: HudStyleConfig | list[HudStyleConfig] | None = None) -> tuple[Observation, dict[str, Any]]:
        """Returns the first observation from the environment.

        Returns:
            Observation: The first observation from the environment
            info: Dictionary of information about the environment
        """
        if configs:
            await self._setup(configs)
            obs, _, _, info = await self.step([])
            return obs, info
        elif self.task:
            await self._setup(self.task.setup)
            obs, _, _, info = await self.step([])
            return obs, info
        else:
            obs, _, _, info = await self.step([])
            return obs, info

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

    async def evaluate(self, config: HudStyleConfig | list[HudStyleConfig] | None = None, target: str | list[str] | None = None) -> Any:
        """Runs the task evaluation function in the environment.

        Returns:
            Any: Result of the evaluation function
        """
        if config:
            return await self._invoke_all(create_evaluate_config(config, target))
        elif self.task:
            return await self._invoke_all(create_evaluate_config(self.task.evaluate, self.task.target))
        else:
            raise ValueError("Either config or task must be provided")

    async def get_vnc_url(self) -> str | None:
        """
        Get the VNC URL for the environment.

        Returns:
            str: The VNC URL for remote viewing/control
        """
        if self.live_url is None:
            await self.get_urls()
        return f"http://{self.url}:5910/vnc.html"

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
