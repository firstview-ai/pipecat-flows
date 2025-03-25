#
# Copyright (c) 2024, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""LLM provider adapters for normalizing function and message formats.

This module provides adapters that normalize interactions between different
LLM providers (OpenAI, Anthropic, Gemini). It handles:
- Function name extraction
- Argument parsing
- Message content formatting
- Provider-specific schema conversion

The adapter system allows the flow manager to work with different LLM
providers while maintaining a consistent internal format.
"""

from typing import Any, Dict, List, Optional, Union

from loguru import logger
from pipecat.adapters.base_llm_adapter import BaseLLMAdapter
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.adapters.services.anthropic_adapter import AnthropicLLMAdapter
from pipecat.adapters.services.gemini_adapter import GeminiLLMAdapter
from pipecat.adapters.services.open_ai_adapter import OpenAILLMAdapter

from .types import FlowsFunctionSchema


class LLMAdapter:
    """Base adapter for LLM-specific format handling.

    Adapters normalize differences between LLM providers:
    - OpenAI: Uses function calling format
    - Anthropic: Uses native function format
    - Google: Uses function declarations format

    This allows the flow system to work consistently across
    different LLM providers while handling format differences
    internally.
    """

    def __init__(self):
        """Initialize the adapter."""
        self.provider_adapter: Optional[BaseLLMAdapter] = None

    def get_function_name(
        self, function_def: Union[Dict[str, Any], FunctionSchema, FlowsFunctionSchema]
    ) -> str:
        """Extract function name from provider-specific function definition or schema.

        Args:
            function_def: Provider-specific function definition or schema

        Returns:
            Function name
        """
        if isinstance(function_def, (FunctionSchema, FlowsFunctionSchema)):
            return function_def.name
        return self._get_function_name_from_dict(function_def)

    def _get_function_name_from_dict(self, function_def: Dict[str, Any]) -> str:
        """Extract function name from provider-specific function definition.

        Args:
            function_def: Provider-specific function definition dictionary

        Returns:
            Function name
        """
        raise NotImplementedError("Subclasses must implement this method")

    def get_function_args(self, function_call: Dict[str, Any]) -> dict:
        """Extract function arguments from provider-specific function call."""
        raise NotImplementedError("Subclasses must implement this method")

    def get_message_content(self, message: Dict[str, Any]) -> str:
        """Extract message content from provider-specific format."""
        raise NotImplementedError("Subclasses must implement this method")

    def format_functions(
        self,
        functions: List[Union[Dict[str, Any], FunctionSchema, FlowsFunctionSchema]],
        original_configs: Optional[List] = None,
    ) -> List[Dict[str, Any]]:
        """Format functions for provider-specific use.

        Args:
            functions: List of function definitions (dicts or schema objects)
            original_configs: Optional original node configs, used by some adapters

        Returns:
            List of functions formatted for the provider
        """
        # Convert to standard FunctionSchema objects for the ToolsSchema
        standard_functions = []

        for func in functions:
            if isinstance(func, FlowsFunctionSchema):
                # Extract just the FunctionSchema part for the LLM
                standard_functions.append(
                    FunctionSchema(
                        name=func.name,
                        description=func.description,
                        properties=func.properties,
                        required=func.required,
                    )
                )
            elif isinstance(func, FunctionSchema):
                # Already a standard FunctionSchema
                standard_functions.append(func)
            else:
                # Convert legacy dictionary format to FunctionSchema
                flows_schema = self.convert_to_function_schema(func)
                # Extract just the FunctionSchema part for the LLM
                standard_functions.append(
                    FunctionSchema(
                        name=flows_schema.name,
                        description=flows_schema.description,
                        properties=flows_schema.properties,
                        required=flows_schema.required,
                    )
                )

        # Create ToolsSchema with all functions
        tools_schema = ToolsSchema(standard_tools=standard_functions)

        # Use the provider adapter to format the functions
        return self.provider_adapter.to_provider_tools_format(tools_schema)

    def format_summary_message(self, summary: str) -> dict:
        """Format a summary as a message appropriate for this LLM provider.

        Args:
            summary: The generated summary text

        Returns:
            A properly formatted message for this provider
        """
        raise NotImplementedError("Subclasses must implement this method")

    async def generate_summary(
        self, llm: Any, summary_prompt: str, messages: List[dict]
    ) -> Optional[str]:
        """Generate a summary using the LLM provider's API directly.

        Args:
            llm: LLM service instance containing client/credentials
            summary_prompt: Prompt text to guide summary generation
            messages: List of messages to summarize

        Returns:
            Generated summary text, or None if generation fails
        """
        raise NotImplementedError("Subclasses must implement this method")

    def convert_to_function_schema(self, function_def: Dict[str, Any]) -> FlowsFunctionSchema:
        """Convert a provider-specific function definition to FlowsFunctionSchema.

        Args:
            function_def: Provider-specific function definition

        Returns:
            FlowsFunctionSchema equivalent
        """
        raise NotImplementedError("Subclasses must implement this method")


class OpenAIAdapter(LLMAdapter):
    """Format adapter for OpenAI.

    Handles OpenAI's function calling format, which is used as the default format
    in the flow system.
    """

    def __init__(self):
        """Initialize the OpenAI adapter."""
        super().__init__()
        self.provider_adapter = OpenAILLMAdapter()

    def _get_function_name_from_dict(self, function_def: Dict[str, Any]) -> str:
        """Extract function name from OpenAI function definition.

        Args:
            function_def: OpenAI-formatted function definition dictionary

        Returns:
            Function name from the definition
        """
        return function_def["function"]["name"]

    def get_function_args(self, function_call: Dict[str, Any]) -> dict:
        """Extract arguments from OpenAI function call.

        Args:
            function_call: OpenAI-formatted function call dictionary

        Returns:
            Dictionary of function arguments, empty if none provided
        """
        return function_call.get("arguments", {})

    def get_message_content(self, message: Dict[str, Any]) -> str:
        """Extract content from OpenAI message format.

        Args:
            message: OpenAI-formatted message dictionary

        Returns:
            Message content as string
        """
        return message["content"]

    def format_summary_message(self, summary: str) -> dict:
        """Format summary as a system message for OpenAI."""
        return {"role": "system", "content": f"Here's a summary of the conversation:\n{summary}"}

    async def generate_summary(
        self, llm: Any, summary_prompt: str, messages: List[dict]
    ) -> Optional[str]:
        """Generate summary using OpenAI's API directly."""
        try:
            prompt_messages = [
                {
                    "role": "system",
                    "content": summary_prompt,
                },
                {
                    "role": "user",
                    "content": f"Conversation history: {messages}",
                },
            ]

            # LLM completion
            response = await llm._client.chat.completions.create(
                model=llm.model_name,
                messages=prompt_messages,
                stream=False,
            )

            return response.choices[0].message.content

        except Exception as e:
            logger.error(f"OpenAI summary generation failed: {e}", exc_info=True)
            return None

    def convert_to_function_schema(self, function_def: Dict[str, Any]) -> FlowsFunctionSchema:
        """Convert OpenAI function definition to FlowsFunctionSchema.

        Args:
            function_def: OpenAI function definition

        Returns:
            FlowsFunctionSchema equivalent with flow-specific fields
        """
        func_data = function_def["function"]
        name = func_data["name"]
        description = func_data.get("description", "")
        parameters = func_data.get("parameters", {})
        properties = parameters.get("properties", {})
        required = parameters.get("required", [])

        # Extract Flows-specific fields
        handler = func_data.get("handler")
        transition_to = func_data.get("transition_to")
        transition_callback = func_data.get("transition_callback")

        return FlowsFunctionSchema(
            name=name,
            description=description,
            properties=properties,
            required=required,
            handler=handler,
            transition_to=transition_to,
            transition_callback=transition_callback,
        )


