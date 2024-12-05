from typing import Any, Dict, Optional, Callable, get_type_hints, Type, TypeVar
from pydantic import BaseModel, Field, validate_call

from phi.utils.log import logger

T = TypeVar("T")


class AgentRetry(Exception):
    """Exception raised when an agent should retry the function call."""

    pass


class Function(BaseModel):
    """Model for storing functions that can be called by an agent."""

    # The name of the function to be called.
    # Must be a-z, A-Z, 0-9, or contain underscores and dashes, with a maximum length of 64.
    name: str
    # A description of what the function does, used by the model to choose when and how to call the function.
    description: Optional[str] = None
    # The parameters the functions accepts, described as a JSON Schema object.
    # To describe a function that accepts no parameters, provide the value {"type": "object", "properties": {}}.
    parameters: Dict[str, Any] = Field(
        default_factory=lambda: {"type": "object", "properties": {}},
        description="JSON Schema object describing function parameters",
    )
    strict: Optional[bool] = None

    # The function to be called.
    entrypoint: Optional[Callable] = None
    # If True, the arguments are sanitized before being passed to the function.
    sanitize_arguments: bool = True
    # If True, the function call will show the result along with sending it to the model.
    show_result: bool = False
    # If True, the agent will stop after the function call.
    stop_after_tool_call: bool = False
    # Hook that runs before the function is executed.
    # If defined, can accept the FunctionCall instance as a parameter.
    pre_hook: Optional[Callable] = None
    # Hook that runs after the function is executed, regardless of success/failure.
    # If defined, can accept the FunctionCall instance as a parameter.
    post_hook: Optional[Callable] = None

    # --*-- FOR INTERNAL USE ONLY --*--
    # The agent that the function is associated with
    _agent: Optional[Any] = None

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump(exclude_none=True, include={"name", "description", "parameters", "strict"})

    @classmethod
    def from_callable(cls, c: Callable, strict: bool = False) -> "Function":
        from inspect import getdoc, signature
        from phi.utils.json_schema import get_json_schema

        function_name = c.__name__
        parameters = {"type": "object", "properties": {}, "required": []}
        try:
            sig = signature(c)
            type_hints = get_type_hints(c)

            # If function has an the agent argument, remove the agent parameter from the type hints
            if "agent" in sig.parameters:
                del type_hints["agent"]
            # logger.info(f"Type hints for {function_name}: {type_hints}")

            # Filter out return type and only process parameters
            param_type_hints = {
                name: type_hints[name]
                for name in sig.parameters
                if name in type_hints and name != "return" and name != "agent"
            }
            # logger.info(f"Arguments for {function_name}: {param_type_hints}")

            # Get JSON schema for parameters only
            parameters = get_json_schema(param_type_hints)

            # If strict=True mark all fields as required
            # See: https://platform.openai.com/docs/guides/structured-outputs/supported-schemas#all-fields-must-be-required
            if strict:
                parameters["required"] = [name for name in parameters["properties"] if name != "agent"]
            else:
                # Mark a field as required if it has no default value
                parameters["required"] = [
                    name
                    for name, param in sig.parameters.items()
                    if param.default == param.empty and name != "self" and name != "agent"
                ]

            # logger.debug(f"JSON schema for {function_name}: {parameters}")
        except Exception as e:
            logger.warning(f"Could not parse args for {function_name}: {e}", exc_info=True)

        return cls(
            name=function_name,
            description=getdoc(c),
            parameters=parameters,
            entrypoint=validate_call(c),
        )

    def process_entrypoint(self, strict: bool = False):
        """Process the entrypoint and make it ready for use by an agent."""
        from inspect import getdoc, signature
        from phi.utils.json_schema import get_json_schema

        if self.entrypoint is None:
            return

        parameters = {"type": "object", "properties": {}, "required": []}
        try:
            sig = signature(self.entrypoint)
            type_hints = get_type_hints(self.entrypoint)

            # If function has an the agent argument, remove the agent parameter from the type hints
            if "agent" in sig.parameters:
                del type_hints["agent"]
            # logger.info(f"Type hints for {self.name}: {type_hints}")

            # Filter out return type and only process parameters
            param_type_hints = {
                name: type_hints[name]
                for name in sig.parameters
                if name in type_hints and name != "return" and name != "agent"
            }
            # logger.info(f"Arguments for {self.name}: {param_type_hints}")

            # Get JSON schema for parameters only
            parameters = get_json_schema(param_type_hints)

            # If strict=True mark all fields as required
            # See: https://platform.openai.com/docs/guides/structured-outputs/supported-schemas#all-fields-must-be-required
            if strict:
                parameters["required"] = [name for name in parameters["properties"] if name != "agent"]
            else:
                # Mark a field as required if it has no default value
                parameters["required"] = [
                    name
                    for name, param in sig.parameters.items()
                    if param.default == param.empty and name != "self" and name != "agent"
                ]

            # logger.debug(f"JSON schema for {self.name}: {parameters}")
        except Exception as e:
            logger.warning(f"Could not parse args for {self.name}: {e}", exc_info=True)

        self.description = getdoc(self.entrypoint)
        self.parameters = parameters
        self.entrypoint = validate_call(self.entrypoint)

    def get_type_name(self, t: Type[T]):
        name = str(t)
        if "list" in name or "dict" in name:
            return name
        else:
            return t.__name__

    def get_definition_for_prompt_dict(self) -> Optional[Dict[str, Any]]:
        """Returns a function definition that can be used in a prompt."""

        if self.entrypoint is None:
            return None

        type_hints = get_type_hints(self.entrypoint)
        return_type = type_hints.get("return", None)
        returns = None
        if return_type is not None:
            returns = self.get_type_name(return_type)

        function_info = {
            "name": self.name,
            "description": self.description,
            "arguments": self.parameters.get("properties", {}),
            "returns": returns,
        }
        return function_info

    def get_definition_for_prompt(self) -> Optional[str]:
        """Returns a function definition that can be used in a prompt."""
        import json

        function_info = self.get_definition_for_prompt_dict()
        if function_info is not None:
            return json.dumps(function_info, indent=2)
        return None


