from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton, 
                           QLabel, QFrame, QSizePolicy, QSpacerItem, QSlider, QProgressBar, QApplication)
from PyQt6.QtCore import Qt, pyqtSignal, QPropertyAnimation, QEasingCurve, QRect, QTimer, pyqtProperty
from PyQt6.QtGui import QFont, QPalette, QIcon, QPixmap, QPainter, QFontMetrics, QColor, QLinearGradient

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
        self.setFixedHeight(52)
        self.setFixedWidth(216)  # Adjusted for new sidebar width
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        
        layout = QHBoxLayout(self)
        layout.setContentsMargins(18, 0, 18, 0)
        layout.setSpacing(16)
        
        # Icon label with better styling
        self.icon_label = QLabel(self.icon_text)
        self.icon_label.setFixedSize(28, 28)
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon_label.setStyleSheet("""
            QLabel {
                color: rgba(255, 255, 255, 0.7);
                font-size: 16px;
                font-weight: 600;
                border-radius: 14px;
                background: qlineargradient(x1: 0, y1: 0, x2: 1, y2: 1,
                                          stop: 0 rgba(255, 255, 255, 0.08),
                                          stop: 1 rgba(255, 255, 255, 0.04));
                border: 1px solid rgba(255, 255, 255, 0.05);
            }
        """)
        
        # Text label with improved typography
        self.text_label = QLabel(self.text)
        self.text_label.setFont(QFont("SF Pro Text", 12, QFont.Weight.Medium))
        
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
                    background: qlineargradient(x1: 0, y1: 0, x2: 1, y2: 1,
                                              stop: 0 rgba(29, 185, 84, 0.18),
                                              stop: 0.5 rgba(29, 185, 84, 0.12),
                                              stop: 1 rgba(29, 185, 84, 0.08));
                    border-left: 3px solid #1ed760;
                    border-radius: 16px;
                    text-align: left;
                    padding: 0px;
                    border: 1px solid rgba(29, 185, 84, 0.2);
                }
                SidebarButton:hover {
                    background: qlineargradient(x1: 0, y1: 0, x2: 1, y2: 1,
                                              stop: 0 rgba(29, 185, 84, 0.25),
                                              stop: 0.5 rgba(29, 185, 84, 0.18),
                                              stop: 1 rgba(29, 185, 84, 0.12));
                    border: 1px solid rgba(29, 185, 84, 0.3);
                }
            """)
            self.text_label.setStyleSheet("""
                color: #1ed760; 
                font-weight: 600; 
                background: transparent;
                letter-spacing: 0.1px;
            """)
            self.icon_label.setStyleSheet("""
                QLabel {
                    color: #1ed760;
                    font-size: 16px;
                    font-weight: 700;
                    border-radius: 14px;
                    background: qlineargradient(x1: 0, y1: 0, x2: 1, y2: 1,
                                              stop: 0 rgba(29, 185, 84, 0.25),
                                              stop: 1 rgba(30, 215, 96, 0.2));
                    border: 1px solid rgba(29, 185, 84, 0.3);
                }
            """)
        else:
            self.setStyleSheet("""
                SidebarButton {
                    background: transparent;
                    border: none;
                    border-radius: 16px;
                    text-align: left;
                    padding: 0px;
                }
                SidebarButton:hover {
                    background: qlineargradient(x1: 0, y1: 0, x2: 1, y2: 1,
                                              stop: 0 rgba(255, 255, 255, 0.06),
                                              stop: 1 rgba(255, 255, 255, 0.03));
                    border-left: 2px solid rgba(255, 255, 255, 0.2);
                    border: 1px solid rgba(255, 255, 255, 0.08);
                }
            """)
            self.text_label.setStyleSheet("""
                color: rgba(255, 255, 255, 0.8); 
                background: transparent;
                letter-spacing: 0.1px;
            """)
            self.icon_label.setStyleSheet("""
                QLabel {
                    color: rgba(255, 255, 255, 0.7);
                    font-size: 16px;
                    font-weight: 600;
                    border-radius: 14px;
                    background: qlineargradient(x1: 0, y1: 0, x2: 1, y2: 1,
                                              stop: 0 rgba(255, 255, 255, 0.08),
                                              stop: 1 rgba(255, 255, 255, 0.04));
                    border: 1px solid rgba(255, 255, 255, 0.05);
                }
            """)

class CryptoDonationWidget(QWidget):
    """Widget for displaying crypto donation addresses"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setup_ui()
    
    def setup_ui(self):
        self.setStyleSheet("""
            CryptoDonationWidget {
                background: qlineargradient(x1: 0, y1: 0, x2: 0, y2: 1,
                                          stop: 0 transparent,
                                          stop: 0.3 rgba(255, 255, 255, 0.02),
                                          stop: 1 rgba(255, 255, 255, 0.04)); 
                border-top: 1px solid rgba(255, 255, 255, 0.08);
                border-bottom-right-radius: 12px;
            }
        """)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 15, 0, 15)
        layout.setSpacing(8)
        
        # Donation title
        donation_title = QLabel("Support Development")
        donation_title.setFont(QFont("SF Pro Text", 10, QFont.Weight.Bold))
        donation_title.setMinimumHeight(16)
        donation_title.setStyleSheet("""
            color: rgba(255, 255, 255, 0.9); 
            padding: 0 20px; 
            margin-bottom: 5px;
            letter-spacing: 0.2px;
            font-weight: 600;
        """)
        layout.addWidget(donation_title)
        
        # Crypto addresses
        crypto_addresses = [
            ("BTC", "Bitcoin", "3JVWrRSkozAQSmw5DXYVxYKsM9bndPTqdS"),
            ("ETH", "Ethereum", "0x343fC48c2cd1C6332b0df9a58F86e6520a026AC5")
        ]
        
        for symbol, name, address in crypto_addresses:
            crypto_item = self.create_crypto_item(symbol, name, address)
            layout.addWidget(crypto_item)
    
    def create_crypto_item(self, symbol: str, name: str, address: str):
        """Create a clickable crypto donation item"""
        item = QFrame()
        item.setFixedHeight(32)
        item.setCursor(Qt.CursorShape.PointingHandCursor)
        item.setStyleSheet("""
            QFrame {
                background: transparent;
                border-radius: 8px;
                margin: 0 12px;
            }
            QFrame:hover {
                background: rgba(255, 255, 255, 0.06);
                border: 1px solid rgba(255, 255, 255, 0.1);
            }
        """)
        
        layout = QHBoxLayout(item)
        layout.setContentsMargins(12, 4, 12, 4)
        layout.setSpacing(6)
        
        # Crypto name
        name_label = QLabel(name)
        name_label.setFont(QFont("SF Pro Text", 9, QFont.Weight.Medium))
        name_label.setStyleSheet("""
            color: rgba(255, 255, 255, 0.8);
            font-weight: 500;
        """)
        
        # Address (truncated)
        address_short = f"{address[:6]}...{address[-4:]}"
        address_label = QLabel(address_short)
        address_label.setFont(QFont("SF Pro Text", 8))
        address_label.setStyleSheet("""
            color: rgba(255, 255, 255, 0.5);
            font-family: 'Courier New', monospace;
        """)
        
        layout.addWidget(name_label)
        layout.addStretch()
        layout.addWidget(address_label)
        
        # Store full address for copying
        item.full_address = address
        item.crypto_name = name
        item.mousePressEvent = lambda event: self.copy_address(address, name)
        
        return item
    
    def copy_address(self, address: str, crypto_name: str):
        """Copy crypto address to clipboard"""
        clipboard = QApplication.clipboard()
        clipboard.setText(address)
        
        # Brief visual feedback (could add a tooltip or status message here)
        print(f"Copied {crypto_name} address to clipboard: {address}")

