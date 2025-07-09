from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton, 
                           QLabel, QFrame, QSizePolicy, QSpacerItem)
from PyQt6.QtCore import Qt, pyqtSignal, QPropertyAnimation, QEasingCurve, QRect
from PyQt6.QtGui import QFont, QPalette, QIcon, QPixmap, QPainter

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
                    background: rgba(29, 185, 84, 0.2);
                    border-left: 3px solid #1db954;
                    border-radius: 0px;
                    text-align: left;
                    padding: 0px;
                }
                SidebarButton:hover {
                    background: rgba(29, 185, 84, 0.3);
                }
            """)
            self.text_label.setStyleSheet("color: #1db954; font-weight: bold;")
            self.icon_label.setStyleSheet("""
                QLabel {
                    color: #1db954;
                    font-size: 16px;
                    font-weight: bold;
                    border-radius: 12px;
                    background: rgba(29, 185, 84, 0.2);
                }
            """)
        else:
            self.setStyleSheet("""
                SidebarButton {
                    background: transparent;
                    border: none;
                    border-radius: 0px;
                    text-align: left;
                    padding: 0px;
                }
                SidebarButton:hover {
                    background: rgba(255, 255, 255, 0.1);
                }
            """)
            self.text_label.setStyleSheet("color: #b3b3b3;")
            self.icon_label.setStyleSheet("""
                QLabel {
                    color: #b3b3b3;
                    font-size: 16px;
                    font-weight: bold;
                    border-radius: 12px;
                    background: rgba(255, 255, 255, 0.1);
                }
            """)

class StatusIndicator(QWidget):
    def __init__(self, service_name: str, parent=None):
        super().__init__(parent)
        self.service_name = service_name
        self.is_connected = False
        self.setup_ui()
    
    def setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(15, 5, 15, 5)
        layout.setSpacing(10)
        
        # Status dot
        self.status_dot = QLabel("●")
        self.status_dot.setFixedSize(12, 12)
        self.status_dot.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        # Service name
        self.service_label = QLabel(self.service_name)
        self.service_label.setFont(QFont("Arial", 9))
        
        layout.addWidget(self.status_dot)
        layout.addWidget(self.service_label)
        layout.addStretch()
        
        self.update_status(False)
    
    def update_status(self, connected: bool):
        self.is_connected = connected
        if connected:
            self.status_dot.setStyleSheet("color: #1db954;")
            self.service_label.setStyleSheet("color: #ffffff;")
        else:
            self.status_dot.setStyleSheet("color: #e22134;")
            self.service_label.setStyleSheet("color: #b3b3b3;")

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
        
        # Status section
        status_section = self.create_status_section()
        layout.addWidget(status_section)
    
    def create_header(self):
        header = QWidget()
        header.setFixedHeight(80)
        header.setStyleSheet("background: #121212; border-bottom: 1px solid #282828;")
        
        layout = QVBoxLayout(header)
        layout.setContentsMargins(20, 20, 20, 20)
        
        # App name
        app_name = QLabel("NewMusic")
        app_name.setFont(QFont("Arial", 18, QFont.Weight.Bold))
        app_name.setStyleSheet("color: #ffffff;")
        
        # Subtitle
        subtitle = QLabel("Music Sync & Manager")
        subtitle.setFont(QFont("Arial", 9))
        subtitle.setStyleSheet("color: #b3b3b3;")
        
        layout.addWidget(app_name)
        layout.addWidget(subtitle)
        
        return header
    
    def create_navigation(self):
        nav_widget = QWidget()
        layout = QVBoxLayout(nav_widget)
        layout.setContentsMargins(0, 20, 0, 20)
        layout.setSpacing(5)
        
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
        status_widget.setFixedHeight(120)
        status_widget.setStyleSheet("background: #181818; border-top: 1px solid #282828;")
        
        layout = QVBoxLayout(status_widget)
        layout.setContentsMargins(0, 15, 0, 15)
        layout.setSpacing(8)
        
        # Status title
        status_title = QLabel("Connection Status")
        status_title.setFont(QFont("Arial", 10, QFont.Weight.Bold))
        status_title.setStyleSheet("color: #ffffff; padding: 0 15px;")
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