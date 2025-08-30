import json
import os
import tempfile
import pytest
from unittest.mock import patch, mock_open
from httpmocker.config_loader import Config, Endpoint, load_config, load_payload_file, _validate_payload_files


class TestEndpoint:
    """Test cases for Endpoint model validation."""
    
    def test_valid_endpoint_with_inline_payload(self):
        """Test creating valid endpoint with inline payload."""
        endpoint = Endpoint(
            method="GET",
            path="/api/test",
            status=200,
            delay_ms=100,
            payload_inline={"message": "test"}
        )
        assert endpoint.method == "GET"
        assert endpoint.path == "/api/test"
        assert endpoint.status == 200
        assert endpoint.delay_ms == 100
        assert endpoint.payload_inline == {"message": "test"}
        assert endpoint.payload_file is None
    
    def test_valid_endpoint_with_file_payload(self):
        """Test creating valid endpoint with file payload."""
        endpoint = Endpoint(
            method="POST",
            path="/api/users",
            status=201,
            delay_ms=0,
            payload_file="payloads/user.json"
        )
        assert endpoint.method == "POST"
        assert endpoint.path == "/api/users"
        assert endpoint.status == 201
        assert endpoint.delay_ms == 0
        assert endpoint.payload_file == "payloads/user.json"
        assert endpoint.payload_inline is None
    
    def test_method_must_be_uppercase(self):
        """Test that HTTP method must be uppercase."""
        with pytest.raises(ValueError, match="HTTP method must be uppercase"):
            Endpoint(
                method="get",
                path="/api/test",
                status=200,
                delay_ms=0,
                payload_inline={"test": "data"}
            )
    
    def test_invalid_status_code_too_low(self):
        """Test that status code must be >= 100."""
        with pytest.raises(ValueError, match="HTTP status code must be between 100-599"):
            Endpoint(
                method="GET",
                path="/api/test",
                status=99,
                delay_ms=0,
                payload_inline={"test": "data"}
            )
    
    def test_invalid_status_code_too_high(self):
        """Test that status code must be <= 599."""
        with pytest.raises(ValueError, match="HTTP status code must be between 100-599"):
            Endpoint(
                method="GET",
                path="/api/test",
                status=600,
                delay_ms=0,
                payload_inline={"test": "data"}
            )
    
    def test_negative_delay(self):
        """Test that delay must be non-negative."""
        with pytest.raises(ValueError, match="Delay must be non-negative"):
            Endpoint(
                method="GET",
                path="/api/test",
                status=200,
                delay_ms=-1,
                payload_inline={"test": "data"}
            )
    
    def test_missing_both_payloads(self):
        """Test that either payload_inline or payload_file must be specified."""
        with pytest.raises(ValueError, match="Either payload_inline or payload_file must be specified"):
            Endpoint(
                method="GET",
                path="/api/test",
                status=200,
                delay_ms=0
            )
    
    def test_both_payloads_specified(self):
        """Test that both payload types cannot be specified."""
        with pytest.raises(ValueError, match="Cannot specify both payload_inline and payload_file"):
            Endpoint(
                method="GET",
                path="/api/test",
                status=200,
                delay_ms=0,
                payload_inline={"test": "data"},
                payload_file="test.json"
            )


class TestConfig:
    """Test cases for Config model validation."""
    
    def test_valid_config(self):
        """Test creating valid configuration."""
        endpoints = [
            Endpoint(
                method="GET",
                path="/api/test",
                status=200,
                delay_ms=0,
                payload_inline={"test": "data"}
            )
        ]
        config = Config(endpoints=endpoints)
        assert len(config.endpoints) == 1
        assert config.endpoints[0].method == "GET"
    
    def test_empty_endpoints_list(self):
        """Test that at least one endpoint must be configured."""
        with pytest.raises(ValueError, match="At least one endpoint must be configured"):
            Config(endpoints=[])


