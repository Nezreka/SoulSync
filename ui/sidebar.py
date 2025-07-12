from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton, 
                           QLabel, QFrame, QSizePolicy, QSpacerItem, QSlider, QProgressBar)
from PyQt6.QtCore import Qt, pyqtSignal, QPropertyAnimation, QEasingCurve, QRect, QTimer
from PyQt6.QtGui import QFont, QPalette, QIcon, QPixmap, QPainter, QFontMetrics

class ScrollingLabel(QLabel):
    """A label that smoothly scrolls text horizontally when it's too long to fit"""
    
    def __init__(self, text="", parent=None):
        super().__init__(parent)
        self.full_text = text
        self.scroll_offset = 0
        self.text_width = 0
        self.should_scroll = False
        self.is_scrolling = False
        self.scroll_speed = 30  # pixels per second
        
        # Animation timer
        self.scroll_timer = QTimer()
        self.scroll_timer.timeout.connect(self.update_scroll)
        
        # Pause timer for smooth start/stop
        self.pause_timer = QTimer()
        self.pause_timer.setSingleShot(True)
        self.pause_timer.timeout.connect(self.start_scroll_animation)
        
        # Set initial properties
        self.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.update_text_metrics()
        
    def setText(self, text):
        """Override setText to handle scroll calculations"""
        self.full_text = text
        self.scroll_offset = 0
        self.update_text_metrics()
        super().setText(text)
        
    def update_text_metrics(self):
        """Calculate if text needs scrolling and start animation if needed"""
        if not self.full_text:
            self.should_scroll = False
            self.stop_scrolling()
            return
            
        font_metrics = QFontMetrics(self.font())
        self.text_width = font_metrics.horizontalAdvance(self.full_text)
        available_width = self.width() - 20  # Account for padding
        
        self.should_scroll = self.text_width > available_width and available_width > 0
        
        if self.should_scroll and not self.is_scrolling:
            # Start scrolling after a pause
            self.pause_timer.start(1500)  # 1.5 second pause before scrolling
        elif not self.should_scroll:
            self.stop_scrolling()
            
    def start_scroll_animation(self):
        """Start the continuous scrolling animation"""
        if self.should_scroll and not self.is_scrolling:
            self.is_scrolling = True
            self.scroll_timer.start(50)  # Update every 50ms for smooth animation
            
    def stop_scrolling(self):
        """Stop scrolling and reset position"""
        self.scroll_timer.stop()
        self.pause_timer.stop()
        self.is_scrolling = False
        self.scroll_offset = 0
        self.update()
        
    def update_scroll(self):
        """Update scroll position for animation"""
        if not self.should_scroll:
            self.stop_scrolling()
            return
            
        available_width = self.width() - 20
        max_scroll = self.text_width - available_width + 30  # Extra padding at end
        
        # Move scroll position
        self.scroll_offset += 2  # 2 pixels per frame
        
        # Reset when we've scrolled past the end
        if self.scroll_offset > max_scroll:
            self.scroll_offset = -50  # Start from off-screen left
            
        self.update()
        
    def paintEvent(self, event):
        """Custom paint event to draw scrolling text"""
        if not self.should_scroll or not self.is_scrolling:
            # Use default painting for non-scrolling text
            super().paintEvent(event)
            return
            
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        # Set font and color from stylesheet
        painter.setFont(self.font())
        
        # Get text color from current style
        painter.setPen(self.palette().color(QPalette.ColorRole.WindowText))
        
        # Draw text at scroll offset position
        text_rect = self.rect()
        text_rect.adjust(10, 0, -10, 0)  # Account for padding
        
        painter.drawText(text_rect.x() - self.scroll_offset, text_rect.y(), 
                        text_rect.width() + self.text_width, text_rect.height(),
                        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                        self.full_text)
        
    def resizeEvent(self, event):
        """Handle resize to recalculate scrolling needs"""
        super().resizeEvent(event)
        self.update_text_metrics()
        
    def enterEvent(self, event):
        """Start scrolling on hover"""
        super().enterEvent(event)
        if self.should_scroll and not self.is_scrolling:
            self.start_scroll_animation()
            
    def leaveEvent(self, event):
        """Optionally stop scrolling when mouse leaves (can be customized)"""
        super().leaveEvent(event)
        # Note: We continue scrolling even after mouse leaves for better UX
        # You can uncomment the line below if you want it to stop on mouse leave
        # self.stop_scrolling()

