import threading
import time
import sys
import os

def start_web_server():
    import uvicorn
    # Append the backend folder path to sys.path if not present
    sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "backend"))
    from backend.server import app as fastapi_app
    
    # Run the server
    uvicorn.run(fastapi_app, host="0.0.0.0", port=8000, log_level="warning")

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