class StatusIndicator(QWidget):
    def __init__(self, service_name: str, parent=None):
        super().__init__(parent)
        self.service_name = service_name
        self.is_connected = False
        self.setup_ui()
    
    def setup_ui(self):
        self.setFixedHeight(38)  # Slightly taller for better proportions
        layout = QHBoxLayout(self)
        layout.setContentsMargins(20, 8, 20, 8)
        layout.setSpacing(14)
        
        # Status dot with more elegant design
        self.status_dot = QLabel("●")
        self.status_dot.setFixedSize(18, 18)
        self.status_dot.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_dot.setStyleSheet("""
            QLabel {
                border-radius: 9px;
                font-size: 10px;
                font-weight: 700;
                border: 1px solid rgba(255, 255, 255, 0.1);
            }
        """)
        
        # Service name with better typography
        self.service_label = QLabel(self.service_name)
        self.service_label.setFont(QFont("SF Pro Text", 10, QFont.Weight.Medium))
        self.service_label.setMinimumWidth(85)
        
        layout.addWidget(self.status_dot)
        layout.addWidget(self.service_label)
        layout.addStretch()
        
        self.update_status(False)
    
    def update_status(self, connected: bool):
        self.is_connected = connected
        if connected:
            self.status_dot.setStyleSheet("""
                QLabel {
                    color: #1ed760;
                    background: qlineargradient(x1: 0, y1: 0, x2: 1, y2: 1,
                                              stop: 0 rgba(29, 185, 84, 0.2),
                                              stop: 1 rgba(30, 215, 96, 0.15));
                    border-radius: 9px;
                    font-size: 10px;
                    font-weight: 700;
                    border: 1px solid rgba(29, 185, 84, 0.3);
                }
            """)
            self.service_label.setStyleSheet("""
                color: rgba(255, 255, 255, 0.95); 
                font-weight: 500;
                letter-spacing: 0.1px;
            """)
        else:
            self.status_dot.setStyleSheet("""
                QLabel {
                    color: #ff6b6b;
                    background: qlineargradient(x1: 0, y1: 0, x2: 1, y2: 1,
                                              stop: 0 rgba(255, 107, 107, 0.15),
                                              stop: 1 rgba(255, 107, 107, 0.1));
                    border-radius: 9px;
                    font-size: 10px;
                    font-weight: 700;
                    border: 1px solid rgba(255, 107, 107, 0.2);
                }
            """)
            self.service_label.setStyleSheet("""
                color: rgba(255, 255, 255, 0.5); 
                font-weight: 400;
                letter-spacing: 0.1px;
            """)

