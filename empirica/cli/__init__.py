"""
CLI Components Module - Modular Empirica CLI Architecture

This module provides a clean, modular structure for the Empirica CLI,
replacing the monolithic cli.py with semantically organized components.
"""

from .cli_core import create_argument_parser, main
from .cli_utils import format_uncertainty_output, handle_cli_error, print_component_status

__all__ = [
    'main',
    'create_argument_parser',
    'print_component_status',
    'format_uncertainty_output',
    'handle_cli_error'
]
