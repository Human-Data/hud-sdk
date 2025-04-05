"""Base classes for environment implementations."""

from __future__ import annotations

import abc
import json
import logging
import uuid
from typing import Any, Optional, Union

from pydantic import BaseModel

from hud.env.env_client import EnvClient
from hud.types import EnvironmentStatus
from hud.utils import HudStyleConfig, expand_config
from hud.utils.config import ExpandedConfig

logger = logging.getLogger("hud.environment")


class Observation(BaseModel):
    """
    Observation from the environment.

    Attributes:
        screenshot: Base64 encoded PNG string of the screen
        text: Text observation, if available
    """

    screenshot: Optional[str] = None  # base64 string png
    text: Optional[str] = None


status_messages = {
    EnvironmentStatus.RUNNING.value: "is running",
    EnvironmentStatus.ERROR.value: "had an error initializing",
    EnvironmentStatus.COMPLETED.value: "completed",
}

def invoke_template(config: ExpandedConfig, package_name: str, divider: str) -> str:
    """
    Return a python script to run the given config.
    """

    # the reason we json dumps twice is to escape the json string
    module_str = ".".join([package_name] + config["module"])
    func_str = config["function"]
    return f"""import json
from {module_str} import {func_str}
args = json.loads({json.dumps(json.dumps(config["args"]))})
result = {func_str}(*args)
result_str = json.dumps(result)
print("{divider}")
print(result_str)
"""

class InvokeError(Exception):
    """
    Error raised when an invoke fails.
    """
    pass

class Environment(BaseModel):
    """
    Environment base class that provides common functionality for all environment implementations.
    This class uses the primitives provided by EnvClient to implement core environment operations.
    """

    metadata: dict[str, Any]
    client: EnvClient
    _preloaded_setup: list[ExpandedConfig] = []
    _preloaded_evaluate: list[ExpandedConfig] = []

    def preload_setup(self, setup_config: HudStyleConfig) -> None:
        """Preload setup configuration from a Task.
        
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
        
        Args:
            evaluate_config: The evaluation configuration, which can be:
                - String (function name): "chrome.is_open"
                - String (function with args): "chrome.active_tab github.com"
                - Dict: {"function": [args]} where args are strings/ints/dicts
                - List of the above
        """
        self._preloaded_evaluate = expand_config(evaluate_config)  

    async def setup(self, setup_config: Optional[HudStyleConfig] = None) -> Any:
        """Run a setup function in the environment.
        
        Args:
            setup_config: The setup configuration to run
            
        Returns:
            Any: Result of the setup function
        """
        configs = self._preloaded_setup if setup_config is None else expand_config(setup_config)
        
        if not configs:
            logger.warning("Empty setup configuration")
            return []
            
        # Execute each config and collect results
        results = []
        for config in configs:
            result, stdout, stderr = await self.invoke(config)
            if stdout:
                logger.info("Setup produced stdout: %s", stdout.decode())
            if stderr:
                logger.warning("Setup produced stderr: %s", stderr.decode())
            results.append(result)
            
        # Return list of results
        return results

    async def step(self, action: str) -> tuple[Observation, float, bool, dict[str, Any]]:
        """Execute a step in the environment.
        
        Args:
            action: The action to execute
            
        Returns:
            Any: Result of the step execution
        """
        observation, stdout, stderr = await self.invoke(ExpandedConfig(
            module=[],
            function="step",
            args=[action]
        ))
        if stdout:
            logger.info("Step produced stdout: %s", stdout.decode())
        if stderr:
            logger.warning("Step produced stderr: %s", stderr.decode())
        
        return observation, 0, False, {}

    async def evaluate(self, evaluate_config: Optional[HudStyleConfig] = None) -> Any:
        """Run an evaluation function in the environment.
        
        Args:
            evaluate_config: The evaluation configuration to run
            
        Returns:
            Any: Result of the evaluation function
        """
        configs = self._preloaded_evaluate if evaluate_config is None else expand_config(evaluate_config)
        
        if not configs:
            logger.warning("Empty evaluation configuration")
            return []
            
        # Execute each config and collect results
        results = []
        for config in configs:
            result, stdout, stderr = await self.invoke(config)
            if stdout:
                logger.info("Evaluation produced stdout: %s", stdout.decode())
            if stderr:
                logger.warning("Evaluation produced stderr: %s", stderr.decode())
            results.append(result)
            
        # Return list of results
        return results

    async def invoke(self, config: ExpandedConfig) -> tuple[Any, bytes, bytes]:
        """Invoke a function in the environment.
        
        Args:
            config: The configuration to invoke
        """

        if await self.client.needs_update():
            logger.info("Environment needs update, updating")
            await self.client.update()

        # generate a random uuid as a divider   
        divider = str(uuid.uuid4())

        template = invoke_template(config, self.client.package_name, divider)
        logger.debug("Invoking template: %s", template)

        result = await self.client.execute(["python", "-c", template])

        # parse the result
        # we take the whole stderr as the stderr, and the stdout is the result pre-divider
        stderr = result["stderr"]
        stdout_parts = result["stdout"].split(divider.encode())
        stdout = stdout_parts[0]

        # parse the json part of the stdout (if it exists)
        if len(stdout_parts) > 1:
            result = json.loads(stdout_parts[1])
        else:
            raise InvokeError(stdout, stderr)

        return result, stdout, stderr


    async def close(self) -> None:
        """Close the environment.
        
        This should release any resources and clean up the environment.
        """
        await self.client.close()

