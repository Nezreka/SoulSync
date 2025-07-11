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

**Current State**: Active development of a PyQt6-based desktop application with functional UI and core integrations.

**Completed Features**:
- ✅ PyQt6 GUI framework with Spotify-inspired dark theme
- ✅ Modular architecture with separate service clients (Spotify, Plex, Soulseek)
- ✅ Modern sidebar navigation with animated buttons and status indicators
- ✅ Media player sidebar with scrolling text animation for long titles
- ✅ Search functionality with real-time filtering (Albums vs Singles)
- ✅ Audio streaming and playback from Soulseek search results
- ✅ Service status monitoring and connection indicators
- ✅ Configuration management system

**Active Work**:
- 🔧 Download Manager functionality and UI improvements
- 🔧 Download tracking system (requires fixes - see Known Issues)

**Known Issues Requiring Attention**:

### Download Tracking System Issues
**Priority**: High - Download tracking is partially broken

**Problem Summary**: The download tracking system has ID management issues causing cancellation failures and incomplete UI updates.

**Specific Issues**:
1. **Download Cancellation Fails (405 Errors)**:
   - Location: `core/soulseek_client.py:809-819` (cancel_download method)
   - Problem: Using constructed filename-based IDs instead of actual API download IDs
   - API returns proper UUIDs like `"2f7e8184-e644-4439-b02d-e48f9c8d24ca"`
   - Code tries to cancel with malformed IDs like `"systemdip_Music\Kendrick Lamar\DAMN. (2017)\01 - BLOOD.flac_1752258522"`

2. **Download ID Mismatch**:
   - Location: `core/soulseek_client.py:639-732` (download method)
   - Problem: Not properly storing/returning actual download IDs from API responses
   - Need to capture and use the real download ID returned by slskd API

3. **Progress Tracking UI Updates**:
   - Location: `ui/pages/downloads.py:3828-3865` (progress tracking)
   - Problem: UI doesn't properly reflect download status changes
   - Missing handling for completed downloads without `percentComplete` field

**What's Working**: 
- slskd API is responding correctly (status 200)
- Downloads initiate successfully
- Transfer status endpoint returns proper data
- Download completion detection works

**What Needs Fixing**:
- Download ID storage and management
- Cancel download endpoint URL construction
- UI state synchronization for completed downloads
- Error handling for missing API fields

### Download Manager UI Improvements
**Priority**: Medium - User experience enhancements requested

**Requested Changes**:
1. Remove "Pause All" button (not needed)
2. Fix "Clear Completed" functionality (currently doesn't work)
3. Replace "Details" button with "Reveal/Open" button that opens the folder containing downloaded files

## Key Components Status

1. **Configuration Management**: ✅ Implemented - Secure handling of API keys and service credentials
2. **Spotify Integration**: ✅ Implemented - Playlist retrieval and metadata extraction
3. **Plex Integration**: ✅ Implemented - Media server synchronization and metadata updates
4. **Soulseek Integration**: 🔧 Partially Complete - Music discovery works, download tracking needs fixes
5. **Matching Engine**: ⏳ Planned - Robust algorithms for matching tracks across services
6. **User Interface**: ✅ Mostly Complete - Spotify-inspired design with modern, animated elements