class LoadingAnimation(QWidget):
    """Thin horizontal loading animation for media player with dual-mode capability"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(12)  # Increased height for text overlay
        self._progress = 0.0
        self._is_active = False
        self._mode = "indefinite"  # "indefinite" or "determinate"
        self._determinate_progress = 0.0  # 0-100% for determinate mode
        
        # Animation setup for indefinite mode
        self.animation = QPropertyAnimation(self, b"progress")
        self.animation.setDuration(1200)  # 1.2 second cycle
        self.animation.setStartValue(0.0)
        self.animation.setEndValue(1.0)
        self.animation.setLoopCount(-1)  # Infinite loop
        self.animation.setEasingCurve(QEasingCurve.Type.InOutSine)
        
        # Progress value animation for smooth transitions in determinate mode
        self.progress_animation = QPropertyAnimation(self, b"determinate_progress")
        self.progress_animation.setDuration(300)  # Smooth 300ms transitions
        self.progress_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        
        # Completion glow effect
        self._glow_opacity = 0.0
        self.glow_animation = QPropertyAnimation(self, b"glow_opacity")
        self.glow_animation.setDuration(800)  # Slower glow pulse
        self.glow_animation.setStartValue(0.0)
        self.glow_animation.setEndValue(1.0)
        self.glow_animation.setLoopCount(3)  # Pulse 3 times
        self.glow_animation.setEasingCurve(QEasingCurve.Type.InOutSine)
        
        self.hide()  # Start hidden
    
    @pyqtProperty(float)
    def progress(self):
        return self._progress
    
    @progress.setter
    def progress(self, value):
        self._progress = value
        self.update()
    
    @pyqtProperty(float)
    def determinate_progress(self):
        return self._determinate_progress
    
    @determinate_progress.setter
    def determinate_progress(self, value):
        self._determinate_progress = value
        self.update()
    
    @pyqtProperty(float)
    def glow_opacity(self):
        return self._glow_opacity
    
    @glow_opacity.setter
    def glow_opacity(self, value):
        self._glow_opacity = value
        self.update()
    
    def start_animation(self):
        """Start the indefinite loading animation"""
        self._is_active = True
        self._mode = "indefinite"
        self.show()
        self.animation.start()
    
    def set_progress(self, percentage):
        """Set determinate progress (0-100%) with smooth animation"""
        if not self._is_active:
            self._is_active = True
            self.show()
        
        # Switch to determinate mode
        if self._mode == "indefinite":
            self._mode = "determinate"
            self.animation.stop()  # Stop indefinite animation
        
        # Animate to new progress value
        self.progress_animation.setStartValue(self._determinate_progress)
        self.progress_animation.setEndValue(percentage)
        self.progress_animation.start()
        
        # Trigger completion glow effect when reaching 100%
        if percentage >= 100 and self._determinate_progress < 100:
            self.glow_animation.start()
    
    def stop_animation(self):
        """Stop the loading animation"""
        self._is_active = False
        self._mode = "indefinite"
        self.animation.stop()
        self.progress_animation.stop()
        self.glow_animation.stop()
        self.hide()
        self._progress = 0.0
        self._determinate_progress = 0.0
        self._glow_opacity = 0.0
        self.update()
    
    def paintEvent(self, event):
        """Custom paint event for dual-mode animation with text overlay"""
        if not self._is_active:
            return
            
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        
        width = self.width()
        height = self.height()
        progress_bar_height = 4  # Bottom 4px for progress bar
        text_height = height - progress_bar_height  # Top area for text
        
        # Background for progress bar area
        progress_rect = self.rect()
        progress_rect.setTop(text_height)
        painter.fillRect(progress_rect, QColor(40, 40, 40))
        
        if self._mode == "indefinite":
            # Indefinite mode: animated gradient wave
            gradient_width = width * 0.3  # 30% of total width
            center_x = self._progress * width
            
            for i in range(int(gradient_width)):
                alpha = max(0, 255 - (abs(i - gradient_width/2) * 8))
                color = QColor(29, 185, 84, int(alpha))  # Spotify green with fade
                x = int(center_x - gradient_width/2 + i)
                if 0 <= x < width:
                    painter.fillRect(x, text_height, 1, progress_bar_height, color)
        
        else:  # determinate mode
            # Determinate mode: progress bar with percentage
            progress_width = (self._determinate_progress / 100.0) * width
            
            # Progress bar with gradient
            if progress_width > 0:
                progress_fill_rect = QRect(0, text_height, int(progress_width), progress_bar_height)
                
                # Create subtle gradient for progress bar
                gradient = QLinearGradient(0, text_height, progress_width, text_height)
                gradient.setColorAt(0, QColor(29, 185, 84))  # Spotify green
                gradient.setColorAt(1, QColor(30, 215, 96))  # Lighter green
                
                painter.fillRect(progress_fill_rect, gradient)
                
                # Add animated glow effect during completion
                if self._glow_opacity > 0:
                    glow_alpha = int(120 * self._glow_opacity)  # Max alpha of 120
                    glow_color = QColor(29, 185, 84, glow_alpha)
                    
                    # Expand glow slightly beyond progress bar for effect
                    glow_rect = QRect(0, text_height - 1, width, progress_bar_height + 2)
                    painter.fillRect(glow_rect, glow_color)
            
            # Percentage text overlay (elegant, small font)
            if text_height > 0 and self._determinate_progress > 0:
                font = QFont("Segoe UI", 7, QFont.Weight.Medium)  # Small, elegant font
                painter.setFont(font)
                painter.setPen(QColor(180, 180, 180))  # Light gray text
                
                percentage_text = f"{int(self._determinate_progress)}%"
                text_rect = QRect(0, 0, width, text_height)
                painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, percentage_text)

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
        self.setFixedHeight(85)  # More space for better proportions
        self.setStyleSheet("""
            MediaPlayer {
                background: qlineargradient(x1: 0, y1: 0, x2: 0, y2: 1,
                                          stop: 0 rgba(26, 26, 26, 0.95),
                                          stop: 0.5 rgba(18, 18, 18, 0.98),
                                          stop: 1 rgba(12, 12, 12, 1.0));
                border: 1px solid rgba(255, 255, 255, 0.05);
                border-radius: 12px;
                margin: 8px 10px;
            }
            MediaPlayer:hover {
                border: 1px solid rgba(29, 185, 84, 0.2);
                background: qlineargradient(x1: 0, y1: 0, x2: 0, y2: 1,
                                          stop: 0 rgba(29, 185, 84, 0.08),
                                          stop: 0.5 rgba(26, 26, 26, 0.95),
                                          stop: 1 rgba(18, 18, 18, 1.0));
            }
        """)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 12, 18, 12)
        layout.setSpacing(12)
        
        # Loading animation at the top
        self.loading_animation = LoadingAnimation()
        layout.addWidget(self.loading_animation)
        
        # Always visible header with basic controls
        self.header = self.create_header()
        layout.addWidget(self.header)
        
        # Expandable content (hidden when collapsed)
        self.expanded_content = self.create_expanded_content()
        self.expanded_content.setVisible(False)
        layout.addWidget(self.expanded_content)
        
        # No track message (shown when no music)
        self.no_track_label = QLabel("Start playing music to see controls")
        self.no_track_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.no_track_label.setStyleSheet("""
            QLabel {
                color: #6a6a6a;
                font-size: 12px;
                font-weight: 400;
                padding: 20px 16px;
                background: transparent;
                letter-spacing: 0.2px;
                font-family: 'Spotify Circular', -apple-system, sans-serif;
                line-height: 1.4;
            }
        """)
        layout.addWidget(self.no_track_label)
    
    def create_header(self):
        header = QWidget()
        main_layout = QVBoxLayout(header)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(8)
        
        # Top row: Track info and play button
        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(14)
        
        # Track info (expandable on click) - now with scrolling for long titles
        self.track_info = ScrollingLabel("No track")
        self.track_info.setStyleSheet("""
            ScrollingLabel {
                color: #ffffff;
                font-size: 14px;
                font-weight: 700;
                background: transparent;
                font-family: 'Spotify Circular', 'SF Pro Display', -apple-system, BlinkMacSystemFont, sans-serif;
                letter-spacing: -0.3px;
                padding: 2px 0px;
                line-height: 1.2;
            }
            ScrollingLabel:hover {
                color: #1ed760;
                text-decoration: underline;
            }
        """)
        self.track_info.setCursor(Qt.CursorShape.PointingHandCursor)
        self.track_info.mousePressEvent = self.toggle_expansion
        
        # Play/pause button - more Spotify-like
        self.play_pause_btn = QPushButton("▷")
        self.play_pause_btn.setFixedSize(40, 40)
        self.play_pause_btn.setStyleSheet("""
            QPushButton {
                background: #1ed760;
                border: none;
                border-radius: 20px;
                color: #000000;
                font-size: 16px;
                font-weight: 900;
                font-family: 'Arial', sans-serif;
            }
            QPushButton:hover {
                background: #1fdf64;
            }
            QPushButton:pressed {
                background: #1ca851;
            }
            QPushButton:disabled {
                background: #535353;
                color: #b3b3b3;
            }
        """)
        self.play_pause_btn.clicked.connect(self.on_play_pause_clicked)
        self.play_pause_btn.setEnabled(False)
        
        top_row.addWidget(self.track_info)
        top_row.addStretch()
        top_row.addWidget(self.play_pause_btn)
        
        # Bottom row: Artist info (always visible in collapsed mode)
        self.artist_info = QLabel("Unknown Artist")
        self.artist_info.setStyleSheet("""
            QLabel {
                color: #b3b3b3;
                font-size: 11px;
                font-weight: 400;
                background: transparent;
                font-family: 'Spotify Circular', -apple-system, BlinkMacSystemFont, sans-serif;
                letter-spacing: 0.1px;
                margin-top: 1px;
            }
        """)
        
        main_layout.addLayout(top_row)
        main_layout.addWidget(self.artist_info)
        
        return header
    
    def create_expanded_content(self):
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 2, 0, 0)
        layout.setSpacing(4)
        
        # Album info 
        self.album_label = QLabel("Unknown Album")
        self.album_label.setStyleSheet("""
            QLabel {
                color: #a7a7a7;
                font-size: 11px;
                font-weight: 400;
                background: transparent;
                font-family: 'Spotify Circular', -apple-system, BlinkMacSystemFont, sans-serif;
                letter-spacing: 0.1px;
            }
        """)
        layout.addWidget(self.album_label)
        
        # Control buttons - more Spotify-like
        controls_layout = QHBoxLayout()
        controls_layout.setContentsMargins(0, 1, 0, 0)
        controls_layout.setSpacing(6)
        
        # Volume control (Spotify style - more prominent)
        volume_layout = QHBoxLayout()
        volume_layout.setSpacing(10)
        
        volume_icon = QLabel("🔊")
        volume_icon.setStyleSheet("""
            QLabel {
                color: #b3b3b3;
                font-size: 13px;
                font-weight: 400;
                padding: 0px;
            }
        """)
        
        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(70)  # Default 70% volume
        self.volume_slider.setFixedWidth(80)
        self.volume_slider.setFixedHeight(20)
        self.volume_slider.setStyleSheet("""
            QSlider::groove:horizontal {
                border: none;
                height: 3px;
                background: #4f4f4f;
                border-radius: 1px;
            }
            QSlider::handle:horizontal {
                background: #ffffff;
                border: none;
                width: 12px;
                height: 12px;
                border-radius: 6px;
                margin: -4px 0;
            }
            QSlider::handle:horizontal:hover {
                background: #1ed760;
            }
            QSlider::sub-page:horizontal {
                background: #1ed760;
                border-radius: 1px;
            }
        """)
        self.volume_slider.valueChanged.connect(self.on_volume_changed)
        
        # Stop button - more visible Spotify style
        self.stop_btn = QPushButton("⏹")
        self.stop_btn.setFixedSize(32, 32)
        self.stop_btn.setStyleSheet("""
            QPushButton {
                background: rgba(255, 255, 255, 0.08);
                border: 1px solid #b3b3b3;
                border-radius: 16px;
                color: #ffffff;
                font-size: 12px;
                font-weight: 500;
            }
            QPushButton:hover {
                background: rgba(255, 255, 255, 0.15);
                border: 1px solid #ffffff;
                color: #ffffff;
            }
            QPushButton:pressed {
                background: rgba(255, 255, 255, 0.25);
            }
            QPushButton:disabled {
                background: transparent;
                border: 1px solid #2a2a2a;
                color: #535353;
            }
        """)
        self.stop_btn.clicked.connect(self.on_stop_clicked)
        self.stop_btn.setEnabled(False)
        
        volume_layout.addWidget(volume_icon)
        volume_layout.addWidget(self.volume_slider)
        
        controls_layout.addLayout(volume_layout)
        controls_layout.addStretch()
        controls_layout.addWidget(self.stop_btn)
        
        layout.addLayout(controls_layout)
        
        return content
    
    def toggle_expansion(self, event=None):
        """Toggle between collapsed and expanded view"""
        if not self.current_track:
            return
            
        self.is_expanded = not self.is_expanded
        
        if self.is_expanded:
            self.setFixedHeight(145)  # More space for the new layout
            self.expanded_content.setVisible(True)
            self.no_track_label.setVisible(False)
        else:
            self.setFixedHeight(85)  # Match the updated collapsed height
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
        
        # Update artist and album info
        artist = getattr(track_result, 'artist', 'Unknown Artist')
        album = getattr(track_result, 'album', 'Unknown Album')
        
        # Update the separate artist and album labels
        self.artist_info.setText(artist)
        self.album_label.setText(album)
        
        # Enable controls
        self.play_pause_btn.setEnabled(True)
        self.stop_btn.setEnabled(True)
        
        # Set to playing state (show pause button since track just started)
        self.set_playing_state(True)
        
        # Hide loading animation now that track is ready
        self.hide_loading()
        
        # Hide no track message and show player
        self.no_track_label.setVisible(False)
        
        # Auto-expand when new track starts
        if not self.is_expanded:
            self.toggle_expansion()
    
    def set_playing_state(self, playing):
        """Update play/pause button state"""
        self.is_playing = playing
        if playing:
            self.play_pause_btn.setText("⏸︎")
            # Start scrolling animation when playing
            if self.track_info.should_scroll and not self.track_info.is_scrolling:
                self.track_info.start_scroll_animation()
        else:
            self.play_pause_btn.setText("▷")
            # Optionally stop scrolling when paused (can be customized)
            # self.track_info.stop_scrolling()
    
    def clear_track(self):
        """Clear current track and reset to no track state"""
        self.current_track = None
        self.is_playing = False
        
        # Stop any animations
        self.track_info.stop_scrolling()
        self.hide_loading()
        
        # Update UI
        self.track_info.setText("No track")
        self.artist_info.setText("Unknown Artist")
        self.album_label.setText("Unknown Album")
        self.play_pause_btn.setText("▷")
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
    
    def show_loading(self):
        """Show and start the loading animation"""
        self.loading_animation.start_animation()
    
    def hide_loading(self):
        """Hide and stop the loading animation"""
        self.loading_animation.stop_animation()
    
    def set_loading_progress(self, percentage):
        """Set loading progress percentage (0-100)"""
        self.loading_animation.set_progress(percentage)
    

class ModernSidebar(QWidget):
    page_changed = pyqtSignal(str)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_page = "dashboard"
        self.buttons = {}
        self.setup_ui()
    
    def setup_ui(self):
        self.setFixedWidth(240)  # Slightly wider for better proportions
        self.setStyleSheet("""
            ModernSidebar {
                background: qlineargradient(x1: 0, y1: 0, x2: 1, y2: 1,
                                          stop: 0 #0d1117,
                                          stop: 0.3 #121212,
                                          stop: 1 #0a0a0a);
                border-right: 1px solid rgba(29, 185, 84, 0.1);
                border-top-right-radius: 12px;
                border-bottom-right-radius: 12px;
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
        
        # Small spacer between media player and crypto
        layout.addItem(QSpacerItem(20, 8, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed))
        
        # Crypto Donation section
        crypto_section = CryptoDonationWidget()
        layout.addWidget(crypto_section)
        
        # Small spacer between crypto and status
        layout.addItem(QSpacerItem(20, 8, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed))
        
        # Status section
        status_section = self.create_status_section()
        layout.addWidget(status_section)
    
    def create_header(self):
        header = QWidget()
        header.setFixedHeight(95)
        header.setStyleSheet("""
            QWidget {
                background: qlineargradient(x1: 0, y1: 0, x2: 0, y2: 1,
                                          stop: 0 rgba(29, 185, 84, 0.08),
                                          stop: 0.4 rgba(29, 185, 84, 0.03),
                                          stop: 1 transparent);
                border-bottom: 1px solid rgba(29, 185, 84, 0.15);
                border-top-right-radius: 12px;
            }
        """)
        
        layout = QVBoxLayout(header)
        layout.setContentsMargins(24, 24, 24, 20)
        layout.setSpacing(4)
        
        # App name with gradient text effect
        app_name = QLabel("SoulSync")
        app_name.setFont(QFont("SF Pro Display", 20, QFont.Weight.Bold))
        app_name.setStyleSheet("""
            color: qlineargradient(x1: 0, y1: 0, x2: 1, y2: 0,
                                 stop: 0 #ffffff,
                                 stop: 0.6 #1ed760,
                                 stop: 1 #1db954);
            letter-spacing: -0.8px;
            font-weight: 700;
        """)
        
        # Subtitle with better typography
        subtitle = QLabel("Music Sync & Manager")
        subtitle.setFont(QFont("SF Pro Text", 10, QFont.Weight.Medium))
        subtitle.setStyleSheet("""
            color: rgba(255, 255, 255, 0.65);
            letter-spacing: 0.2px;
            font-weight: 500;
            margin-top: 2px;
        """)
        
        layout.addWidget(app_name)
        layout.addWidget(subtitle)
        
        return header
    
    def create_navigation(self):
        nav_widget = QWidget()
        nav_widget.setStyleSheet("""
            QWidget {
                background: transparent;
                border-radius: 12px;
            }
        """)
        layout = QVBoxLayout(nav_widget)
        layout.setContentsMargins(12, 25, 12, 25)
        layout.setSpacing(8)
        
        # Navigation buttons
        nav_items = [
            ("dashboard", "Dashboard", "📊"),
            ("sync", "Sync", "🔄"),
            ("downloads", "Search", "📥"),
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
        status_widget.setFixedHeight(150)  # Slightly taller for better proportions
        status_widget.setStyleSheet("""
            QWidget {
                background: qlineargradient(x1: 0, y1: 0, x2: 0, y2: 1,
                                          stop: 0 transparent,
                                          stop: 0.3 rgba(255, 255, 255, 0.02),
                                          stop: 1 rgba(255, 255, 255, 0.04)); 
                border-top: 1px solid rgba(255, 255, 255, 0.08);
                border-bottom-right-radius: 12px;
            }
        """)
        
        layout = QVBoxLayout(status_widget)
        layout.setContentsMargins(0, 20, 0, 20)
        layout.setSpacing(8)
        
        # Status title with better typography
        status_title = QLabel("Service Status")
        status_title.setFont(QFont("SF Pro Text", 11, QFont.Weight.Bold))
        status_title.setStyleSheet("""
            color: rgba(255, 255, 255, 0.9); 
            padding: 0 20px; 
            margin-bottom: 8px;
            letter-spacing: 0.2px;
            font-weight: 600;
        """)
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