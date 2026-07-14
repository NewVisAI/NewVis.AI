import threading
import time
import sys
import os

def start_web_server():
    import uvicorn
    import socket
    # Append the backend folder path to sys.path if not present
    sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "backend"))
    from backend.server import app as fastapi_app
    
    # Scan for first free port starting from 8000
    target_port = 8000
    for port in range(8000, 8010):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.5)
        # On Windows, using SO_REUSEADDR allows quick port reclaiming
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("0.0.0.0", port))
            s.close()
            target_port = port
            break
        except OSError:
            continue
            
    print(f"[SERVER] Port verified. Launching Web Dashboard on port {target_port} ...", flush=True)
    # Run the server
    uvicorn.run(fastapi_app, host="0.0.0.0", port=target_port, log_level="warning")

def main():
    print("="*60)
    print("[START] SENTINEL AI CCTV - UNIFIED DEMO LAUNCHER")
    print("="*60)

    # 1. Start the FastAPI Web Dashboard Server in a background thread
    print("[SERVER] Starting Web Dashboard Server on http://localhost:8000 ...")
    server_thread = threading.Thread(target=start_web_server, daemon=True)
    server_thread.start()

    # Give the server a brief moment to bind to the port
    time.sleep(1.5)
    print("[SUCCESS] Web Server is running in the background.")
    print("Open http://localhost:8000 in your browser to access the Dashboard!")
    print("-"*60)
    print("[SURVEILLANCE] Starting Surveillance GUI / Search Engine...")
    print("="*60)

    # 2. Run the main surveillance loop in the main thread
    try:
        import app
        app.main()
    except KeyboardInterrupt:
        print("\n[STOP] Stopping demo...")
    except Exception as e:
        print(f"\n[ERROR] App error occurred: {e}")
        import traceback
        traceback.print_exc()
    finally:
        print("[FINISHED] Demo closed.")

if __name__ == "__main__":
    main()
