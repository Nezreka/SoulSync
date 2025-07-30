from PyQt6.QtWidgets import QWidget, QLabel, QHBoxLayout, QVBoxLayout, QGraphicsOpacityEffect
from PyQt6.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve, pyqtSignal, QRect
from PyQt6.QtGui import QFont, QPainter, QPaintEvent
import time
from typing import List, Optional
from enum import Enum

class ToastType(Enum):
    SUCCESS = "success"
    INFO = "info" 
    WARNING = "warning"
    ERROR = "error"

class Toast(QWidget):
    """Individual toast notification widget"""
    closed = pyqtSignal(object)  # Emits self when closing
    
    def __init__(self, message: str, toast_type: ToastType = ToastType.INFO, duration: int = 4000, parent=None):
        super().__init__(parent)
        self.message = message
        self.toast_type = toast_type
        self.duration = duration
        self.created_time = time.time()
        
        self.setup_ui()
        self.setup_animations()
        self.setup_auto_dismiss()
        
    def setup_ui(self):
        """Setup the toast UI"""
        self.setFixedHeight(60)
        self.setMinimumWidth(300)
        self.setMaximumWidth(400)
        
        # Make the widget click-through for the background but clickable for the content
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        
        # Main layout
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)
        
        # Icon label
        self.icon_label = QLabel()
        self.icon_label.setFont(QFont("Segoe UI", 14))
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon_label.setFixedSize(24, 24)
        
        # Message label
        self.message_label = QLabel(self.message)
        self.message_label.setFont(QFont("Segoe UI", 10))
        self.message_label.setWordWrap(True)
        self.message_label.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        
        layout.addWidget(self.icon_label)
        layout.addWidget(self.message_label, 1)
        
        # Apply styling based on toast type
        self.apply_styling()
        
    def apply_styling(self):
        """Apply styling based on toast type"""
        if self.toast_type == ToastType.SUCCESS:
            icon = "✅"
            accent_color = "#1db954"  # Spotify green
            bg_color = "rgba(29, 185, 84, 0.15)"
            border_color = "rgba(29, 185, 84, 0.3)"
        elif self.toast_type == ToastType.ERROR:
            icon = "❌"
            accent_color = "#f04747"
            bg_color = "rgba(240, 71, 71, 0.15)"
            border_color = "rgba(240, 71, 71, 0.3)"
        elif self.toast_type == ToastType.WARNING:
            icon = "⚠️"
            accent_color = "#ffa500"
            bg_color = "rgba(255, 165, 0, 0.15)"
            border_color = "rgba(255, 165, 0, 0.3)"
        else:  # INFO
            icon = "ℹ️"
            accent_color = "#5865f2"
            bg_color = "rgba(88, 101, 242, 0.15)"
            border_color = "rgba(88, 101, 242, 0.3)"
            
        self.icon_label.setText(icon)
        
        self.setStyleSheet(f"""
            Toast {{
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(45, 45, 45, 0.95),
                    stop:1 rgba(35, 35, 35, 0.95));
                border: 1px solid {border_color};
                border-left: 3px solid {accent_color};
                border-radius: 8px;
            }}
        """)
        
        self.message_label.setStyleSheet(f"""
            color: #ffffff;
            background: transparent;
        """)
        
    def setup_animations(self):
        """Setup slide-in and fade-out animations"""
        # Opacity effect for fade animations
        self.opacity_effect = QGraphicsOpacityEffect()
        self.setGraphicsEffect(self.opacity_effect)
        
        # Slide-in animation (from right)
        self.slide_animation = QPropertyAnimation(self, b"geometry")
        self.slide_animation.setDuration(300)
        self.slide_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        
        # Fade-out animation
        self.fade_animation = QPropertyAnimation(self.opacity_effect, b"opacity")
        self.fade_animation.setDuration(200)
        self.fade_animation.setEasingCurve(QEasingCurve.Type.OutQuad)
        
        # Connect fade animation to close
        self.fade_animation.finished.connect(self._on_fade_complete)
        
    def setup_auto_dismiss(self):
        """Setup auto-dismiss timer"""
        if self.duration > 0:
            self.dismiss_timer = QTimer()
            self.dismiss_timer.setSingleShot(True)
            self.dismiss_timer.timeout.connect(self.dismiss)
            self.dismiss_timer.start(self.duration)
    
    def show_at_position(self, target_rect: QRect):
        """Show the toast with slide-in animation at the specified position"""
        # Start position (off-screen to the right)
        start_rect = QRect(target_rect.x() + 50, target_rect.y(), target_rect.width(), target_rect.height())
        
        # Set initial position and show
        self.setGeometry(start_rect)
        self.show()
        
        # Animate to target position
        self.slide_animation.setStartValue(start_rect)
        self.slide_animation.setEndValue(target_rect)
        self.slide_animation.start()
    
    def dismiss(self):
        """Dismiss the toast with fade-out animation"""
        if hasattr(self, 'dismiss_timer'):
            self.dismiss_timer.stop()
            
        self.fade_animation.setStartValue(1.0)
        self.fade_animation.setEndValue(0.0)
        self.fade_animation.start()
    
    def _on_fade_complete(self):
        """Called when fade animation completes"""
        self.closed.emit(self)
        self.hide()
        self.deleteLater()
    
    def mousePressEvent(self, event):
        """Handle click to dismiss"""
        if event.button() == Qt.MouseButton.LeftButton:
            self.dismiss()
        super().mousePressEvent(event)