class AnthropicAdapter(LLMAdapter):
    """Format adapter for Anthropic.

    Handles Anthropic's native function format, converting between OpenAI's format
    and Anthropic's as needed.
    """

    def __init__(self):
        """Initialize the Anthropic adapter."""
        super().__init__()
        self.provider_adapter = AnthropicLLMAdapter()

    def _get_function_name_from_dict(self, function_def: Dict[str, Any]) -> str:
        """Extract function name from Anthropic function definition.

        Args:
            function_def: Anthropic-formatted function definition dictionary

        Returns:
            Function name from the definition
        """
        return function_def["name"]

    def get_function_args(self, function_call: Dict[str, Any]) -> dict:
        """Extract arguments from Anthropic function call.

        Args:
            function_call: Anthropic-formatted function call dictionary

        Returns:
            Dictionary of function arguments, empty if none provided
        """
        return function_call.get("arguments", {})

    def get_message_content(self, message: Dict[str, Any]) -> str:
        """Extract content from Anthropic message format.

        Handles both string content and structured content arrays.

        Args:
            message: Anthropic-formatted message dictionary

        Returns:
            Message content as string, concatenated if from multiple parts
        """
        if isinstance(message.get("content"), list):
            return " ".join(item["text"] for item in message["content"] if item["type"] == "text")
        return message.get("content", "")

    def format_summary_message(self, summary: str) -> dict:
        """Format summary as a user message for Anthropic."""
        return {"role": "user", "content": f"Here's a summary of the conversation:\n{summary}"}

    async def generate_summary(
        self, llm: Any, summary_prompt: str, messages: List[dict]
    ) -> Optional[str]:
        """Generate summary using Anthropic's API directly."""
        try:
            prompt_messages = [
                {
                    "role": "user",
                    "content": f"Conversation history: {messages}",
                },
            ]

            # LLM completion
            response = await llm._client.messages.create(
                model=llm.model_name,
                messages=prompt_messages,
                system=summary_prompt,
                max_tokens=8192,
                stream=False,
            )

            return response.content[0].text

        except Exception as e:
            logger.error(f"Anthropic summary generation failed: {e}", exc_info=True)
            return None

    def convert_to_function_schema(self, function_def: Dict[str, Any]) -> FlowsFunctionSchema:
        """Convert Anthropic function definition to FlowsFunctionSchema.

        Args:
            function_def: Anthropic function definition

        Returns:
            FlowsFunctionSchema equivalent with flow-specific fields
        """
        name = function_def["name"]
        description = function_def.get("description", "")
        input_schema = function_def.get("input_schema", {})
        properties = input_schema.get("properties", {})
        required = input_schema.get("required", [])

        # Extract Flows-specific fields
        handler = function_def.get("handler")
        transition_to = function_def.get("transition_to")
        transition_callback = function_def.get("transition_callback")

        return FlowsFunctionSchema(
            name=name,
            description=description,
            properties=properties,
            required=required,
            handler=handler,
            transition_to=transition_to,
            transition_callback=transition_callback,
        )


