import json
import os
from pathlib import Path
from typing import Dict, Any, Optional, Union
from pydantic import BaseModel, Field, validator


class Endpoint(BaseModel):
    """Configuration model for a single API endpoint."""
    method: str = Field(..., description="HTTP method (case-sensitive, uppercase)")
    path: str = Field(..., description="Exact path to match")
    status: int = Field(..., description="HTTP status code to return")
    delay_ms: int = Field(..., description="Response delay in milliseconds")
    payload_inline: Optional[Dict[str, Any]] = Field(None, description="Inline JSON payload")
    payload_file: Optional[str] = Field(None, description="Path to external payload file")

    @validator('method')
    def validate_method(cls, v):
        """Ensure HTTP method is uppercase."""
        if not v.isupper():
            raise ValueError(f"HTTP method must be uppercase, got: {v}")
        return v

    @validator('status')
    def validate_status(cls, v):
        """Ensure status code is valid HTTP status."""
        if not (100 <= v <= 599):
            raise ValueError(f"HTTP status code must be between 100-599, got: {v}")
        return v

    @validator('delay_ms')
    def validate_delay(cls, v):
        """Ensure delay is non-negative."""
        if v < 0:
            raise ValueError(f"Delay must be non-negative, got: {v}")
        return v

    def __init__(self, **data):
        super().__init__(**data)
        # Ensure exactly one payload type is specified
        has_inline = self.payload_inline is not None
        has_file = self.payload_file is not None
        
        if not has_inline and not has_file:
            raise ValueError("Either payload_inline or payload_file must be specified")
        if has_inline and has_file:
            raise ValueError("Cannot specify both payload_inline and payload_file")


class Config(BaseModel):
    """Configuration model for the entire application."""
    endpoints: list[Endpoint] = Field(..., description="List of endpoint configurations")

    @validator('endpoints')
    def validate_endpoints_not_empty(cls, v):
        """Ensure at least one endpoint is configured."""
        if not v:
            raise ValueError("At least one endpoint must be configured")
        return v


def load_config(config_path: str) -> Config:
    """
    Load and validate configuration from JSON file.
    
    Args:
        config_path: Path to the configuration JSON file
        
    Returns:
        Validated Config object
        
    Raises:
        SystemExit: On configuration errors (with clean error messages)
    """
    try:
        # Check if config file exists
        if not os.path.exists(config_path):
            print(f"Error: Configuration file not found: {config_path}")
            raise SystemExit(1)
        
        # Load and parse JSON
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                config_data = json.load(f)
        except json.JSONDecodeError as e:
            print(f"Error: Invalid JSON in configuration file: {e}")
            raise SystemExit(1)
        except Exception as e:
            print(f"Error: Could not read configuration file: {e}")
            raise SystemExit(1)
        
        # Validate configuration structure
        try:
            config = Config(**config_data)
        except Exception as e:
            print(f"Error: Invalid configuration: {e}")
            raise SystemExit(1)
        
        # Validate payload files exist
        _validate_payload_files(config)
        
        return config
        
    except SystemExit:
        raise
    except Exception as e:
        print(f"Error: Unexpected error loading configuration: {e}")
        raise SystemExit(1)


def _validate_payload_files(config: Config) -> None:
    """
    Validate that all referenced payload files exist.
    
    Args:
        config: Validated configuration object
        
    Raises:
        SystemExit: If any payload file is missing
    """
    missing_files = []
    
    for endpoint in config.endpoints:
        if endpoint.payload_file:
            if not os.path.exists(endpoint.payload_file):
                missing_files.append(endpoint.payload_file)
    
    if missing_files:
        print("Error: Missing payload files:")
        for file_path in missing_files:
            print(f"  - {file_path}")
        raise SystemExit(1)


def load_payload_file(file_path: str) -> Dict[str, Any]:
    """
    Load JSON payload from file.
    
    Args:
        file_path: Path to the payload JSON file
        
    Returns:
        Parsed JSON data
        
    Raises:
        SystemExit: On file loading or JSON parsing errors
    """
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON in payload file {file_path}: {e}")
        raise SystemExit(1)
    except Exception as e:
        print(f"Error: Could not read payload file {file_path}: {e}")
        raise SystemExit(1)