class TestLoadConfig:
    """Test cases for load_config function."""
    
    def test_load_valid_config(self):
        """Test loading valid configuration file."""
        config_data = {
            "endpoints": [
                {
                    "method": "GET",
                    "path": "/api/test",
                    "status": 200,
                    "delay_ms": 100,
                    "payload_inline": {"message": "test"}
                }
            ]
        }
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(config_data, f)
            config_path = f.name
        
        try:
            config = load_config(config_path)
            assert len(config.endpoints) == 1
            assert config.endpoints[0].method == "GET"
            assert config.endpoints[0].path == "/api/test"
        finally:
            os.unlink(config_path)
    
    def test_config_file_not_found(self):
        """Test handling of missing configuration file."""
        with pytest.raises(SystemExit) as exc_info:
            load_config("nonexistent.json")
        assert exc_info.value.code == 1
    
    def test_invalid_json(self):
        """Test handling of invalid JSON in config file."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            f.write('{"invalid": json}')  # Invalid JSON
            config_path = f.name
        
        try:
            with pytest.raises(SystemExit) as exc_info:
                load_config(config_path)
            assert exc_info.value.code == 1
        finally:
            os.unlink(config_path)
    
    def test_invalid_config_structure(self):
        """Test handling of invalid configuration structure."""
        config_data = {
            "endpoints": [
                {
                    "method": "get",  # Invalid: lowercase
                    "path": "/api/test",
                    "status": 200,
                    "delay_ms": 0,
                    "payload_inline": {"test": "data"}
                }
            ]
        }
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(config_data, f)
            config_path = f.name
        
        try:
            with pytest.raises(SystemExit) as exc_info:
                load_config(config_path)
            assert exc_info.value.code == 1
        finally:
            os.unlink(config_path)
    
    def test_missing_payload_file(self):
        """Test handling of missing payload files."""
        config_data = {
            "endpoints": [
                {
                    "method": "GET",
                    "path": "/api/test",
                    "status": 200,
                    "delay_ms": 0,
                    "payload_file": "nonexistent.json"
                }
            ]
        }
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(config_data, f)
            config_path = f.name
        
        try:
            with pytest.raises(SystemExit) as exc_info:
                load_config(config_path)
            assert exc_info.value.code == 1
        finally:
            os.unlink(config_path)
    
    def test_config_with_existing_payload_files(self):
        """Test configuration with existing payload files."""
        # Create payload file
        payload_data = {"users": [{"id": 1, "name": "test"}]}
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as payload_file:
            json.dump(payload_data, payload_file)
            payload_path = payload_file.name
        
        # Create config file
        config_data = {
            "endpoints": [
                {
                    "method": "GET",
                    "path": "/api/users",
                    "status": 200,
                    "delay_ms": 0,
                    "payload_file": payload_path
                }
            ]
        }
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as config_file:
            json.dump(config_data, config_file)
            config_path = config_file.name
        
        try:
            config = load_config(config_path)
            assert len(config.endpoints) == 1
            assert config.endpoints[0].payload_file == payload_path
        finally:
            os.unlink(config_path)
            os.unlink(payload_path)


class TestValidatePayloadFiles:
    """Test cases for _validate_payload_files function."""
    
    def test_validate_existing_files(self):
        """Test validation with existing payload files."""
        # Create temporary payload file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump({"test": "data"}, f)
            payload_path = f.name
        
        try:
            endpoint = Endpoint(
                method="GET",
                path="/api/test",
                status=200,
                delay_ms=0,
                payload_file=payload_path
            )
            config = Config(endpoints=[endpoint])
            
            # Should not raise any exception
            _validate_payload_files(config)
        finally:
            os.unlink(payload_path)
    
    def test_validate_missing_files(self):
        """Test validation with missing payload files."""
        endpoint = Endpoint(
            method="GET",
            path="/api/test",
            status=200,
            delay_ms=0,
            payload_file="nonexistent.json"
        )
        config = Config(endpoints=[endpoint])
        
        with pytest.raises(SystemExit) as exc_info:
            _validate_payload_files(config)
        assert exc_info.value.code == 1
    
    def test_validate_inline_payloads_only(self):
        """Test validation with only inline payloads (no files to check)."""
        endpoint = Endpoint(
            method="GET",
            path="/api/test",
            status=200,
            delay_ms=0,
            payload_inline={"test": "data"}
        )
        config = Config(endpoints=[endpoint])
        
        # Should not raise any exception
        _validate_payload_files(config)


class TestLoadPayloadFile:
    """Test cases for load_payload_file function."""
    
    def test_load_valid_payload_file(self):
        """Test loading valid JSON payload file."""
        payload_data = {"users": [{"id": 1, "name": "Alice"}]}
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(payload_data, f)
            payload_path = f.name
        
        try:
            loaded_data = load_payload_file(payload_path)
            assert loaded_data == payload_data
        finally:
            os.unlink(payload_path)
    
    def test_load_invalid_json_payload(self):
        """Test handling of invalid JSON in payload file."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            f.write('{"invalid": json}')  # Invalid JSON
            payload_path = f.name
        
        try:
            with pytest.raises(SystemExit) as exc_info:
                load_payload_file(payload_path)
            assert exc_info.value.code == 1
        finally:
            os.unlink(payload_path)
    
    def test_load_nonexistent_payload_file(self):
        """Test handling of missing payload file."""
        with pytest.raises(SystemExit) as exc_info:
            load_payload_file("nonexistent.json")
        assert exc_info.value.code == 1


class TestEdgeCases:
    """Test edge cases and boundary conditions."""
    
    def test_endpoint_with_zero_delay(self):
        """Test endpoint with zero delay."""
        endpoint = Endpoint(
            method="GET",
            path="/api/instant",
            status=200,
            delay_ms=0,
            payload_inline={"instant": True}
        )
        assert endpoint.delay_ms == 0
    
    def test_endpoint_with_large_delay(self):
        """Test endpoint with large delay value."""
        endpoint = Endpoint(
            method="GET",
            path="/api/slow",
            status=200,
            delay_ms=60000,  # 1 minute
            payload_inline={"slow": True}
        )
        assert endpoint.delay_ms == 60000
    
    def test_endpoint_with_empty_path(self):
        """Test endpoint with empty path."""
        endpoint = Endpoint(
            method="GET",
            path="",
            status=200,
            delay_ms=0,
            payload_inline={"root": True}
        )
        assert endpoint.path == ""
    
    def test_endpoint_with_complex_path(self):
        """Test endpoint with complex path including special characters."""
        endpoint = Endpoint(
            method="GET",
            path="/api/users/123/posts?filter=active&sort=date",
            status=200,
            delay_ms=0,
            payload_inline={"complex": True}
        )
        assert endpoint.path == "/api/users/123/posts?filter=active&sort=date"
    
    def test_config_with_multiple_endpoints(self):
        """Test configuration with multiple endpoints."""
        endpoints = [
            Endpoint(
                method="GET",
                path="/api/users",
                status=200,
                delay_ms=100,
                payload_inline={"users": []}
            ),
            Endpoint(
                method="POST",
                path="/api/users",
                status=201,
                delay_ms=200,
                payload_inline={"created": True}
            ),
            Endpoint(
                method="DELETE",
                path="/api/users/123",
                status=204,
                delay_ms=50,
                payload_inline={}
            )
        ]
        config = Config(endpoints=endpoints)
        assert len(config.endpoints) == 3
        assert config.endpoints[0].method == "GET"
        assert config.endpoints[1].method == "POST"
        assert config.endpoints[2].method == "DELETE"