class GeminiAdapter(LLMAdapter):
    """Format adapter for Google's Gemini.

    Handles Gemini's function declarations format, converting between OpenAI's format
    and Gemini's as needed.
    """

    def __init__(self):
        """Initialize the Gemini adapter."""
        super().__init__()
        self.provider_adapter = GeminiLLMAdapter()

    def _get_function_name_from_dict(self, function_def: Dict[str, Any]) -> str:
        """Extract function name from Gemini function definition.

        Args:
            function_def: Gemini-formatted function definition dictionary

        Returns:
            Function name from the first declaration, or empty string if none found
        """
        logger.debug(f"Getting function name from: {function_def}")
        if "function_declarations" in function_def:
            declarations = function_def["function_declarations"]
            if declarations and isinstance(declarations, list):
                return declarations[0]["name"]
        return ""

    def get_function_args(self, function_call: Dict[str, Any]) -> dict:
        """Extract arguments from Gemini function call.

        Args:
            function_call: Gemini-formatted function call dictionary

        Returns:
            Dictionary of function arguments, empty if none provided
        """
        return function_call.get("args", {})

    def get_message_content(self, message: Dict[str, Any]) -> str:
        """Extract content from Gemini message format.

        Args:
            message: Gemini-formatted message dictionary

        Returns:
            Message content as string
        """
        return message["content"]

    def format_functions(
        self,
        functions: List[Union[Dict[str, Any], FunctionSchema, FlowsFunctionSchema]],
        original_configs: Optional[List] = None,
    ) -> List[Dict[str, Any]]:
        """Format functions for Gemini's specific use.

        This special implementation processes both converted schemas and original configs
        to ensure Gemini's specific format is preserved when possible.

        Args:
            functions: List of function definitions (dicts or schema objects)
            original_configs: Optional original node configs, used to preserve native formats

        Returns:
            List of functions formatted for Gemini
        """
        gemini_functions = []

        # If original_configs is provided, extract functions from it
        if original_configs:
            for func_config in original_configs:
                if isinstance(func_config, FlowsFunctionSchema):
                    # Convert FlowsFunctionSchema to Gemini format
                    gemini_functions.append(
                        {
                            "name": func_config.name,
                            "description": func_config.description,
                            "parameters": {
                                "type": "object",
                                "properties": func_config.properties,
                                "required": func_config.required,
                            },
                        }
                    )
                elif "function_declarations" in func_config:
                    # Already in Gemini format, use directly but remove handler/transition fields
                    for decl in func_config["function_declarations"]:
                        decl_copy = decl.copy()
                        if "handler" in decl_copy:
                            del decl_copy["handler"]
                        if "transition_to" in decl_copy:
                            del decl_copy["transition_to"]
                        if "transition_callback" in decl_copy:
                            del decl_copy["transition_callback"]
                        gemini_functions.append(decl_copy)
        else:
            # If no original configs, use the converted schemas
            for func in functions:
                if isinstance(func, (FunctionSchema, FlowsFunctionSchema)):
                    # Convert to Gemini format
                    gemini_functions.append(
                        {
                            "name": func.name,
                            "description": func.description,
                            "parameters": {
                                "type": "object",
                                "properties": func.properties,
                                "required": func.required,
                            },
                        }
                    )

        # Format as Gemini expects - an array with a single object containing function_declarations
        return [{"function_declarations": gemini_functions}]

    def format_summary_message(self, summary: str) -> dict:
        """Format summary as a user message for Gemini."""
        return {"role": "user", "content": f"Here's a summary of the conversation:\n{summary}"}

    async def generate_summary(
        self, llm: Any, summary_prompt: str, messages: List[dict]
    ) -> Optional[str]:
        """Generate summary using Google's API directly."""
        try:
            # Format messages for Gemini
            contents = [
                {
                    "role": "user",
                    "parts": [{"text": (f"{summary_prompt}\n\nConversation history: {messages}")}],
                }
            ]

            # Use non-streaming completion
            response = await llm._client.generate_content_async(contents=contents, stream=False)

            return response.text

        except Exception as e:
            logger.error(f"Google summary generation failed: {e}", exc_info=True)
            return None

    def convert_to_function_schema(self, function_def: Dict[str, Any]) -> FlowsFunctionSchema:
        """Convert Gemini function definition to FlowsFunctionSchema.

        Args:
            function_def: Gemini function definition

        Returns:
            FlowsFunctionSchema equivalent with flow-specific fields
        """
        if "function_declarations" in function_def:
            # Use first declaration if there are multiple
            decl = function_def["function_declarations"][0]
            # If we have function declarations, the handler might be in the declaration
            handler = decl.get("handler")
            transition_to = decl.get("transition_to")
            transition_callback = decl.get("transition_callback")
        else:
            decl = function_def
            # Otherwise, the handler might be at the top level
            handler = function_def.get("handler")
            transition_to = function_def.get("transition_to")
            transition_callback = function_def.get("transition_callback")

        name = decl["name"]
        description = decl.get("description", "")
        parameters = decl.get("parameters", {})
        properties = parameters.get("properties", {})
        required = parameters.get("required", [])

        return FlowsFunctionSchema(
            name=name,
            description=description,
            properties=properties,
            required=required,
            handler=handler,
            transition_to=transition_to,
            transition_callback=transition_callback,
        )