class ToastManager(QWidget):
    """Manages multiple toast notifications"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent_widget = parent
        self.active_toasts: List[Toast] = []
        self.toast_spacing = 10
        self.margin_from_edge = 20
        
        # Make this widget transparent and non-interactive
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        
    def show_toast(self, message: str, toast_type: ToastType = ToastType.INFO, duration: int = 4000):
        """Show a new toast notification"""
        toast = Toast(message, toast_type, duration, self.parent_widget)
        toast.closed.connect(self._on_toast_closed)
        
        # Calculate position for this toast
        position = self._calculate_toast_position(len(self.active_toasts))
        
        # Add to active toasts list
        self.active_toasts.append(toast)
        
        # Show the toast
        toast.show_at_position(position)
        
        # Reposition existing toasts if needed
        self._reposition_existing_toasts()
        
    def _calculate_toast_position(self, index: int) -> QRect:
        """Calculate position for a toast at the given index"""
        if not self.parent_widget:
            return QRect(0, 0, 350, 60)
            
        parent_rect = self.parent_widget.rect()
        toast_height = 60
        toast_width = 350
        
        x = parent_rect.width() - toast_width - self.margin_from_edge
        y = self.margin_from_edge + (index * (toast_height + self.toast_spacing))
        
        return QRect(x, y, toast_width, toast_height)
    
    def _reposition_existing_toasts(self):
        """Reposition existing toasts to make room for new ones"""
        for i, toast in enumerate(self.active_toasts[:-1]):  # Exclude the newest toast
            new_position = self._calculate_toast_position(i)
            
            # Animate to new position if needed
            current_geo = toast.geometry()
            if current_geo.y() != new_position.y():
                toast.slide_animation.stop()
                toast.slide_animation.setStartValue(current_geo)
                toast.slide_animation.setEndValue(new_position)
                toast.slide_animation.start()
    
    def _on_toast_closed(self, toast: Toast):
        """Handle when a toast is closed"""
        if toast in self.active_toasts:
            self.active_toasts.remove(toast)
            
        # Reposition remaining toasts
        for i, remaining_toast in enumerate(self.active_toasts):
            new_position = self._calculate_toast_position(i)
            current_geo = remaining_toast.geometry()
            
            if current_geo != new_position:
                remaining_toast.slide_animation.stop()
                remaining_toast.slide_animation.setStartValue(current_geo)
                remaining_toast.slide_animation.setEndValue(new_position)
                remaining_toast.slide_animation.start()
    
    def clear_all_toasts(self):
        """Dismiss all active toasts"""
        for toast in self.active_toasts.copy():
            toast.dismiss()
    
    # Convenience methods for different toast types
    def success(self, message: str, duration: int = 4000):
        """Show a success toast"""
        self.show_toast(message, ToastType.SUCCESS, duration)
    
    def error(self, message: str, duration: int = 6000):
        """Show an error toast (longer duration)"""
        self.show_toast(message, ToastType.ERROR, duration)
    
    def warning(self, message: str, duration: int = 5000):
        """Show a warning toast"""
        self.show_toast(message, ToastType.WARNING, duration)
    
    def info(self, message: str, duration: int = 4000):
        """Show an info toast"""
        self.show_toast(message, ToastType.INFO, duration)