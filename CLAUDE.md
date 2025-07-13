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
- 🎯 **PRIORITY FEATURE**: Spotify Matched Download System - Advanced music organization with intelligent matching
- ⏳ Additional UI polish and user experience improvements  
- ⏳ Enhanced matching engine development for cross-service track matching

**Current System Status**: All major download management functionality is working correctly. Ready for advanced Spotify integration features.

---

# 🎯 MAJOR FEATURE: Spotify Matched Download & Organization System

## 🎯 Feature Overview

**Ultimate Goal**: Transform downloaded music files into a perfectly organized library with Spotify-accurate metadata and professional folder structure.

**Core Concept**: Add "Matched Download" buttons (🎯) alongside regular download buttons that automatically:
1. Download the track using existing Soulseek integration
2. Intelligently match the track with Spotify's database for accurate metadata
3. Transfer and organize the file into a professional folder structure
4. Handle complex edge cases like remixes, compilations, and mixed-artist albums

**Target Folder Structure**:
```
Transfer/
├── Taylor Swift/
│   ├── Taylor Swift - 1989 (Taylor's Version)/
│   │   ├── Taylor Swift - Shake It Off (Taylor's Version).flac
│   │   ├── Taylor Swift - Blank Space (Taylor's Version).flac
│   │   └── ...
│   └── Taylor Swift - Folklore/
│       ├── Taylor Swift - Cardigan.flac
│       └── ...
├── Daft Punk/
│   └── Daft Punk - Random Access Memories/
│       ├── Daft Punk - Get Lucky (feat. Pharrell Williams).flac
│       └── ...
└── Various Artists/
    └── Various Artists - Now That's What I Call Music 50/
        ├── Britney Spears - Toxic.flac
        └── ...
```

## 🚀 Implementation Phases

### Phase 1: Foundation & Architecture ⏳
**Status**: In Planning
**Deliverables**:
- ✅ Comprehensive specification document (SPOTIFY_MATCHING_SPEC.md)
- ⏳ Advanced metadata extraction system leveraging existing TrackResult/AlbumResult data
- ⏳ Sophisticated matching algorithms with multi-stage fallback logic
- ⏳ Professional UI architecture with responsive modal design

### Phase 2: Core Services Development 📋
**Status**: Pending Phase 1
**Deliverables**:
- SpotifyMatchingService with intelligent search query generation
- FileOrganizationService with atomic file operations and conflict resolution
- MatchingEngine with confidence scoring and remix detection
- Enhanced error handling and logging systems

### Phase 3: Professional UI Implementation 🎨
**Status**: Pending Phase 2  
**Deliverables**:
- Responsive MatchingModal with proper spacing and layouts
- Real-time search interface with debouncing and progress indicators
- Confidence visualization and user feedback systems
- Accessibility features and keyboard navigation

### Phase 4: Integration & Polish ⚡
**Status**: Pending Phase 3
**Deliverables**:
- 🎯 Matched download buttons integrated into existing UI components
- Download completion detection and automatic processing
- Album-level matching and batch processing capabilities
- Comprehensive testing and edge case handling

## 🎵 Supported Download Types

### 1. **Singles** (Primary Focus)
- Individual tracks from search results
- Most straightforward matching scenario
- Foundation for more complex matching logic

### 2. **Albums** (Future Enhancement)
- Complete album downloads with track-by-track matching
- Handle mixed-artist compilations intelligently
- Detect and separate "fake albums" (user playlists disguised as albums)

### 3. **Individual Album Tracks** (Future Enhancement)
- Tracks downloaded individually from within album results
- Inherit album context for better matching accuracy
- Maintain consistency with full album downloads

## 🧠 Intelligent Matching System

### Advanced Metadata Extraction
**Challenge**: Soulseek filenames are inconsistent and unreliable
**Solution**: Multi-source metadata aggregation
- **Primary**: Leverage existing `TrackResult.artist`, `TrackResult.title`, `TrackResult.album` fields
- **Secondary**: Enhanced filename parsing with regex patterns
- **Tertiary**: Directory path analysis for album context
- **Fallback**: Manual user input through search interface

