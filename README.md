# mcp-tool-layer
Enhances LLM agents with a Model Context Protocol layer for contextual reasoning and structured task execution. Includes agents that interact with semantic technologies (e.g. RDF, OWL, SPARQL), enabling hybrid symbolic-neural workflows.

## Overview 

## Setup

1. It is recommended to use `Python 3.11`. 
2. `Docker` is required. 
3. Populate `configs/mcp_configs.json.example`, where you should replace the path placeholders to actual paths on your local machine. 
Make sure you remove `.example` from the file name to activate the settings.
4. Populate `.env.example` with your LLM `BASE_URL` and `API_KEY`. At least one pair of `BASE_URL` and `API_KEY` is required, local or remote.
5. Install the dependencies with `pip install -e .` (this installs the project in editable mode using the `pyproject.toml` configuration).
6. Spin up `docker` containers with `docker compose up -d` inside the `docker` folder.

## Usage 

## Project structure 


