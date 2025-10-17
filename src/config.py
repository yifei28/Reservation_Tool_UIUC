"""Configuration management for facility reserver."""

import json
import os
from pathlib import Path
from typing import Dict, Any
from dotenv import load_dotenv


def load_config(config_path: str = 'config.json') -> Dict[str, Any]:
    """
    Load configuration from JSON file and environment variables.

    Args:
        config_path: Path to the configuration JSON file

    Returns:
        Dictionary containing configuration settings

    Raises:
        FileNotFoundError: If config file doesn't exist
        json.JSONDecodeError: If config file is invalid JSON
    """
    # Load environment variables from .env file
    load_dotenv()

    config = {}

    # Load from JSON file if it exists
    config_file = Path(config_path)
    if config_file.exists():
        with open(config_file, 'r') as f:
            config = json.load(f)

    # Override with environment variables
    config['netid'] = os.getenv('UIUC_NETID', config.get('netid'))
    config['password'] = os.getenv('UIUC_PASSWORD', config.get('password'))
    config['email'] = os.getenv('EMAIL', config.get('email'))
    config['smtp_server'] = os.getenv('SMTP_SERVER', config.get('smtp_server', 'smtp.gmail.com'))
    config['smtp_port'] = int(os.getenv('SMTP_PORT', config.get('smtp_port', 587)))
    config['smtp_user'] = os.getenv('SMTP_USER', config.get('smtp_user'))
    config['smtp_password'] = os.getenv('SMTP_PASSWORD', config.get('smtp_password'))

    # Validate required fields
    if not config.get('netid') or not config.get('password'):
        raise ValueError("UIUC NetID and password are required (set in .env or config.json)")

    return config


def create_example_config(output_path: str = 'config.json.example'):
    """Create an example configuration file."""
    example_config = {
        "sport": "badminton",
        "preferredTimes": ["09:00", "10:00", "11:00"],
        "courtPreferences": ["Court 1", "Court 2"],
        "notificationEmail": "your-email@example.com",
        "retryAttempts": 3
    }

    with open(output_path, 'w') as f:
        json.dump(example_config, f, indent=2)

    print(f"Example configuration created at {output_path}")


def create_example_env(output_path: str = '.env.example'):
    """Create an example .env file."""
    example_env = """# UIUC Credentials
UIUC_NETID=your_netid
UIUC_PASSWORD=your_password

# Email Notifications (optional)
EMAIL=your-email@example.com
SMTP_SERVER=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your-email@gmail.com
SMTP_PASSWORD=your_app_specific_password
"""

    with open(output_path, 'w') as f:
        f.write(example_env)

    print(f"Example .env file created at {output_path}")


if __name__ == '__main__':
    # Create example files when run directly
    create_example_config()
    create_example_env()