### Sophisticated Search Strategies
1. **Exact Match**: Artist + Title + Album (highest confidence)
2. **Partial Match**: Artist + Title (good confidence) 
3. **Fuzzy Match**: Normalized strings with similarity scoring
4. **Remix Detection**: Extract remix artist from title patterns
5. **Manual Search**: User-driven fallback with suggestions

### Confidence Scoring System
- **90-100%**: Exact metadata match, auto-proceed
- **75-89%**: High confidence, show for user confirmation
- **60-74%**: Medium confidence, require user review
- **Below 60%**: Low confidence, manual search required

## 🎛️ User Experience Flow

### Seamless Workflow
1. **User clicks 🎯** on any track (single or within album)
2. **Download starts immediately** using existing proven download system
3. **Matching modal appears** with elegant, responsive design
4. **Automatic matching runs** in background with progress indication
5. **Results displayed** with confidence scores and preview information
6. **User confirms or refines** the match through intuitive interface
7. **File transferred atomically** to organized structure
8. **Success feedback** with option to open destination folder

### Error Handling & Fallbacks
- **No automatic match**: Manual search interface with intelligent suggestions
- **Multiple high-confidence matches**: User selection with detailed comparison
- **No suitable matches found**: Option to proceed with regular download
- **File transfer errors**: Rollback mechanisms and detailed error reporting
- **Spotify API failures**: Graceful degradation with retry logic

## 🔧 Technical Challenges & Solutions

### Challenge 1: Inconsistent Soulseek Metadata
**Problem**: Filenames like "Track 01.mp3" or "asdjkfh - some song.flac"
**Solution**: Multi-stage extraction using existing TrackResult fields + enhanced parsing

### Challenge 2: Remix Track Attribution  
**Problem**: "Song Title (Artist Remix)" should match to remix artist, not original
**Solution**: Regex-based remix detection with artist extraction patterns

### Challenge 3: Album vs Playlist Distinction
**Problem**: User playlists disguised as "albums" with mixed artists
**Solution**: Artist consistency analysis and intelligent categorization

### Challenge 4: File Organization Conflicts
**Problem**: Duplicate files, naming conflicts, atomic operations
**Solution**: Professional file management with backup, rollback, and deduplication

### Challenge 5: Spotify API Rate Limits
**Problem**: Search throttling and request failures
**Solution**: Intelligent caching, request batching, and exponential backoff

## ⚙️ Configuration & Settings

### User Preferences
- **Auto-match threshold**: Minimum confidence for automatic processing
- **Folder naming patterns**: Customizable organization schemes  
- **Transfer location**: Default destination directory
- **Conflict resolution**: Overwrite, rename, or skip duplicate files
- **Remix handling**: Original artist vs remix artist preference

### Advanced Options
- **Search aggressiveness**: Number of search strategies to attempt
- **Metadata sources**: Priority order for information extraction
- **Quality preferences**: File format and bitrate handling
- **Cover art download**: Album artwork integration

## 🎯 Success Criteria

### Functional Requirements
- [ ] 95%+ success rate for popular tracks with clear metadata
- [ ] Graceful fallback handling for edge cases
- [ ] Sub-3-second matching time for typical searches
- [ ] Professional folder organization matching industry standards
- [ ] Zero data loss during file operations

### User Experience Requirements  
- [ ] Intuitive interface requiring minimal user training
- [ ] Clear progress indication and feedback
- [ ] Responsive design that adapts to different screen sizes
- [ ] Accessibility compliance for keyboard navigation
- [ ] Professional visual design matching existing application theme

### Technical Requirements
- [ ] Robust error handling with detailed logging
- [ ] Atomic file operations with rollback capability
- [ ] Efficient memory usage during batch operations
- [ ] Integration with existing download queue system
- [ ] Maintainable code architecture for future enhancements

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