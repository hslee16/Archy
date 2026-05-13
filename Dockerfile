# Container image for archy.
#
# Primary use is satisfying registry checks (Glama, etc.) that need to
# verify the MCP server boots and responds to the MCP initialize
# handshake over stdio. Also useful for CI runs that don't want to
# install Python locally:
#
#   docker run --rm -v "$PWD":/code archy score /code
#   docker run --rm -v "$PWD":/code archy check /code
#
# Default command is `archy mcp` (stdio MCP server). Override with any
# subcommand by passing it after the image name.
#
# Build: docker build -t archy .
# Release flow: bump ARCHY_VERSION when archy ships a new tag.

FROM python:3.11-slim

ARG ARCHY_VERSION=0.15.0

RUN pip install --no-cache-dir "archy==${ARCHY_VERSION}"

WORKDIR /code

ENTRYPOINT ["archy"]
CMD ["mcp"]