class FunctionCall(BaseModel):
    """Model for Function Calls"""

    # The function to be called.
    function: Function
    # The arguments to call the function with.
    arguments: Optional[Dict[str, Any]] = None
    # The result of the function call.
    result: Optional[Any] = None
    # The ID of the function call.
    call_id: Optional[str] = None

    # Error while parsing arguments or running the function.
    error: Optional[str] = None

    def get_call_str(self) -> str:
        """Returns a string representation of the function call."""
        if self.arguments is None:
            return f"{self.function.name}()"

        trimmed_arguments = {}
        for k, v in self.arguments.items():
            if isinstance(v, str) and len(v) > 100:
                trimmed_arguments[k] = "..."
            else:
                trimmed_arguments[k] = v
        call_str = f"{self.function.name}({', '.join([f'{k}={v}' for k, v in trimmed_arguments.items()])})"
        return call_str

    def execute(self) -> bool:
        """Runs the function call.

        Returns True if the function call was successful, False otherwise.
        The result of the function call is stored in self.result.
        """
        from inspect import signature

        if self.function.entrypoint is None:
            return False

        logger.debug(f"Running: {self.get_call_str()}")

        # Execute pre-hook if it exists
        if self.function.pre_hook is not None:
            try:
                # Check if the pre-hook has and agent argument
                if "agent" in signature(self.function.pre_hook).parameters:
                    self.function.pre_hook(self, agent=self.function._agent)
                else:
                    self.function.pre_hook(self)
            except AgentRetry as e:
                logger.debug(f"Agent retry requested: {e}")
                self.error = str(e)
                return False
            except Exception as e:
                logger.warning(f"Error in pre-hook callback: {e}")
                logger.exception(e)

        success = False
        # Call the function with no arguments if none are provided.
        if self.arguments is None:
            try:
                # Check if the function has an agent argument
                if "agent" in signature(self.function.entrypoint).parameters:
                    self.result = self.function.entrypoint(agent=self.function._agent)
                else:
                    self.result = self.function.entrypoint()
                success = True
            except Exception as e:
                logger.warning(f"Could not run function {self.get_call_str()}")
                logger.exception(e)
                self.error = str(e)
                return False
        else:
            # Call the function with the provided arguments
            try:
                # Check if the function has an agent argument
                if "agent" in signature(self.function.entrypoint).parameters:
                    self.result = self.function.entrypoint(**self.arguments, agent=self.function._agent)
                else:
                    self.result = self.function.entrypoint(**self.arguments)
                success = True
            except Exception as e:
                logger.warning(f"Could not run function {self.get_call_str()}")
                logger.exception(e)
                self.error = str(e)
                return False

        # Execute post-hook if it exists
        if self.function.post_hook is not None:
            try:
                # Check if the post-hook has an agent argument
                if "agent" in signature(self.function.post_hook).parameters:
                    self.function.post_hook(self, agent=self.function._agent)
                else:
                    self.function.post_hook(self)
            except AgentRetry as e:
                logger.debug(f"Agent retry requested: {e}")
                self.error = str(e)
                return False
            except Exception as e:
                logger.warning(f"Error in post-hook callback: {e}")
                logger.exception(e)

        return success
