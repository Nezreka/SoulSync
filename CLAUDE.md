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
- ✅ Complete download queue management system with functional buttons
- ✅ Compact, practical download item UI design
- ✅ Album track button state management and cancellation handling
- ✅ Clear completed downloads functionality with backend integration

**Recently Completed Work**:

### ✅ Download Manager Complete Redesign (COMPLETED)
- **COMPLETED**: Fully functional Cancel and Open buttons in download queue
- **COMPLETED**: Proper slskd API integration with correct endpoint formats
- **COMPLETED**: Enhanced album track button state management with cancellation support
- **COMPLETED**: Clear All Completed Downloads functionality using slskd backend API
- **COMPLETED**: Compact download item UI redesign for optimal space utilization

### ✅ Download Queue Button Functionality (RESOLVED)
- **FIXED**: Cancel button now properly cancels downloads using correct slskd API format
- **FIXED**: Open button successfully opens download folders with fallback logic
- **FIXED**: Enhanced debugging and error handling for button operations
- **FIXED**: Album track buttons properly reset after individual track cancellation

### ✅ UI/UX Improvements (COMPLETED)
- **COMPLETED**: Redesigned CompactDownloadItem with efficient 45px height
- **COMPLETED**: Conditional layout system (active: filename/uploader/progress/cancel, finished: filename/uploader/open)
- **COMPLETED**: Smart text ellipsis handling preventing horizontal overflow
- **COMPLETED**: Optimized space allocation fitting perfectly in download queue container
- **COMPLETED**: Clean, functional button design with immediate accessibility

**Active Work**:
- ⏳ Additional UI polish and user experience improvements
- ⏳ Matching engine development for cross-service track matching

**Current System Status**: All major download management functionality is working correctly.

## Key Components Status

1. **Configuration Management**: ✅ Complete - Secure handling of API keys and service credentials
2. **Spotify Integration**: ✅ Complete - Playlist retrieval and metadata extraction
3. **Plex Integration**: ✅ Complete - Media server synchronization and metadata updates
4. **Soulseek Integration**: ✅ Complete - Full music discovery, download management, and queue functionality
5. **Download Management**: ✅ Complete - Comprehensive download queue with cancel/open functionality
6. **User Interface**: ✅ Complete - Spotify-inspired design with practical, efficient download management
7. **Matching Engine**: ⏳ In Development - Robust algorithms for matching tracks across services

## Technical Architecture

### Download Management System
- **CompactDownloadItem**: Efficient 45px height design with conditional layouts
- **slskd API Integration**: Proper DELETE endpoints with username/download_id parameters
- **Album Track Management**: State synchronization between album buttons and download queue
- **Clear Completed**: Backend integration for removing finished downloads from slskd
- **Progress Tracking**: Real-time updates with percentage display and status monitoring

### UI Design Philosophy
- **Function over Form**: Practical, user-focused design prioritizing usability
- **Container Responsive**: Optimized for small download queue areas without horizontal scrolling
- **Efficient Space Usage**: Conditional layouts maximize available space
- **Immediate Accessibility**: Buttons positioned for quick access without scrolling