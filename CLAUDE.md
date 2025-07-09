# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a new music management application project that aims to create a Spotify-like desktop application with Python. The project is in its initial planning phase with only a project requirements document (`project.txt`) currently present.

## Project Requirements

The application will be a music management tool that:
- Connects to Spotify API and Plex Media Server
- Features an elegant, animated, vibrant theme similar to Spotify's desktop/web app
- Synchronizes Spotify playlists to Plex using robust matching systems
- Integrates with Soulseek for downloading FLAC/high-quality audio files
- Updates music metadata on Plex based on Spotify metadata including album art
- Provides core functionality that feels and looks like Spotify

## Configuration

The application will use a central `config.json` file to store:
- Spotify API credentials and login information
- Plex Media Server connection details
- Other connected service configurations

## Development Status

This is a greenfield project with no existing codebase. When implementing:
- Create a Python-based application with GUI framework (likely PyQt, Tkinter, or web-based with Flask/FastAPI)
- Implement modular architecture separating concerns for different services (Spotify, Plex, Soulseek)
- Focus on robust matching algorithms for music synchronization
- Prioritize user experience with Spotify-like interface design
- Ensure secure handling of API credentials and authentication tokens

## Key Components to Implement

1. **Configuration Management**: Secure handling of API keys and service credentials
2. **Spotify Integration**: Playlist retrieval and metadata extraction
3. **Plex Integration**: Media server synchronization and metadata updates
4. **Soulseek Integration**: Music discovery and download functionality
5. **Matching Engine**: Robust algorithms for matching tracks across services
6. **User Interface**: Spotify-inspired design with modern, animated elements