import sys
import os.path
import datetime
import pickle
import warnings
import winsound
import threading
import ctypes
warnings.filterwarnings("ignore", category=DeprecationWarning)

from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                            QLabel, QPushButton, QScrollArea, QFrame, QMessageBox)
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QFont, QPalette, QColor, QIcon
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from contextlib import contextmanager

@contextmanager
def suppress_qt_warnings():
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=DeprecationWarning, module='PyQt5.sip')
        yield

SCOPES = ['https://www.googleapis.com/auth/calendar.readonly']

with suppress_qt_warnings():
    class EventCard(QFrame):
        def __init__(self, event, parent=None):
            super().__init__(parent)
            self.setFrameStyle(QFrame.Shape.Box | QFrame.Shadow.Raised)
            self.setStyleSheet("""
                QFrame {
                    background-color: white;
                    border-radius: 3px;
                    margin: 1px;
                    padding: 4px;
                }
            """)
            
            layout = QVBoxLayout()
            layout.setSpacing(2)  # Reduce spacing between elements
            layout.setContentsMargins(4, 4, 4, 4)  # Reduce margins
            
            # Event title
            title = QLabel(event['summary'])
            title.setFont(QFont('Arial', 9, QFont.Weight.Bold))
            title.setWordWrap(True)
            layout.addWidget(title)
            
            # Event time
            start = event['start'].get('dateTime', event['start'].get('date'))
            end = event['end'].get('dateTime', event['end'].get('date'))
            
            if 'T' in start:  # This is a timed event
                start_dt = datetime.datetime.fromisoformat(start)
                end_dt = datetime.datetime.fromisoformat(end)
                time_str = f"{start_dt.strftime('%I:%M %p')} - {end_dt.strftime('%I:%M %p')}"
                if start_dt.date() != datetime.date.today():
                    time_str = f"{start_dt.strftime('%B %d, %Y')} {time_str}"
            else:  # All-day event
                start_dt = datetime.date.fromisoformat(start)
                time_str = f"All day - {start_dt.strftime('%B %d, %Y')}"
                
            time_label = QLabel(time_str)
            time_label.setFont(QFont('Arial', 8))
            layout.addWidget(time_label)
            
            # Location (if available)
            if 'location' in event:
                location = QLabel(event['location'])
                location.setFont(QFont('Arial', 8))
                location.setStyleSheet('color: gray; font-style: italic;')
                location.setWordWrap(True)
                layout.addWidget(location)
                
            self.setLayout(layout)

    class EventNotifier:
        def __init__(self):
            self.notified_events = {}  # Track notifications by event ID and reminder time
            self.timer = None
            self.events = []
            self.check_interval = 30  # Check every 30 seconds
            self.main_window = None  # Store reference to main window for popup
            self.reminder_times = [30, 3]  # Reminder times in minutes

        def set_main_window(self, window):
            self.main_window = window

        def start(self):
            self.timer = threading.Timer(self.check_interval, self._check_events)
            self.timer.daemon = True
            self.timer.start()

        def stop(self):
            if self.timer:
                self.timer.cancel()

        def update_events(self, events):
            self.events = events
            # Reset notification status when events are updated
            self.notified_events = {}

        def _show_notification(self, event, minutes_until):
            # Play notification sound
            winsound.PlaySound("SystemExclamation", winsound.SND_ASYNC)
            
            if self.main_window:
                # Format the event time
                start = event.get('start', {}).get('dateTime')
                start_time = datetime.datetime.fromisoformat(start.replace('Z', '+00:00')).strftime("%I:%M %p")
                
                # Create message with location if available
                location = event.get('location', '')
                location_text = f"\nLocation: {location}" if location else ""
                
                # Create reminder message based on time until event
                if minutes_until == 30:
                    time_msg = "30 minutes"
                elif minutes_until == 3:
                    time_msg = "3 minutes"
                else:
                    time_msg = f"{minutes_until} minutes"
                    
                message = f"Event starts in {time_msg}:\n\n{event['summary']}\nStarts at: {start_time}{location_text}"
                
                # Create popup dialog
                msg_box = QMessageBox(self.main_window)
                msg_box.setWindowTitle("Event Reminder")
                msg_box.setText(message)
                msg_box.setIcon(QMessageBox.Information)
                msg_box.setWindowFlags(msg_box.windowFlags() | Qt.WindowStaysOnTopHint)
                msg_box.setWindowIcon(QIcon("logo.png"))
                msg_box.exec_()

        def _check_events(self):
            now = datetime.datetime.now(datetime.timezone.utc)
            
            for event in self.events:
                start = event.get('start', {}).get('dateTime')
                if not start:  # Skip all-day events
                    continue

                event_id = event['id']
                if event_id not in self.notified_events:
                    self.notified_events[event_id] = set()

                start_time = datetime.datetime.fromisoformat(start.replace('Z', '+00:00'))
                time_until_event = start_time - now
                minutes_until = time_until_event.total_seconds() / 60

                # Check for each reminder time
                for reminder_time in self.reminder_times:
                    # If we haven't sent this reminder yet and it's time to send it
                    if (reminder_time not in self.notified_events[event_id] and
                        reminder_time - 0.5 <= minutes_until <= reminder_time + 0.5):
                        self._show_notification(event, reminder_time)
                        self.notified_events[event_id].add(reminder_time)

            # Schedule next check
            self.start()

    class CalendarWidget(QMainWindow):
        def __init__(self):
            super().__init__()
            self.setWindowTitle('Event Horizon')
            self.setWindowFlags(Qt.WindowStaysOnTopHint)
            
            # Initialize event notifier and pass window reference
            self.notifier = EventNotifier()
            self.notifier.set_main_window(self)  # Pass reference to main window
            
            # Set up the main widget and layout
            main_widget = QWidget()
            self.setCentralWidget(main_widget)
            layout = QVBoxLayout(main_widget)
            layout.setSpacing(2)
            layout.setContentsMargins(4, 4, 4, 4)
            
            # Add refresh button
            self.refresh_btn = QPushButton('Refresh')
            self.refresh_btn.clicked.connect(self.refresh_events)
            self.refresh_btn.setStyleSheet("""
                QPushButton {
                    background-color: #4285f4;
                    color: white;
                    border: none;
                    padding: 3px;
                    border-radius: 2px;
                    max-height: 24px;
                }
                QPushButton:hover {
                    background-color: #357abd;
                }
                QPushButton:disabled {
                    background-color: #cccccc;
                }
            """)
            layout.addWidget(self.refresh_btn)
            
            # Add status label
            self.status_label = QLabel()
            self.status_label.setStyleSheet("""
                QLabel {
                    color: #666666;
                    font-size: 8pt;
                    padding: 2px;
                }
            """)
            self.status_label.setAlignment(Qt.AlignCenter)
            layout.addWidget(self.status_label)
            
            # Create scroll area for events
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setStyleSheet("""
                QScrollArea {
                    border: none;
                    background-color: #f8f9fa;
                }
            """)
            
            self.events_widget = QWidget()
            self.events_layout = QVBoxLayout(self.events_widget)
            self.events_layout.setSpacing(2)  # Reduce spacing between events
            self.events_layout.setContentsMargins(2, 2, 2, 2)  # Reduce margins
            scroll.setWidget(self.events_widget)
            layout.addWidget(scroll)
            
            # Set up auto-refresh timer (15 minutes)
            self.timer = QTimer()
            self.timer.timeout.connect(self.refresh_events)
            self.timer.start(15 * 60 * 1000)  # 15 minutes in milliseconds
            
            # Set window size and style
            self.setMinimumSize(300, 400)
            self.setStyleSheet("""
                QMainWindow {
                    background-color: #f8f9fa;
                }
            """)
            
            # Initialize and load events
            self.creds = None
            self.refresh_events()

        def get_credentials(self):
            if os.path.exists('token.pickle'):
                with open('token.pickle', 'rb') as token:
                    self.creds = pickle.load(token)
            
            if not self.creds or not self.creds.valid:
                if self.creds and self.creds.expired and self.creds.refresh_token:
                    self.creds.refresh(Request())
                else:
                    flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
                    self.creds = flow.run_local_server(port=0)
                
                with open('token.pickle', 'wb') as token:
                    pickle.dump(self.creds, token)
        
        def refresh_events(self):
            try:
                # Disable refresh button and show status
                self.refresh_btn.setEnabled(False)
                self.refresh_btn.setText('Refreshing...')
                self.status_label.setText('Syncing with Google Calendar...')
                self.status_label.setStyleSheet('QLabel { color: #666666; }')
                QApplication.processEvents()  # Force UI update
                
                print("Getting credentials...")
                self.get_credentials()
                print("Credentials obtained successfully")
                
                # Clear existing events - Fixed widget removal
                while self.events_layout.count():
                    item = self.events_layout.takeAt(0)
                    if item.widget():
                        item.widget().deleteLater()
            
                print("Building calendar service...")
                service = build('calendar', 'v3', credentials=self.creds)
                print("Calendar service built successfully")
                
                now = datetime.datetime.utcnow().isoformat() + 'Z'
                print("Fetching events...")
                events_result = service.events().list(
                    calendarId='primary',
                    timeMin=now,
                    maxResults=10,
                    singleEvents=True,
                    orderBy='startTime'
                ).execute()
                print("Events fetched successfully")
                events = events_result.get('items', [])
                
                # Update notifier with new events
                self.notifier.update_events(events)
                
                if not events:
                    no_events = QLabel('No upcoming events')
                    no_events.setAlignment(Qt.AlignCenter)
                    self.events_layout.addWidget(no_events)
                else:
                    for event in events:
                        self.events_layout.addWidget(EventCard(event))
                        
                # Add stretch to push events to the top
                self.events_layout.addStretch()
                
                # Show success status
                current_time = datetime.datetime.now().strftime("%I:%M %p")
                self.status_label.setText(f'Last updated: {current_time}')
                self.status_label.setStyleSheet('QLabel { color: #28a745; }')  # Green color for success
            
            except Exception as e:
                error_msg = str(e)
                error_label = QLabel(f'Error: {error_msg}')
                error_label.setWordWrap(True)  # Allow error message to wrap
                error_label.setStyleSheet('color: red;')
                self.events_layout.addWidget(error_label)
                
                # Show detailed error status
                self.status_label.setText(f'Failed to refresh: {error_msg}')
                self.status_label.setStyleSheet('QLabel { color: #dc3545; }')  # Red color for error
        
            finally:
                # Re-enable refresh button
                self.refresh_btn.setEnabled(True)
                self.refresh_btn.setText('Refresh')

        def closeEvent(self, event):
            # Stop the notifier when the application closes
            self.notifier.stop()
            event.accept()

if __name__ == '__main__':
    # Set the app ID for Windows taskbar icon
    myappid = 'mycompany.eventhorizon.calendar.1'  # arbitrary string
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
    
    app = QApplication(sys.argv)
    
    # Set application icon
    app_icon = QIcon("logo.png")
    app.setWindowIcon(app_icon)
    
    # Set application-wide style
    app.setStyle('Fusion')
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor('#f8f9fa'))
    app.setPalette(palette)
    
    widget = CalendarWidget()
    widget.setWindowIcon(app_icon)
    widget.show()
    
    # Start the notification system
    widget.notifier.start()
    
    sys.exit(app.exec_())