class SidebarButton(QPushButton):
    def __init__(self, text: str, icon_text: str = "", parent=None):
        super().__init__(parent)
        self.text = text
        self.icon_text = icon_text
        self.is_active = False
        self.setup_ui()
    
    def setup_ui(self):
        self.setFixedHeight(50)
        self.setFixedWidth(200)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        
        layout = QHBoxLayout(self)
        layout.setContentsMargins(20, 0, 20, 0)
        layout.setSpacing(15)
        
        # Icon label
        self.icon_label = QLabel(self.icon_text)
        self.icon_label.setFixedSize(24, 24)
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon_label.setStyleSheet("""
            QLabel {
                color: #b3b3b3;
                font-size: 16px;
                font-weight: bold;
                border-radius: 12px;
                background: rgba(255, 255, 255, 0.1);
            }
        """)
        
        # Text label
        self.text_label = QLabel(self.text)
        self.text_label.setFont(QFont("Arial", 11, QFont.Weight.Medium))
        
        layout.addWidget(self.icon_label)
        layout.addWidget(self.text_label)
        layout.addStretch()
        
        self.update_style()
    
    def set_active(self, active: bool):
        self.is_active = active
        self.update_style()
    
    def update_style(self):
        if self.is_active:
            self.setStyleSheet("""
                SidebarButton {
                    background: rgba(29, 185, 84, 0.15);
                    border-left: 4px solid #1db954;
                    border-radius: 12px;
                    text-align: left;
                    padding: 0px;
                }
                SidebarButton:hover {
                    background: rgba(29, 185, 84, 0.25);
                    transform: scale(1.02);
                }
            """)
            self.text_label.setStyleSheet("color: #1db954; font-weight: bold; background: transparent;")
            self.icon_label.setStyleSheet("""
                QLabel {
                    color: #1db954;
                    font-size: 16px;
                    font-weight: bold;
                    border-radius: 14px;
                    background: rgba(29, 185, 84, 0.25);
                }
            """)
        else:
            self.setStyleSheet("""
                SidebarButton {
                    background: transparent;
                    border: none;
                    border-radius: 12px;
                    text-align: left;
                    padding: 0px;
                }
                SidebarButton:hover {
                    background: rgba(255, 255, 255, 0.08);
                    border-left: 2px solid rgba(255, 255, 255, 0.3);
                }
            """)
            self.text_label.setStyleSheet("color: #b3b3b3; background: transparent;")
            self.icon_label.setStyleSheet("""
                QLabel {
                    color: #b3b3b3;
                    font-size: 16px;
                    font-weight: bold;
                    border-radius: 14px;
                    background: rgba(255, 255, 255, 0.08);
                }
            """)

class StatusIndicator(QWidget):
    def __init__(self, service_name: str, parent=None):
        super().__init__(parent)
        self.service_name = service_name
        self.is_connected = False
        self.setup_ui()
    
    def setup_ui(self):
        self.setFixedHeight(35)  # Ensure enough height
        layout = QHBoxLayout(self)
        layout.setContentsMargins(15, 8, 15, 8)
        layout.setSpacing(12)
        
        # Status dot with rounded background
        self.status_dot = QLabel("●")
        self.status_dot.setFixedSize(16, 16)
        self.status_dot.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_dot.setStyleSheet("""
            QLabel {
                border-radius: 8px;
                font-size: 12px;
                font-weight: bold;
            }
        """)
        
        # Service name
        self.service_label = QLabel(self.service_name)
        self.service_label.setFont(QFont("Arial", 10, QFont.Weight.Medium))
        self.service_label.setMinimumWidth(80)  # Ensure text doesn't get cut off
        
        layout.addWidget(self.status_dot)
        layout.addWidget(self.service_label)
        layout.addStretch()
        
        self.update_status(False)
    
    def update_status(self, connected: bool):
        self.is_connected = connected
        if connected:
            self.status_dot.setStyleSheet("""
                QLabel {
                    color: #1db954;
                    background: rgba(29, 185, 84, 0.15);
                    border-radius: 8px;
                    font-size: 12px;
                    font-weight: bold;
                }
            """)
            self.service_label.setStyleSheet("color: #ffffff; font-weight: 500;")
        else:
            self.status_dot.setStyleSheet("""
                QLabel {
                    color: #e22134;
                    background: rgba(226, 33, 52, 0.15);
                    border-radius: 8px;
                    font-size: 12px;
                    font-weight: bold;
                }
            """)
            self.service_label.setStyleSheet("color: #b3b3b3; font-weight: 400;")

