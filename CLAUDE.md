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
- ✅ Download progress tracking for both singles and albums
- ✅ Enhanced filename matching system preventing false positives
- ✅ Compact download queue UI with proper space utilization

**Active Work**:
- 🔧 Download Manager button functionality (Cancel/Open buttons)
- ⏳ Additional UI polish and user experience improvements

**Recently Resolved Issues**:

### ✅ Download Tracking System (RESOLVED)
- **FIXED**: Download progress tracking now works correctly for both singles and albums
- **FIXED**: Album tracks no longer show same name in active downloads - enhanced filename matching prevents false matches
- **FIXED**: Download ID management and API integration properly handles real UUIDs
- **FIXED**: UI state synchronization for completed downloads with proper queue transitions

**Current Issues Requiring Attention**:

### Download Manager Button Issues
**Priority**: High - Download queue buttons non-functional

**Problem Summary**: The Cancel and Open buttons in download queue interface are not working when clicked.

**Specific Issues**:
1. **Cancel Button Not Working**:
   - Location: CompactDownloadItem cancel button in active download queue
   - Problem: Button clicks not triggering download cancellation
   - Likely causes: Signal connection issues or incorrect download ID usage for API calls
   - Expected behavior: Should cancel active downloads and remove from queue

2. **Open Button Not Working**:
   - Location: CompactDownloadItem open button in finished downloads section
   - Problem: Button clicks not opening download folder location
   - Likely causes: Missing signal connections, incorrect file paths, or silent error handling
   - Expected behavior: Should open file explorer to show downloaded files

**Investigation Needed**:
- Verify button signal connections in CompactDownloadItem class
- Check if download IDs are properly passed to cancellation methods
- Validate file paths for completed downloads
- Add user feedback for button operation failures

### Future UI Improvements
**Priority**: Low - Additional enhancements for later consideration

**Potential Changes**:
1. Remove "Pause All" button (not needed)
2. Fix "Clear Completed" functionality (currently doesn't work)
3. Additional download queue management features

## Key Components Status

1. **Configuration Management**: ✅ Implemented - Secure handling of API keys and service credentials
2. **Spotify Integration**: ✅ Implemented - Playlist retrieval and metadata extraction
3. **Plex Integration**: ✅ Implemented - Media server synchronization and metadata updates
4. **Soulseek Integration**: ✅ Mostly Complete - Music discovery and download tracking working, minor button issues remain
5. **Matching Engine**: ⏳ Planned - Robust algorithms for matching tracks across services
6. **User Interface**: ✅ Mostly Complete - Spotify-inspired design with modern, animated elements