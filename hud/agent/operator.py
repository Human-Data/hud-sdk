import json
import logging
import os
from typing import Any, Literal, cast

from openai import OpenAI
from openai.types.responses import (
    ToolParam,
    ResponseInputParam,
    ResponseInputItemParam,
    ResponseOutputMessage,
    ResponseComputerToolCall
)

from hud.agent.base import Agent
from hud.adapters.operator import OperatorAdapter
from hud.env.environment import Observation
from hud.settings import settings

logger = logging.getLogger(__name__)

class OperatorAgent(Agent[OpenAI, dict[str, Any]]):
    """
    An agent implementation using OpenAI's Computer Use API.
    
    This agent interacts with HUD environments using OpenAI's Computer Use API
    through the OperatorAdapter which converts actions to the format expected by HUD.
    """
    
    def __init__(
        self, 
        client: OpenAI | None = None,
        model: str = "computer-use-preview",
        environment: Literal["windows", "mac", "linux", "browser"] = "windows",
        adapter: OperatorAdapter | None = None,
        max_iterations: int = 8
    ):
        """
        Initialize the OperatorAgent.
        
        Args:
            client: The OpenAI client for API calls (optional, created automatically if not provided)
            model: The model to use for computer use
            environment: The environment type (windows, mac, linux, browser)
            adapter: The adapter to use for preprocessing and postprocessing
            max_iterations: Maximum number of iterations for the agent
        """
        # Initialize client if not provided
        if client is None:
            # Get API key from settings
            api_key = settings.openai_api_key
            if not api_key:
                raise ValueError("OpenAI API key not found in settings or environment variables. Set OPENAI_API_KEY.")
            
            # Create synchronous client
            client = OpenAI(api_key=api_key)
        
        super().__init__(client=client, adapter=adapter)
        
        self.model = model
        self.environment = environment
        self.max_iterations = max_iterations
        
        # Default dimensions
        self.width = 1024
        self.height = 768
        
        # Update dimensions if adapter is provided
        if self.adapter:
            self.width = self.adapter.agent_width
            self.height = self.adapter.agent_height
        
        # Message history and state tracking
        self.last_response_id = None
        self.pending_call_id = None
        self.initial_prompt = None
    
    async def fetch_response(self, observation: Observation) -> tuple[list[dict[str, Any]], bool]:
        """
        Fetch a response from the model based on the observation.
        
        Args:
            observation: The preprocessed observation
            
        Returns:
            tuple[list[dict[str, Any]], bool]: A tuple containing the list of raw actions and a
                                             boolean indicating if the agent believes the task is complete
        """
        if not self.client:
            raise ValueError("Client is required")
        
        # Define the computer use tool with correct type using cast
        computer_tool = cast(ToolParam, {
            "type": "computer_use_preview",
            "display_width": self.width,
            "display_height": self.height,
            "environment": self.environment
        })
        
        # Process the observation based on whether it's the first one or a response to an action
        if self.pending_call_id is None and self.last_response_id is None:
            # This is the first observation, store and send the prompt
            self.initial_prompt = observation.text
            
            # Create the initial request following the required structure
            input_content: list[dict[str, Any]] = [
                {"type": "input_text", "text": observation.text or ""}
            ]
            
            # Add screenshot if present
            if observation.screenshot:
                input_content.append({
                    "type": "input_image",
                    "image_url": f"data:image/png;base64,{observation.screenshot}"
                })
            
            # Structure the input correctly for the API using cast
            input_param = cast(ResponseInputParam, [{
                "role": "user",
                "content": input_content
            }])
            
            # Call OpenAI API for the initial prompt (synchronous call)
            response = self.client.responses.create(
                model=self.model,
                tools=[computer_tool],
                input=input_param, 
                truncation="auto"
            )
            
        else:
            # This is a response to a previous action
            if not observation.screenshot:
                logger.warning("No screenshot provided for response to action")
                return [], True
                
            # Create a response to the previous action with the new screenshot
            input_param_followup = cast(ResponseInputParam, [
                    cast(ResponseInputItemParam, {
                        "call_id": self.pending_call_id,
                        "type": "computer_call_output",
                        "output": {
                            "type": "input_image",
                            "image_url": f"data:image/png;base64,{observation.screenshot}"
                        }
                    })
                ])
            
            # Call OpenAI API for follow-up (synchronous call)
            response = self.client.responses.create(
                model=self.model,
                previous_response_id=self.last_response_id,
                tools=[computer_tool],
                input=input_param_followup,
                truncation="auto"
            )
        
        # Store the response ID for the next call
        self.last_response_id = response.id
        
        # Process the response to extract computer calls
        actions = []
        done = True  # Assume we're done unless we find a computer call
        
        # Loop through all items in the output to find computer_call items
        computer_calls = [
            item for item in response.output 
            if isinstance(item, ResponseComputerToolCall) and item.type == "computer_call"
        ]
        
        if computer_calls:
            # Extract the computer calls and mark that we're not done
            done = False
            
            # Process all computer calls
            for computer_call in computer_calls:
                self.pending_call_id = computer_call.call_id
                action = computer_call.action
                actions.append(action.model_dump())
                
                # Log the action
                logger.info(f"Computer call action: {action}")
        else:
            # If there are no computer calls, print some debug info
            logger.info("No computer call found in the response. Either complete or error.")
            for item in response.output:
                if isinstance(item, ResponseOutputMessage) and item.type == "message":
                    logger.info(f"Message: {item.content}")
        
        return actions, done 