class MediaPlayer(QWidget):
    # Signals for media control
    play_pause_requested = pyqtSignal()
    stop_requested = pyqtSignal()
    volume_changed = pyqtSignal(float)  # Volume as percentage (0.0 to 1.0)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.is_playing = False
        self.is_expanded = False
        self.current_track = None
        self.setup_ui()
    
    def setup_ui(self):
        self.setFixedHeight(65)  # Start collapsed with more breathing room
        self.setStyleSheet("""
            MediaPlayer {
                background: qlineargradient(x1: 0, y1: 0, x2: 0, y2: 1,
                                          stop: 0 #1e1e1e,
                                          stop: 1 #141414);
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 12px;
                margin: 0 6px;
            }
            MediaPlayer:hover {
                border: 1px solid rgba(255, 255, 255, 0.12);
                background: qlineargradient(x1: 0, y1: 0, x2: 0, y2: 1,
                                          stop: 0 #212121,
                                          stop: 1 #171717);
            }
        """)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 10, 16, 10)
        layout.setSpacing(10)
        
        # Always visible header with basic controls
        self.header = self.create_header()
        layout.addWidget(self.header)
        
        # Expandable content (hidden when collapsed)
        self.expanded_content = self.create_expanded_content()
        self.expanded_content.setVisible(False)
        layout.addWidget(self.expanded_content)
        
        # No track message (shown when no music)
        self.no_track_label = QLabel("No track playing")
        self.no_track_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.no_track_label.setStyleSheet("""
            QLabel {
                color: #6a6a6a;
                font-size: 11px;
                font-weight: 400;
                padding: 12px;
                background: transparent;
                letter-spacing: 0.5px;
            }
        """)
        layout.addWidget(self.no_track_label)
    
    def create_header(self):
        header = QWidget()
        layout = QHBoxLayout(header)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        
        # Track info (expandable on click) - now with scrolling for long titles
        self.track_info = ScrollingLabel("No track")
        self.track_info.setStyleSheet("""
            ScrollingLabel {
                color: #ffffff;
                font-size: 13px;
                font-weight: 600;
                background: transparent;
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                letter-spacing: 0.2px;
                padding: 2px 0px;
            }
            ScrollingLabel:hover {
                color: #1ed760;
            }
        """)
        self.track_info.setCursor(Qt.CursorShape.PointingHandCursor)
        self.track_info.mousePressEvent = self.toggle_expansion
        
        # Play/pause button
        self.play_pause_btn = QPushButton("▶")
        self.play_pause_btn.setFixedSize(36, 36)
        self.play_pause_btn.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1: 0, y1: 0, x2: 0, y2: 1,
                                          stop: 0 #1ed760,
                                          stop: 1 #1db954);
                border: none;
                border-radius: 18px;
                color: #000000;
                font-size: 14px;
                font-weight: 900;
                font-family: Arial, sans-serif;
            }
            QPushButton:hover {
                background: qlineargradient(x1: 0, y1: 0, x2: 0, y2: 1,
                                          stop: 0 #1fdf64,
                                          stop: 1 #1ed760);
                transform: scale(1.06);
            }
            QPushButton:pressed {
                background: qlineargradient(x1: 0, y1: 0, x2: 0, y2: 1,
                                          stop: 0 #1ca851,
                                          stop: 1 #169c46);
                transform: scale(0.98);
            }
            QPushButton:disabled {
                background: qlineargradient(x1: 0, y1: 0, x2: 0, y2: 1,
                                          stop: 0 #404040,
                                          stop: 1 #2a2a2a);
                color: #666666;
            }
        """)
        self.play_pause_btn.clicked.connect(self.on_play_pause_clicked)
        self.play_pause_btn.setEnabled(False)
        
        layout.addWidget(self.track_info)
        layout.addStretch()
        layout.addWidget(self.play_pause_btn)
        
        return header
    
    def create_expanded_content(self):
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 5, 0, 0)
        layout.setSpacing(8)
        
        # Artist and album info
        self.artist_album_label = QLabel("Unknown Artist • Unknown Album")
        self.artist_album_label.setStyleSheet("""
            QLabel {
                color: #a7a7a7;
                font-size: 11px;
                font-weight: 400;
                background: transparent;
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                letter-spacing: 0.3px;
                margin-top: 2px;
            }
        """)
        layout.addWidget(self.artist_album_label)
        
        # Control buttons
        controls_layout = QHBoxLayout()
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(10)
        
        # Stop button
        self.stop_btn = QPushButton("⏹")
        self.stop_btn.setFixedSize(28, 28)
        self.stop_btn.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1: 0, y1: 0, x2: 0, y2: 1,
                                          stop: 0 rgba(226, 33, 52, 0.15),
                                          stop: 1 rgba(180, 28, 42, 0.15));
                border: 1px solid rgba(226, 33, 52, 0.3);
                border-radius: 14px;
                color: #e22134;
                font-size: 11px;
                font-weight: 700;
                font-family: Arial, sans-serif;
            }
            QPushButton:hover {
                background: qlineargradient(x1: 0, y1: 0, x2: 0, y2: 1,
                                          stop: 0 rgba(226, 33, 52, 0.25),
                                          stop: 1 rgba(180, 28, 42, 0.25));
                border: 1px solid rgba(226, 33, 52, 0.5);
                transform: scale(1.05);
            }
            QPushButton:pressed {
                background: qlineargradient(x1: 0, y1: 0, x2: 0, y2: 1,
                                          stop: 0 rgba(180, 28, 42, 0.3),
                                          stop: 1 rgba(150, 23, 35, 0.3));
                transform: scale(0.95);
            }
            QPushButton:disabled {
                background: rgba(100, 100, 100, 0.1);
                border: 1px solid rgba(100, 100, 100, 0.2);
                color: #666666;
            }
        """)
        self.stop_btn.clicked.connect(self.on_stop_clicked)
        self.stop_btn.setEnabled(False)
        
        # Volume control
        volume_layout = QHBoxLayout()
        volume_layout.setSpacing(8)
        
        volume_icon = QLabel("♪")
        volume_icon.setStyleSheet("""
            QLabel {
                color: #a7a7a7;
                font-size: 12px;
                font-weight: 600;
                font-family: Arial, sans-serif;
                padding: 0px 2px;
            }
        """)
        
        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(70)  # Default 70% volume
        self.volume_slider.setFixedWidth(65)
        self.volume_slider.setStyleSheet("""
            QSlider::groove:horizontal {
                border: none;
                height: 4px;
                background: qlineargradient(x1: 0, y1: 0, x2: 1, y2: 0,
                                          stop: 0 #383838,
                                          stop: 1 #2a2a2a);
                border-radius: 2px;
            }
            QSlider::handle:horizontal {
                background: qlineargradient(x1: 0, y1: 0, x2: 0, y2: 1,
                                          stop: 0 #ffffff,
                                          stop: 1 #e0e0e0);
                border: 1px solid rgba(255, 255, 255, 0.3);
                width: 12px;
                height: 12px;
                border-radius: 6px;
                margin: -4px 0;
            }
            QSlider::handle:horizontal:hover {
                background: qlineargradient(x1: 0, y1: 0, x2: 0, y2: 1,
                                          stop: 0 #1ed760,
                                          stop: 1 #1db954);
                border: 1px solid rgba(30, 215, 96, 0.5);
                transform: scale(1.1);
            }
            QSlider::sub-page:horizontal {
                background: qlineargradient(x1: 0, y1: 0, x2: 1, y2: 0,
                                          stop: 0 #1ed760,
                                          stop: 1 #1db954);
                border-radius: 2px;
            }
        """)
        self.volume_slider.valueChanged.connect(self.on_volume_changed)
        
        volume_layout.addWidget(volume_icon)
        volume_layout.addWidget(self.volume_slider)
        
        controls_layout.addWidget(self.stop_btn)
        controls_layout.addStretch()
        controls_layout.addLayout(volume_layout)
        
        layout.addLayout(controls_layout)
        
        return content
    
    def toggle_expansion(self, event=None):
        """Toggle between collapsed and expanded view"""
        if not self.current_track:
            return
            
        self.is_expanded = not self.is_expanded
        
        if self.is_expanded:
            self.setFixedHeight(125)  # Slightly taller for better spacing
            self.expanded_content.setVisible(True)
            self.no_track_label.setVisible(False)
        else:
            self.setFixedHeight(65)  # Match the updated collapsed height
            self.expanded_content.setVisible(False)
    
    def set_track_info(self, track_result):
        """Update the media player with new track information"""
        self.current_track = track_result
        
        # Update track name
        track_name = getattr(track_result, 'title', None) or getattr(track_result, 'filename', 'Unknown Track')
        if hasattr(track_result, 'filename'):
            # Clean up filename for display
            import os
            track_name = os.path.splitext(os.path.basename(track_result.filename))[0]
        
        self.track_info.setText(track_name)
        
        # Update artist and album
        artist = getattr(track_result, 'artist', 'Unknown Artist')
        album = getattr(track_result, 'album', 'Unknown Album')
        self.artist_album_label.setText(f"{artist} • {album}")
        
        # Enable controls
        self.play_pause_btn.setEnabled(True)
        self.stop_btn.setEnabled(True)
        
        # Set to playing state (show pause button since track just started)
        self.set_playing_state(True)
        
        # Hide no track message and show player
        self.no_track_label.setVisible(False)
        
        # Auto-expand when new track starts
        if not self.is_expanded:
            self.toggle_expansion()
    
    def set_playing_state(self, playing):
        """Update play/pause button state"""
        self.is_playing = playing
        if playing:
            self.play_pause_btn.setText("⏸")
            # Start scrolling animation when playing
            if self.track_info.should_scroll and not self.track_info.is_scrolling:
                self.track_info.start_scroll_animation()
        else:
            self.play_pause_btn.setText("▶")
            # Optionally stop scrolling when paused (can be customized)
            # self.track_info.stop_scrolling()
    
    def clear_track(self):
        """Clear current track and reset to no track state"""
        self.current_track = None
        self.is_playing = False
        
        # Stop any scrolling animation
        self.track_info.stop_scrolling()
        
        # Update UI
        self.track_info.setText("No track")
        self.artist_album_label.setText("Unknown Artist • Unknown Album")
        self.play_pause_btn.setText("▶")
        self.play_pause_btn.setEnabled(False)
        self.stop_btn.setEnabled(False)
        
        # Show no track message
        self.no_track_label.setVisible(True)
        
        # Collapse view
        if self.is_expanded:
            self.toggle_expansion()
    
    def on_play_pause_clicked(self):
        """Handle play/pause button click"""
        self.play_pause_requested.emit()
    
    def on_stop_clicked(self):
        """Handle stop button click"""
        self.stop_requested.emit()
    
    def on_volume_changed(self, value):
        """Handle volume slider change"""
        volume = value / 100.0  # Convert to 0.0-1.0
        self.volume_changed.emit(volume)
    

class ModernSidebar(QWidget):
    page_changed = pyqtSignal(str)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_page = "dashboard"
        self.buttons = {}
        self.setup_ui()
    
    def setup_ui(self):
        self.setFixedWidth(220)
        self.setStyleSheet("""
            ModernSidebar {
                background: #121212;
                border-right: 1px solid #282828;
            }
        """)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        # Header
        header = self.create_header()
        layout.addWidget(header)
        
        # Navigation buttons
        nav_section = self.create_navigation()
        layout.addWidget(nav_section)
        
        # Spacer
        layout.addItem(QSpacerItem(20, 40, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding))
        
        # Media Player section
        self.media_player = MediaPlayer()
        layout.addWidget(self.media_player)
        
        # Small spacer between media player and status
        layout.addItem(QSpacerItem(20, 10, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed))
        
        # Status section
        status_section = self.create_status_section()
        layout.addWidget(status_section)
    
    def create_header(self):
        header = QWidget()
        header.setFixedHeight(85)
        header.setStyleSheet("""
            QWidget {
                background: #121212; 
                border-bottom: 1px solid #282828;
                border-bottom-left-radius: 8px;
                border-bottom-right-radius: 8px;
            }
        """)
        
        layout = QVBoxLayout(header)
        layout.setContentsMargins(20, 22, 20, 22)
        layout.setSpacing(3)
        
        # App name
        app_name = QLabel("NewMusic")
        app_name.setFont(QFont("Arial", 19, QFont.Weight.Bold))
        app_name.setStyleSheet("color: #ffffff; letter-spacing: -0.5px;")
        
        # Subtitle
        subtitle = QLabel("Music Sync & Manager")
        subtitle.setFont(QFont("Arial", 10))
        subtitle.setStyleSheet("color: #b3b3b3; opacity: 0.8;")
        
        layout.addWidget(app_name)
        layout.addWidget(subtitle)
        
        return header
    
    def create_navigation(self):
        nav_widget = QWidget()
        nav_widget.setStyleSheet("""
            QWidget {
                background: #121212;
                border-radius: 8px;
            }
        """)
        layout = QVBoxLayout(nav_widget)
        layout.setContentsMargins(8, 20, 8, 20)
        layout.setSpacing(6)
        
        # Navigation buttons
        nav_items = [
            ("dashboard", "Dashboard", "📊"),
            ("sync", "Playlist Sync", "🔄"),
            ("downloads", "Downloads", "📥"),
            ("artists", "Artists", "🎵"),
            ("settings", "Settings", "⚙️")
        ]
        
        for page_id, title, icon in nav_items:
            button = SidebarButton(title, icon)
            button.clicked.connect(lambda checked, pid=page_id: self.change_page(pid))
            self.buttons[page_id] = button
            layout.addWidget(button)
        
        # Set dashboard as active by default
        self.buttons["dashboard"].set_active(True)
        
        return nav_widget
    
    def create_status_section(self):
        status_widget = QWidget()
        status_widget.setFixedHeight(140)  # Increased height
        status_widget.setStyleSheet("""
            QWidget {
                background: #181818; 
                border-top: 1px solid #282828;
                border-top-left-radius: 8px;
                border-top-right-radius: 8px;
            }
        """)
        
        layout = QVBoxLayout(status_widget)
        layout.setContentsMargins(0, 18, 0, 18)  # Better margins
        layout.setSpacing(6)  # Tighter spacing between items
        
        # Status title
        status_title = QLabel("Connection Status")
        status_title.setFont(QFont("Arial", 11, QFont.Weight.Bold))
        status_title.setStyleSheet("color: #ffffff; padding: 0 15px; margin-bottom: 5px;")
        layout.addWidget(status_title)
        
        # Status indicators
        self.spotify_status = StatusIndicator("Spotify")
        self.plex_status = StatusIndicator("Plex")
        self.soulseek_status = StatusIndicator("Soulseek")
        
        layout.addWidget(self.spotify_status)
        layout.addWidget(self.plex_status)
        layout.addWidget(self.soulseek_status)
        
        return status_widget
    
    def change_page(self, page_id: str):
        if page_id != self.current_page:
            # Update button states
            for btn_id, button in self.buttons.items():
                button.set_active(btn_id == page_id)
            
            self.current_page = page_id
            self.page_changed.emit(page_id)
    
    def update_service_status(self, service: str, connected: bool):
        status_map = {
            "spotify": self.spotify_status,
            "plex": self.plex_status,
            "soulseek": self.soulseek_status
        }
        
        if service in status_map:
            status_map[service].update_status(connected)