def create_adapter(llm) -> LLMAdapter:
    """Create appropriate adapter based on LLM service type.

    Uses string-based type checking to avoid importing unnecessary dependencies.

    Args:
        llm: LLM service instance

    Returns:
        LLMAdapter: Provider-specific adapter

    Raises:
        ValueError: If LLM type is not supported or required dependency not installed
    """
    llm_type = type(llm).__name__

    if llm_type == "OpenAILLMService":
        logger.debug("Creating OpenAI adapter")
        return OpenAIAdapter()

    if llm_type == "AnthropicLLMService":
        logger.debug("Creating Anthropic adapter")
        return AnthropicAdapter()

    if llm_type == "GoogleLLMService":
        logger.debug("Creating Google adapter")
        return GeminiAdapter()

    # If we get here, either the LLM type is not supported or the required dependency is not installed
    llm_type = type(llm).__name__
    error_msg = f"Unsupported LLM type or missing dependency: {llm_type}\n"
    error_msg += "Make sure you have installed the required dependency:\n"
    error_msg += "- For OpenAI: pip install 'pipecat-ai[openai]'\n"
    error_msg += "- For Anthropic: pip install 'pipecat-ai[anthropic]'\n"
    error_msg += "- For Google: pip install 'pipecat-ai[google]'"

    raise ValueError(error_msg)
